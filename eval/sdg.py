"""RAGAS synthetic test-set generation (course Session 5, `1-SDG.ipynb`) on the policy documents.

"Synthetic data generation" (SDG) means a language model invents test questions and answers
from our own documents, so we get more test data than we could write by hand.
RAGAS is the library that does it. A "knowledge graph" here is the set of policy chunks plus
links between similar ones, which RAGAS uses to write questions. "Single-hop" questions need one
chunk to answer. "Multi-hop" questions need facts from several chunks.

Run: uv run python -m eval.sdg --size 15
     uv run python -m eval.sdg --triage-only     (re-flag the saved candidates; no API calls)
     uv run python -m eval.sdg --merge           (reviewed keep/edit candidates -> eval/golden_rag_sdg.jsonl)
The generation run calls paid APIs. It writes docs/evidence/evals/sdg_candidates.jsonl,
docs/evidence/evals/sdg_summary.json and docs/evidence/evals/sdg_knowledge_graph.json.

Pipeline (same steps as the course notebook): policies -> KnowledgeGraph document nodes ->
`default_transforms` (summaries, themes, entities, similarity links) -> `TestsetGenerator` with a
query distribution of 50% single-hop specific, 25% multi-hop abstract, 25% multi-hop specific.

The output is a list of CANDIDATES, never a finished test set. They are written to
`docs/evidence/evals/sdg_candidates.jsonl` with `reviewed_by_human: false`, kept apart from the hand-made
`eval/golden_rag.jsonl`, and are not used in any reported metric until the owner has read and
accepted them. Each candidate is annotated automatically:
  - `source_docs`: policy documents whose text contains the reference context;
  - `context_in_policy`: every reference context really is a passage of the policies;
  - `similar_golden`: the closest hand-made golden question when it is nearly the same;
  - `retrieval_hit`: do the current retriever's top-k chunks include the source documents.
"""

import argparse
import json
import math
import os
import re
import warnings

from app import config
from app.rag.embeddings import make_embeddings
from app.rag.retriever import Retriever
from eval.common import EVAL_DIR, RESULTS_DIR, environment, estimate_cost, load_jsonl, save_result

SDG_MODEL = os.getenv("SDG_MODEL", "gpt-4o-mini")  # not a reasoning model: RAGAS prompts need n>1
# Cosine similarity (1.0 = same meaning) at or above this marks a candidate as a near-duplicate
# of a hand-made golden question. 0.88 is a judgement call, tuned by eye, not a standard value.
DUPLICATE_THRESHOLD = 0.88
OUT = RESULTS_DIR / "sdg_candidates.jsonl"
MERGED_OUT = (
    EVAL_DIR / "golden_rag_sdg.jsonl"
)  # reviewed candidates, kept apart from the golden set


def _norm(text: str) -> str:
    """Simplify a text so two versions of the same passage compare equal.

    Args:
        text: A passage, possibly with markdown symbols or a RAGAS hop label.

    Returns:
        Lower-case text with markdown symbols removed and whitespace collapsed.
    """
    text = re.sub(r"^\s*<\d+-hop>", "", text)  # multi-hop contexts are labelled "<1-hop>" ...
    return re.sub(r"[\s*_`#>|-]+", " ", text).strip().lower()


def load_documents():
    """Load every policy markdown file as a LangChain Document.

    Returns:
        A list of Documents. Each has a doc_id (such as "POL-01") taken from the file name.
    """
    # Imported here so that importing this module stays fast.
    from langchain_core.documents import Document

    docs = []
    for path in sorted((config.DATA_DIR / "policies").glob("*.md")):
        docs.append(
            Document(
                page_content=path.read_text(encoding="utf-8"),
                metadata={"doc_id": path.name.split("_")[0], "source": path.name},
            )
        )
    return docs


def section_nodes(docs):
    """Split each policy into one chunk per `## ` section, text exactly as written (no added prefix).

    Args:
        docs: The policy Documents from load_documents().

    Returns:
        A list of section Documents. Each keeps its policy metadata plus a "section" title.
    """
    from langchain_core.documents import Document

    out = []
    for d in docs:
        # re.split with a capture group returns [intro, heading1, body1, heading2, body2, ...].
        # So headings are the odd items and bodies are the even items after them.
        parts = re.split(r"(?m)^## +(.+)$", d.page_content)
        for heading, body in zip(parts[1::2], parts[2::2], strict=False):
            if body.strip():
                out.append(
                    Document(
                        page_content=f"## {heading.strip()}\n{body.strip()}",
                        metadata={**d.metadata, "section": heading.strip()},
                    )
                )
    return out


def source_docs(contexts: list[str], docs) -> list[str]:
    """Find which policies contain the given reference contexts (markdown noise removed).

    Args:
        contexts: Reference passages that RAGAS used to write a question.
        docs: The policy Documents.

    Returns:
        A sorted list of policy ids (such as "POL-01") whose text contains a context.
    """
    found = set()
    for ctx in contexts:
        # Only the first 200 characters are matched: enough to be unique, and it tolerates
        # small changes that RAGAS may make near the end of a passage.
        needle = _norm(ctx)[:200]
        for d in docs:
            if needle and needle in _norm(d.page_content):
                found.add(d.metadata["doc_id"])
    return sorted(found)


def context_in_policy(contexts: list[str], docs) -> bool:
    """Check that every reference context really is a passage of the policies.

    This guards against a model inventing a "source" that does not exist.

    Args:
        contexts: Reference passages from RAGAS.
        docs: The policy Documents.

    Returns:
        True if there is at least one context and all of them are found; otherwise False.
    """
    whole = " ".join(_norm(d.page_content) for d in docs)
    return bool(contexts) and all(_norm(c)[:200] in whole for c in contexts)


def cosine(a, b) -> float:
    """Compute cosine similarity: how close two vectors point, from -1 to 1 (1 = same meaning).

    Args:
        a: First embedding vector.
        b: Second embedding vector (same length).

    Returns:
        The similarity as a float.
    """
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


def generate(docs, size: int):
    """Build the knowledge graph and ask RAGAS to generate test questions.

    Args:
        docs: The policy Documents.
        size: How many questions to generate.

    Returns:
        A tuple (samples, usage, node count, relationship count). `samples` is a list of dicts,
        and `usage` is a UsageHandler with the token counts.
    """
    # --- Step 1: imports and model setup. The heavy imports are here so that other
    # functions in this file can be used (and tested) without loading RAGAS ---
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import nest_asyncio
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.testset import TestsetGenerator
    from ragas.testset.graph import KnowledgeGraph, Node, NodeType
    from ragas.testset.synthesizers import (
        MultiHopAbstractQuerySynthesizer,
        MultiHopSpecificQuerySynthesizer,
        SingleHopSpecificQuerySynthesizer,
    )
    from ragas.testset.transforms import apply_transforms, default_transforms_for_prechunked

    from eval.common import UsageHandler

    # RAGAS runs async code; nest_asyncio allows that when an event loop already exists.
    nest_asyncio.apply()
    usage = UsageHandler()
    llm = LangchainLLMWrapper(ChatOpenAI(model=SDG_MODEL, temperature=0, callbacks=[usage]))
    emb = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=config.EMBEDDING_MODEL))

    # --- Step 2: build the knowledge graph from section chunks ---
    # The policies are about 600 tokens each, which sends `default_transforms` down its headline
    # splitter path and it fails on the shorter documents ("'headlines' property not found").
    # Each `## ` section is already a natural chunk, so the pre-chunked pipeline is used.
    kg = KnowledgeGraph()
    for d in section_nodes(docs):
        kg.nodes.append(
            Node(
                type=NodeType.CHUNK,
                properties={"page_content": d.page_content, "document_metadata": d.metadata},
            )
        )
    # The transforms add summaries, themes, entities and similarity links to each node.
    apply_transforms(kg, default_transforms_for_prechunked(llm=llm, embedding_model=emb))
    kg.save(str(RESULTS_DIR / "sdg_knowledge_graph.json"))

    # --- Step 3: generate questions with the mix of question types (weights add up to 1.0) ---
    generator = TestsetGenerator(llm=llm, embedding_model=emb, knowledge_graph=kg)
    distribution = [
        (SingleHopSpecificQuerySynthesizer(llm=llm), 0.5),
        (MultiHopAbstractQuerySynthesizer(llm=llm), 0.25),
        (MultiHopSpecificQuerySynthesizer(llm=llm), 0.25),
    ]
    testset = generator.generate(testset_size=size, query_distribution=distribution)
    return testset.to_list(), usage, len(kg.nodes), len(kg.relationships)


def annotate(samples: list[dict], docs) -> list[dict]:
    """Turn RAGAS samples into candidate rows with automatic quality annotations.

    Args:
        samples: Raw samples from generate().
        docs: The policy Documents.

    Returns:
        One dict per candidate, marked as not yet reviewed by a human.
    """
    # Embed the golden questions once, to spot candidates that repeat a hand-made question.
    embeddings = make_embeddings()
    golden = load_jsonl("golden_rag.jsonl")
    golden_vecs = embeddings.embed_documents([g["question"] for g in golden])
    retriever = Retriever()
    rows = []
    for i, s in enumerate(samples, start=1):
        contexts = s.get("reference_contexts") or []
        srcs = source_docs(contexts, docs)
        # Find the golden question closest in meaning to this candidate.
        vec = embeddings.embed_query(s["user_input"])
        sims = [cosine(vec, g) for g in golden_vecs]
        best = max(range(len(sims)), key=sims.__getitem__)
        # Which policies does the real retriever find for this question?
        retrieved = {c["doc_id"] for c in retriever.retrieve(s["user_input"], config.TOP_K)}
        rows.append(
            {
                "id": f"SDG{i:02d}",
                "question": s["user_input"],
                "reference_answer": s.get("reference", ""),
                "reference_contexts": contexts,
                "synthesizer": s.get("synthesizer_name", ""),
                "source": "ragas_sdg",
                "source_docs": srcs,
                "context_in_policy": context_in_policy(contexts, docs),
                "similar_golden": (
                    {"id": golden[best]["id"], "similarity": round(sims[best], 3)}
                    if sims[best] >= DUPLICATE_THRESHOLD
                    else None
                ),
                # None when no source policy was found, so there is nothing to compare with.
                "retrieval_hit": (set(srcs) <= retrieved) if srcs else None,
                "reviewed_by_human": False,
                "review_decision": "",  # owner: keep / edit / drop
            }
        )
    return rows


# Matches low-value questions such as "What is POL-03?" that only ask what an id means.
DOC_ID_QUESTION = re.compile(
    r"^\s*(what|who|which)\s+(is|are|does|do)\s+(the\s+)?POL-\d+\b", re.IGNORECASE
)


def triage(row: dict) -> list[str]:
    """Raise cheap automatic flags that help the reviewer; they do not decide anything.

    Args:
        row: One candidate row from annotate().

    Returns:
        A list of flag names (empty if the candidate looks fine).
    """
    flags = []
    question = row["question"]
    named = set(re.findall(r"POL-\d+", question))
    if named - set(row["source_docs"]):
        flags.append("names_policy_not_in_source")
    if DOC_ID_QUESTION.match(question) or re.search(
        r"\bwhat does POL-\d+ refer", question, re.IGNORECASE
    ):
        flags.append("asks_what_a_policy_id_is")
    if len(row["source_docs"]) > 1 and row["synthesizer"].startswith("single_hop"):
        flags.append("single_hop_but_several_source_docs")
    if row["retrieval_hit"] is False:
        flags.append("retriever_misses_source")
    return flags


def merge_reviewed(rows: list[dict]) -> list[dict]:
    """Turn the reviewed candidates that were kept or edited into golden-set rows.

    A candidate is merged only when a reviewer set `reviewed_by_human` to true AND the decision is
    `keep` or `edit`. Dropped, undecided and unreviewed candidates are left out. The output rows
    have the same fields as `eval/golden_rag.jsonl`, so the RAG evaluation can read them.

    Args:
        rows: Candidate rows as saved in docs/evidence/evals/sdg_candidates.jsonl.

    Returns:
        Golden-style rows, in the order of the candidates. The ids stay as the candidate ids
        (SDG01, ...), so they never clash with the hand-made ids (G01, ...).
    """
    merged = []
    for r in rows:
        if not r.get("reviewed_by_human") or r.get("review_decision") not in ("keep", "edit"):
            continue
        merged.append(
            {
                "id": r["id"],
                "question": r["question"],
                "reference_answer": r["reference_answer"],
                "reference_policy_ids": list(r["source_docs"]),
                "type": "answerable",
                "reviewed_by_human": True,
                "source": "ragas_sdg",
                "review_decision": r["review_decision"],
            }
        )
    return merged


def merge() -> None:
    """Write the kept and edited reviewed candidates to eval/golden_rag_sdg.jsonl (no API calls)."""
    rows = [json.loads(x) for x in OUT.read_text("utf-8").splitlines() if x]
    merged = merge_reviewed(rows)
    target = MERGED_OUT
    target.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n", "utf-8")
    skipped = len(rows) - len(merged)
    print(f"{len(merged)} of {len(rows)} candidates merged into {target} ({skipped} left out)")


def main() -> None:
    """Generate candidates (or only re-flag saved ones), then save and print a summary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=15)
    parser.add_argument("--triage-only", action="store_true", help="re-flag the saved candidates")
    parser.add_argument(
        "--merge", action="store_true", help="write reviewed keep/edit candidates to golden_rag_sdg"
    )
    args = parser.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # Merge mode: copy only the candidates a reviewer kept or edited. No API calls.
    if args.merge:
        merge()
        return
    # Shortcut mode: re-run only the cheap flags on the file we already saved. No API calls.
    if args.triage_only:
        rows = [json.loads(x) for x in OUT.read_text("utf-8").splitlines() if x]
        for r in rows:
            r["auto_flags"] = triage(r)
        OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
        print(f"{sum(1 for r in rows if r['auto_flags'])} of {len(rows)} candidates flagged")
        return

    # --- Step 1: generate candidates, annotate them and flag the suspicious ones ---
    docs = load_documents()
    samples, usage, n_nodes, n_rel = generate(docs, args.size)
    rows = annotate(samples, docs)
    for r in rows:
        r["auto_flags"] = triage(r)
    # One JSON object per line (JSONL), so humans can review and diff the file easily.
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")

    # --- Step 2: build and save a run summary ---
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["synthesizer"]] = kinds.get(r["synthesizer"], 0) + 1
    hits = [r["retrieval_hit"] for r in rows if r["retrieval_hit"] is not None]
    summary = {
        "environment": environment(),
        "generator_model": SDG_MODEL,
        "requested": args.size,
        "generated": len(rows),
        "graph_nodes": n_nodes,
        "graph_relationships": n_rel,
        "by_synthesizer": kinds,
        "context_in_policy": sum(r["context_in_policy"] for r in rows),
        "no_source_doc_found": sum(1 for r in rows if not r["source_docs"]),
        "near_duplicates_of_golden": sum(1 for r in rows if r["similar_golden"]),
        "retrieval_hit_rate": round(sum(hits) / len(hits), 3) if hits else None,
        "flagged_for_review": sum(1 for r in rows if r["auto_flags"]),
        "cost_usd": estimate_cost(SDG_MODEL, usage.prompt_tokens, usage.completion_tokens),
    }
    save_result("sdg_summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "environment"}, indent=2))
    print(f"candidates written to {OUT}")


if __name__ == "__main__":
    main()
