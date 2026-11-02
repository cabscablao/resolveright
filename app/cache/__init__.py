"""Two cache tiers: exact-match LLM cache (SQLite) and a semantic cache on Qdrant.

A cache stores an earlier answer so a repeat question is served instantly and for free,
instead of calling the LLM again.
"""
