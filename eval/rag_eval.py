"""RAG evaluation: golden set -> retrieve -> answer from the excerpts -> RAGAS + decline rate.

RAG (retrieval-augmented generation) means the model answers using policy text that we
first search for, instead of from memory. The "golden set" is a file of questions with
known good answers. RAGAS is a library that scores RAG answers by asking a judge LLM.

Run: uv run python -m eval.rag_eval --label baseline
     uv run python -m eval.rag_eval --label section_aware --strategy section
     uv run python -m eval.rag_eval --label smoke --limit 3        (quick check)
     uv run python -m eval.rag_eval --label recheck --only SDG03,G10   (chosen questions)
It calls paid APIs. It writes eval/results/rag_<label>.json and prints the metrics.

Metrics (RAGAS 0.4.3, judge model JUDGE_MODEL, default gpt-4o-mini):
  - faithfulness: is every claim in the answer supported by the retrieved text?
  - answer relevancy: does the answer actually address the question?
  - context precision: are the useful chunks ranked near the top of the retrieved ones?
  - context recall: did retrieval find everything needed to write the reference answer?
They are computed on the 20 answerable questions. The 5 unanswerable ones are scored by
the "correctly declined" rate instead (the model should say "I cannot answer").
Also reported: retrieval hit rate (are the reference policies among the retrieved chunks?) and
the safety invariant (no citation to a chunk that was not retrieved).
"""

import argparse
import time
import warnings

from pydantic import BaseModel, Field

from app import config
from app.agent.llm import OpenAILLM
from app.rag import ingest as ingest_mod
from app.rag.retriever import Retriever
from eval.common import (
    JUDGE_MODEL,
    UsageHandler,
    environment,
    estimate_cost,
    load_jsonl,
    mean,
    save_result,
)


class RagAnswer(BaseModel):
    """The structured answer we ask the model to return for one question."""

    answer: str
    answerable: bool = Field(description="False if the excerpts do not answer the question")
    cited_chunk_ids: list[str]


def _excerpts(chunks: list[dict]) -> str:
    """Format retrieved chunks as one text block for the prompt.

    Args:
        chunks: Retrieved chunk dicts with chunk_id, doc_id, section and text.

    Returns:
        The chunks joined by blank lines, each with a header showing its id.
    """
    return "\n\n".join(
        f"[chunk_id: {c['chunk_id']}] ({c['doc_id']} {c['section']})\n{c['text']}" for c in chunks
    )


def select_items(golden: list[dict], only: str = "", limit: int = 0) -> list[dict]:
    """Pick which questions to run.

    Args:
        golden: All loaded questions.
        only: Comma-separated question ids (empty means all). An unknown id is an error.
        limit: Keep only the first N after filtering (0 means all).

    Returns:
        The selected questions, in file order.
    """
    if only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        missing = wanted - {g["id"] for g in golden}
        if missing:
            raise SystemExit(f"unknown question ids: {sorted(missing)}")
        golden = [g for g in golden if g["id"] in wanted]
    return golden[:limit] if limit else golden


def run_pipeline(golden, retriever, llm, system, k):
    """Run retrieval and answering for every golden question.

    Args:
        golden: List of golden-set items (question, reference ids, reference answer...).
        retriever: The Retriever that searches the policy chunks.
        llm: The model wrapper used to write answers.
        system: The system prompt text for answering from excerpts.
        k: How many chunks to retrieve per question.

    Returns:
        One result dict per question: the answer, retrieved chunks, hit flag and latency.
    """
    rows = []
    for item in golden:
        # --- Step 1: retrieve the top k chunks and ask the model to answer from them ---
        start = time.perf_counter()
        chunks = retriever.retrieve(item["question"], k)
        user = f"Policy excerpts:\n{_excerpts(chunks)}\n\nQuestion: {item['question']}"
        try:
            out = llm.parse(system, user, RagAnswer)
            answer, answerable, cited = out.answer, out.answerable, out.cited_chunk_ids
        except Exception as exc:  # noqa: BLE001 - record the failure, keep the run going
            # One failed call should not throw away the whole run, so count it as "not answered".
            answer, answerable, cited = f"[LLM error: {type(exc).__name__}]", False, []

        # --- Step 2: build the result row with the checks used later ---
        retrieved_ids = [c["chunk_id"] for c in chunks]
        retrieved_docs = {c["doc_id"] for c in chunks}
        refs = set(item["reference_policy_ids"])
        rows.append(
            {
                **item,
                "answer": answer,
                "answered": answerable,
                "cited_chunk_ids": cited,
                "retrieved_chunk_ids": retrieved_ids,
                "retrieved_docs": sorted(retrieved_docs),
                "contexts": [c["text"] for c in chunks],
                # "hit" = every reference policy is among the retrieved docs.
                # None when the question has no reference policy (unanswerable ones).
                "hit": refs <= retrieved_docs if refs else None,
                # Safety check: the model must not cite a chunk it was never shown.
                "invalid_citations": [c for c in cited if c not in retrieved_ids],
                "latency_s": round(time.perf_counter() - start, 2),
            }
        )
        print(f"{item['id']} answered={answerable} hit={rows[-1]['hit']} {item['question'][:60]}")
    return rows


def ragas_scores(rows, usage: UsageHandler) -> tuple[dict, list[dict]]:
    """Run the four RAGAS metrics on the answerable questions.

    Args:
        rows: Result rows from run_pipeline. Each gets a "ragas" key added (in place).
        usage: Handler that counts the judge model's tokens.

    Returns:
        A tuple of (mean score per metric, raw per-question score records).
    """
    # RAGAS imports are inside the function because they are slow and only needed here.
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import nest_asyncio
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

    # RAGAS runs async code; nest_asyncio allows that when an event loop already exists.
    nest_asyncio.apply()

    # --- Step 1: build the RAGAS dataset (unanswerable questions have no reference answer) ---
    samples_rows = [r for r in rows if r["type"] != "unanswerable"]
    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=r["question"],
                response=r["answer"],
                retrieved_contexts=r["contexts"],
                reference=r["reference_answer"],
            )
            for r in samples_rows
        ]
    )

    # --- Step 2: score with a judge model at temperature 0 (most repeatable) ---
    llm = ChatOpenAI(model=JUDGE_MODEL, temperature=0, callbacks=[usage])
    embeddings = OpenAIEmbeddings(model=config.EMBEDDING_MODEL)
    result = evaluate(
        dataset=dataset,
        metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()],
        llm=llm,
        embeddings=embeddings,
        show_progress=False,
    )

    # --- Step 3: average per metric and attach each question's scores to its row ---
    per_item = result.to_pandas().to_dict(orient="records")
    names = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    # `p.get(n) == p.get(n)` is False only for NaN, so this drops failed (NaN) scores.
    summary = {n: mean([p.get(n) for p in per_item if p.get(n) == p.get(n)]) for n in names}
    for r, p in zip(samples_rows, per_item):
        r["ragas"] = {n: (None if p.get(n) != p.get(n) else round(float(p[n]), 4)) for n in names}
    return summary, per_item


def main() -> int:
    """Run the whole RAG evaluation, save the result file and print the metrics.

    Returns:
        The process exit code (0 means success).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--strategy", default=config.CHUNK_STRATEGY)
    parser.add_argument(
        "--retriever", default=config.RETRIEVAL_MODE, help="dense|hybrid|multiquery"
    )
    parser.add_argument("--k", type=int, default=config.TOP_K)
    parser.add_argument("--limit", type=int, default=0, help="only the first N items (smoke test)")
    parser.add_argument(
        "--include-sdg",
        action="store_true",
        help="also use the reviewed synthetic questions in golden_rag_sdg.jsonl (python -m eval.sdg --merge)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated question ids to run, for example SDG03,G10 (cheap re-check)",
    )
    args = parser.parse_args()

    # --- Step 1: load the golden set and prepare the index, retriever and model ---
    golden = load_jsonl("golden_rag.jsonl")
    if args.include_sdg or args.only:
        golden += load_jsonl("golden_rag_sdg.jsonl")
    golden = select_items(golden, args.only, args.limit)
    ingest_mod.ingest(strategy=args.strategy)  # no-op when the collection is up to date
    retriever = Retriever(strategy=args.strategy, mode=args.retriever)
    llm = OpenAILLM()
    system = (config.ROOT_DIR / "prompts" / "rag_answer.md").read_text(encoding="utf-8")

    # --- Step 2: retrieve and answer, then score the answerable questions with RAGAS ---
    rows = run_pipeline(golden, retriever, llm, system, args.k)
    usage = UsageHandler()
    answerable = [r for r in rows if r["type"] != "unanswerable"]
    unanswerable = [r for r in rows if r["type"] == "unanswerable"]
    scores = {}
    if answerable:
        scores, _ = ragas_scores(rows, usage)

    # --- Step 3: add the non-RAGAS metrics ---
    declined = [r for r in unanswerable if not r["answered"]]
    metrics = {
        **scores,
        "correctly_declined": f"{len(declined)}/{len(unanswerable)}" if unanswerable else None,
        "wrongly_declined": f"{sum(1 for r in answerable if not r['answered'])}/{len(answerable)}",
        "retrieval_hit_rate": mean([1.0 if r["hit"] else 0.0 for r in answerable]),
        "invalid_citations": sum(len(r["invalid_citations"]) for r in rows),
        "mean_latency_s": mean([r["latency_s"] for r in rows]),
    }

    # --- Step 4: estimate cost for the answering model and for the judge ---
    cost = {
        "agent_model": config.LLM_MODEL,
        "agent_usage": llm.usage,
        "agent_cost_usd": estimate_cost(
            config.LLM_MODEL, llm.usage["prompt_tokens"], llm.usage["completion_tokens"]
        ),
        "judge_model": JUDGE_MODEL,
        "judge_calls": usage.calls,
        "judge_cost_usd": estimate_cost(JUDGE_MODEL, usage.prompt_tokens, usage.completion_tokens),
    }

    # --- Step 5: save with the settings actually used, then print a summary ---
    # Override the defaults in environment() with this run's command-line choices.
    env = environment() | {
        "chunk_strategy": args.strategy,
        "retriever": args.retriever,
        "top_k": args.k,
    }
    path = save_result(
        f"rag_{args.label}.json",
        {"label": args.label, "environment": env, "metrics": metrics, "cost": cost, "items": rows},
    )
    print("\n== RAG metrics:", args.label)
    for key, value in metrics.items():
        print(f"  {key}: {value}")
    print(
        f"  estimated cost: agent USD {cost['agent_cost_usd']} + judge USD {cost['judge_cost_usd']}"
    )
    print("saved:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
