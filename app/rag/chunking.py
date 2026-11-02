"""Chunking strategies (LangChain splitters, as in course Sessions 3-5).

Why this file exists: a policy document is too long to search as one piece, so we cut it into
small "chunks". Each chunk is embedded (turned into a list of numbers that captures its
meaning) and stored in the vector database. At question time the retriever finds the chunks
closest in meaning to the question.

Where it sits in the flow: ingest (app/rag/ingest.py) calls a strategy from here for every
policy file, before the chunks are embedded and saved to Qdrant.

Baseline = RecursiveCharacterTextSplitter measured in tokens (about 800, overlap 100).
The section-aware strategy is added in W2 as the targeted improvement.
"""

import re
import uuid
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app import config

# Cached tiktoken encoder. Loading it can need the network, so we load it once, lazily.
_encoder = None
# Set to True after one failed load so we do not retry (and wait for a timeout) on every call.
_encoder_failed = False


def token_len(text: str) -> int:
    """Count the tokens in a text (a token is a word piece, the unit LLMs read and bill by).

    Uses tiktoken with the gpt-4o encoding. Falls back to a word-based estimate offline.

    Args:
        text: The text to measure.

    Returns:
        The token count (exact with tiktoken, an estimate otherwise).
    """
    global _encoder, _encoder_failed
    if _encoder is None and not _encoder_failed:
        try:
            import tiktoken

            _encoder = tiktoken.encoding_for_model("gpt-4o")
        except Exception:  # noqa: BLE001 - no network to fetch the encoding file
            _encoder_failed = True
    if _encoder is not None:
        return len(_encoder.encode(text))
    # Offline fallback: English averages about 0.75 words per token, so tokens = words * 4/3.
    return int(len(text.split()) * 4 / 3)


def load_policy(path: Path) -> tuple[str, str, str]:
    """Read one policy markdown file and pull out its id and title.

    Args:
        path: Path to a file named like "POL-01-returns.md".

    Returns:
        A tuple (doc_id, title, text). doc_id is the first 6 characters of the file name
        (for example "POL-01"). The title comes from the "# POL-01: Title" heading.
    """
    text = path.read_text(encoding="utf-8")
    # "POL-01" is exactly 6 characters, so the first 6 characters of the name are the id.
    doc_id = path.name[:6]
    # Match the first-line heading "# POL-01: Returns Policy" and capture the title part.
    match = re.match(r"#\s*POL-\d+:\s*(.+)", text)
    # If the heading is missing, fall back to the id so the chunk still has a title.
    title = match.group(1).strip() if match else doc_id
    return doc_id, title, text


def point_id(chunk_id: str) -> str:
    """Turn a readable chunk id into a UUID, because Qdrant only accepts UUID point ids.

    uuid5 is deterministic: the same chunk id always gives the same UUID, so re-ingesting
    overwrites a point instead of creating a duplicate.

    Args:
        chunk_id: A readable id such as "POL-01#s2".

    Returns:
        The UUID as a string.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def baseline_chunks(
    doc_id: str,
    title: str,
    text: str,
    doc_hash: str,
    size: int = config.CHUNK_TOKENS,
    overlap: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[Document]:
    """Baseline strategy: cut the text into fixed-size pieces with no section awareness.

    Args:
        doc_id: Policy id, for example "POL-01".
        title: Policy title.
        text: Full policy text.
        doc_hash: Hash of the text; stored so ingest can tell when a file has changed.
        size: Maximum chunk size in tokens.
        overlap: Tokens repeated between neighbouring chunks, so a sentence cut at a
            boundary still appears whole in one of them.

    Returns:
        One LangChain Document per chunk, with metadata.
    """
    # Try the biggest natural break first (blank line), then line, sentence, then word.
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        length_function=token_len,
        separators=["\n\n", "\n", ". ", " "],
    )
    pieces = splitter.split_text(text)
    return [
        Document(
            page_content=piece,
            metadata={
                "chunk_id": f"{doc_id}#c{n}",
                "doc_id": doc_id,
                "title": title,
                "section": "",  # baseline has no section awareness
                "doc_hash": doc_hash,
            },
        )
        for n, piece in enumerate(pieces)
    ]


def section_chunks(
    doc_id: str,
    title: str,
    text: str,
    doc_hash: str,
    size: int = config.CHUNK_TOKENS,
    overlap: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[Document]:
    """One chunk per `## ` section, prefixed with the policy id, title and section heading.

    The prefix keeps a small chunk self-explanatory ("POL-01 Returns Policy / Return window"),
    which helps both the embedding and the BM25 keyword match (BM25 is a classic keyword
    ranking formula). A section longer than `size` tokens is split further with the
    baseline splitter.

    Args:
        doc_id: Policy id, for example "POL-01".
        title: Policy title.
        text: Full policy text.
        doc_hash: Hash of the text; stored so ingest can tell when a file has changed.
        size: Maximum chunk size in tokens.
        overlap: Tokens repeated between neighbouring chunks when a section is split.

    Returns:
        One LangChain Document per chunk, with metadata including the section heading.
    """
    # --- Step 1: split the text at every "## Heading" line ---
    # The capture group makes re.split keep the headings in the result.
    parts = re.split(r"(?m)^## +(.+)$", text)
    # parts = [preamble, heading1, body1, heading2, body2, ...]; the preamble (title and the
    # document-id line) carries no policy content and is dropped.
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        length_function=token_len,
        separators=["\n\n", "\n", ". ", " "],
    )
    docs: list[Document] = []
    # --- Step 2: pair each heading (odd positions) with its body (even positions) ---
    for heading, body in zip(parts[1::2], parts[2::2]):
        heading, body = heading.strip(), body.strip()
        if not body:
            continue
        # --- Step 3: build the prefix and keep short sections whole ---
        prefix = f"{doc_id} {title} / {heading}\n\n"
        pieces = [body] if token_len(body) <= size else splitter.split_text(body)
        for piece in pieces:
            docs.append(
                Document(
                    page_content=prefix + piece,
                    metadata={
                        # len(docs) is a running counter, so ids stay unique across sections.
                        "chunk_id": f"{doc_id}#s{len(docs)}",
                        "doc_id": doc_id,
                        "title": title,
                        "section": heading,
                        "doc_hash": doc_hash,
                    },
                )
            )
    return docs


# Maps the CHUNK_STRATEGY setting to the function that implements it.
STRATEGIES = {"baseline": baseline_chunks, "section": section_chunks}
