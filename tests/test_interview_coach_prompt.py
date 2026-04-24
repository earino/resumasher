"""Structural assertions on INTERVIEW_COACH_PROMPT (issue #29).

These tests don't run an LLM. They verify the prompt's text shape — the
properties the issue identified as load-bearing for keeping weaker models
(observed: Haiku 4.5) from interpreting the prompt as "create a deliverable
file." If a future edit reverts the framing, these tests fail.

The Tier 3 live test (`tests/test_interview_coach_live.py`) is what
actually proves Haiku behaves; these are the cheap regression guards.
"""

from __future__ import annotations

from scripts.prompts import INTERVIEW_COACH_PROMPT


def _first_paragraph(text: str) -> str:
    return text.split("\n\n", 1)[0]


def test_first_paragraph_does_not_use_bundle_or_produce_framing():
    """The opening of the prompt must NOT use "bundle" / "produce a document"
    framing — the issue traced both to weaker-model misbehavior. Use
    "generate questions and answers" / "return a markdown document" instead."""
    first_para = _first_paragraph(INTERVIEW_COACH_PROMPT).lower()
    assert "bundle" not in first_para, (
        "First paragraph must not say 'bundle' — it primes weaker models to "
        "treat the task as creating a deliverable file. See issue #29."
    )
    assert "produce a markdown document" not in first_para, (
        "First paragraph must not say 'produce a markdown document' — "
        "weaker models read this as 'create a file'. Use 'return a markdown "
        "document in your response' instead."
    )


def test_first_paragraph_explicitly_says_no_files():
    """Belt-and-suspenders: the opening must explicitly tell the model to
    return text, not write files. This is the message Haiku missed because
    the constraint was buried at the bottom in the original prompt."""
    first_para = _first_paragraph(INTERVIEW_COACH_PROMPT).lower()
    # Accept any of several phrasings for forward-compat.
    assert any(
        phrase in first_para
        for phrase in (
            "do not create any files",
            "do not write to disk",
            "do not write any files",
        )
    ), (
        "First paragraph must explicitly forbid file creation. The issue's "
        "root cause was that this constraint was at the bottom of a 10KB "
        "prompt, after the 'bundle/produce' framing had already shaped the "
        "model's plan."
    )


def test_tool_usage_constraints_appear_before_produce_framing():
    """The TOOL USAGE CONSTRAINTS block must appear BEFORE any text that
    could be interpreted as 'now produce the document'. Original bug: the
    constraint block was at line ~550 in the prompt; "produce a markdown
    document" was at line ~514. Order matters for weaker models."""
    p = INTERVIEW_COACH_PROMPT
    constraints_idx = p.find("TOOL USAGE CONSTRAINTS")
    assert constraints_idx != -1, "TOOL USAGE CONSTRAINTS block must exist"

    # The instruction-to-emit-the-doc must appear AFTER the constraints.
    # Match the new wording ("Return a markdown document inline") OR any
    # backward-compat phrasing that future edits might use.
    emit_phrases = ("Return a markdown document", "Use this structure")
    emit_idx = min(
        (p.find(phrase) for phrase in emit_phrases if p.find(phrase) != -1),
        default=-1,
    )
    assert emit_idx != -1, "Prompt must instruct the model to emit a document"
    assert constraints_idx < emit_idx, (
        "TOOL USAGE CONSTRAINTS must appear BEFORE the 'emit document' "
        "instruction. If the order regresses, weaker models will plan to "
        "use Write before they read the constraint. See issue #29."
    )


def test_tool_constraints_explicitly_call_out_write_tool():
    """The constraint block must specifically name the Write tool. A generic
    "do not use tools" instruction failed on Haiku because Haiku's plan
    treated Write as a natural step rather than a tool invocation."""
    p = INTERVIEW_COACH_PROMPT
    # Find the constraints paragraph and check it mentions Write specifically.
    constraints_start = p.find("TOOL USAGE CONSTRAINTS")
    constraints_end = p.find("\n\n", constraints_start)
    constraints_block = p[constraints_start:constraints_end]
    assert "Write" in constraints_block, (
        "TOOL USAGE CONSTRAINTS must specifically name the Write tool. "
        "A generic 'no tools' instruction is not enough for weaker models."
    )


def test_prompt_still_substitutes_required_variables():
    """Smoke check: the prompt template still has the substitution slots the
    build_prompt machinery expects. Don't lose them in the surgery."""
    for var in ("{tailored_resume}", "{folder_summary}", "{jd_text}"):
        assert var in INTERVIEW_COACH_PROMPT, (
            f"Required substitution slot {var} missing from "
            f"INTERVIEW_COACH_PROMPT — build_prompt will fail."
        )


def test_prompt_preserves_failure_sentinel():
    """The orchestrator's is-failure check looks for 'FAILURE: <reason>' —
    don't drop that contract during the surgery."""
    assert "FAILURE:" in INTERVIEW_COACH_PROMPT
    assert "<one-line reason>" in INTERVIEW_COACH_PROMPT
