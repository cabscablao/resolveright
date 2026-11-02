"""Manual retrieval check on 5 questions (gate for W1 Weekday 4).

"Retrieval" is the search step of RAG: given a question, find the policy chunks most likely
to answer it. This script asks 5 questions and checks that the right policy is in the top 3.
It is a quick sanity check, not a full evaluation (see eval/rag_eval.py for that).

Run: uv run python -m scripts.retrieval_check            (real embeddings, needs OPENAI_API_KEY)
     $env:EMBEDDING_PROVIDER='hash'; uv run python -m scripts.retrieval_check   (offline)
Ingests first if the store is empty, then prints the top 3 chunks per question.
It writes nothing except the vector store that ingest may fill. Exit code 0 means all 5 passed.
"""

from app.rag.embeddings import make_embeddings
from app.rag.ingest import ingest
from app.rag.retriever import Retriever

# (question, id of the policy that should be found for it)
CHECKS = [
    ("How many days do I have to return something after it arrives?", "POL-01"),
    ("Who can approve a refund over ₱5,000?", "POL-02"),
    ("My package still has not arrived, when is it treated as lost?", "POL-06"),
    ("Can I cancel my order after it has shipped?", "POL-07"),
    ("Does the warranty cover a laptop that stopped working after 8 months?", "POL-08"),
]


def main() -> int:
    """Run every check and print the top 3 chunks for each question.

    Returns:
        0 if every question found its expected policy in the top 3, otherwise 1.
    """
    # --- Step 1: make sure the policy chunks are in the vector store ---
    embeddings = make_embeddings()
    print("ingest:", ingest(embeddings=embeddings))
    retriever = Retriever(embeddings)

    # --- Step 2: ask each question and look for the expected policy in the top 3 ---
    passed = 0
    for question, expected in CHECKS:
        chunks = retriever.retrieve(question, 3)
        found = expected in [c["doc_id"] for c in chunks]
        # True counts as 1, so this adds up the passed checks.
        passed += found
        print(f"\nQ: {question}\n   expected {expected}: {'OK' if found else 'MISS'}")
        for c in chunks:
            print(f"   {c['score']:.3f}  {c['chunk_id']:<10} {c['title']}")
    print(f"\n{passed}/{len(CHECKS)} questions retrieved the expected policy in the top 3")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
