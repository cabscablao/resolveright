"""Tests for the synthetic test-question generator (SDG) helpers. No network needed.

SDG makes draft evaluation questions from the policies. These tests cover the plain helper
functions around it: cutting policies into sections, matching text back to a policy,
and flagging weak questions for a human to review.
"""

from eval import sdg


def test_section_nodes_keep_policy_text_exactly_and_carry_the_doc_id():
    """Sections are exact copies of policy text and know which policy they came from.

    Exact text is needed so we can later prove a question's source really exists.
    """
    docs = sdg.load_documents()
    nodes = sdg.section_nodes(docs)
    assert len(docs) == 10 and len(nodes) > len(docs)
    assert all(n.page_content.startswith("## ") and n.metadata["doc_id"] for n in nodes)
    assert sdg.context_in_policy([n.page_content for n in nodes], docs)


def test_source_docs_finds_the_policy_that_holds_the_text():
    """Given a passage, we can name the policy it came from (used as the "right answer" source)."""
    docs = sdg.load_documents()
    node = sdg.section_nodes(docs)[0]
    assert sdg.source_docs([node.page_content], docs) == [node.metadata["doc_id"]]


def test_multi_hop_labels_are_ignored_when_matching():
    """A "<1-hop>" label added by the generator must not stop a passage from matching."""
    docs = sdg.load_documents()
    node = sdg.section_nodes(docs)[0]
    labelled = f"<1-hop>\n\n{node.page_content}"
    assert sdg.context_in_policy([labelled], docs)
    assert sdg.source_docs([labelled], docs) == [node.metadata["doc_id"]]


def test_invented_text_is_not_a_policy_passage():
    """Made-up text is rejected, so a model cannot slip in facts that are not in our policies."""
    docs = sdg.load_documents()
    assert not sdg.context_in_policy(["Brightcart offers a 90 day unicorn guarantee."], docs)
    assert sdg.source_docs(["Brightcart offers a 90 day unicorn guarantee."], docs) == []
    assert not sdg.context_in_policy([], docs)


def _row(question, docs, synth="single_hop_specific_query_synthesizer", hit=True):
    """Build a minimal generated-question row so triage tests stay short."""
    return {"question": question, "source_docs": docs, "synthesizer": synth, "retrieval_hit": hit}


def test_triage_flags_the_known_weak_patterns():
    """Each known weak-question pattern gets its flag, and a good question gets none.

    The flags tell the human reviewer which candidates need a closer look.
    """
    assert sdg.triage(_row("What is the return window?", ["POL-01"])) == []
    assert "names_policy_not_in_source" in sdg.triage(_row("What does POL-04 say?", ["POL-01"]))
    assert "asks_what_a_policy_id_is" in sdg.triage(_row("What is POL-10?", ["POL-10"]))
    assert "single_hop_but_several_source_docs" in sdg.triage(_row("q", ["POL-01", "POL-02"]))
    assert "retriever_misses_source" in sdg.triage(_row("q", ["POL-01"], hit=False))
    # Several source docs is normal for a multi-hop question, so no flag here.
    multi = _row("q", ["POL-01", "POL-02"], synth="multi_hop_abstract_query_synthesizer")
    assert sdg.triage(multi) == []


def test_cosine():
    """Cosine similarity gives 1 for identical directions and 0 for unrelated ones."""
    assert round(sdg.cosine([1, 0], [1, 0]), 6) == 1.0
    assert round(sdg.cosine([1, 0], [0, 1]), 6) == 0.0


def test_merge_keeps_only_reviewed_keep_and_edit_candidates():
    """Dropped, undecided and unreviewed candidates stay out; the rest become golden-style rows."""
    base = {"question": "Q?", "reference_answer": "A.", "source_docs": ["POL-01"]}
    rows = [
        {**base, "id": "SDG01", "reviewed_by_human": True, "review_decision": "keep"},
        {**base, "id": "SDG02", "reviewed_by_human": True, "review_decision": "edit"},
        {**base, "id": "SDG03", "reviewed_by_human": True, "review_decision": "drop"},
        {**base, "id": "SDG04", "reviewed_by_human": False, "review_decision": "keep"},
        {**base, "id": "SDG05", "reviewed_by_human": True, "review_decision": ""},
    ]
    merged = sdg.merge_reviewed(rows)
    assert [m["id"] for m in merged] == ["SDG01", "SDG02"]
    assert merged[0]["reference_policy_ids"] == ["POL-01"]
    assert merged[0]["type"] == "answerable" and merged[0]["reviewed_by_human"] is True
