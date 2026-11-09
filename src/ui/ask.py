"""Ask tab: one question, one answer card, one citation, and inspection."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.config import Settings
from src.service import answer_question
from src.ui.artifacts import (
    prompt_artifact,
    raw_llm_artifact,
    retrieval_artifact,
    selected_rows,
    trace_rows,
    user_prompt_text,
    validation_rows,
)
from src.ui.common import render_answer_card

EXAMPLES = [
    (
        "Large Cap expense ratio",
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
    ),
    (
        "ELSS lock-in",
        "What is the lock-in period for HDFC ELSS Tax Saver Fund Direct Plan Growth?",
    ),
    (
        "Capital-gains statement",
        "Where can I download a capital-gains statement?",
    ),
]


def _handle_pending_question() -> None:
    if st.session_state.pop("clear_question_input", False):
        st.session_state["question_input"] = ""
    pending = st.session_state.pop("pending_question", None)
    if pending:
        st.session_state["question_input"] = pending
        st.session_state["pending_submit"] = True


def _render_examples() -> None:
    st.markdown("**Try a factual question**")
    columns = st.columns(3)
    for column, (label, question) in zip(columns, EXAMPLES):
        if column.button(label, width="stretch", key=f"example_{label}"):
            st.session_state["pending_question"] = question
            st.rerun()


def _render_inspect(settings: Settings, response: dict[str, Any]) -> None:
    trace_id = str(response.get("retrieval_trace_id") or response.get("trace_id") or "")
    trace = retrieval_artifact(settings, trace_id) or {}
    prompt = prompt_artifact(settings, trace_id)
    raw = raw_llm_artifact(settings, trace_id)
    answer_path = settings.path("answers") / f"{trace_id}.json"
    trace_path = settings.path("retrieval") / f"{trace_id}.json"

    with st.expander("Inspect this answer", expanded=False):
        retrieval_tab, validation_tab, prompt_tab, artifacts_tab = st.tabs(
            ["Retrieval", "Validation", "Prompt & LLM", "Artifacts"]
        )

        with retrieval_tab:
            gate = trace.get("evidence_gate", {}) or {}
            left, middle, right = st.columns(3)
            left.metric("Intent", str(trace.get("intent", response.get("intent", "—"))))
            middle.metric(
                "Scheme", str(trace.get("scheme") or response.get("scheme") or "—")
            )
            right.metric(
                "Evidence gate",
                "passed" if gate.get("passed") else str(gate.get("reason", "not reached")),
            )
            st.caption(
                f"Fact type: `{trace.get('fact_type') or response.get('fact_type') or '—'}`"
                f" · Selected chunks: {len(trace.get('selected', []) or [])}"
            )
            st.caption(f"Redacted question: {response.get('question_redacted', '—')}")

            filters = trace.get("filters") or {}
            if filters:
                st.markdown("**Primary filters**")
                st.json(filters)
            attempts = trace.get("filter_attempts") or []
            if attempts:
                st.markdown("**Filter fallback attempts**")
                st.dataframe(pd.DataFrame(attempts), width="stretch", hide_index=True)

            candidates = trace_rows(trace)
            if candidates:
                st.markdown("**Candidates**")
                st.dataframe(
                    pd.DataFrame(candidates), width="stretch", hide_index=True
                )
            else:
                st.info("No candidates were retrieved for this trace.")

            selected = selected_rows(trace)
            if selected:
                st.markdown("**Selected evidence**")
                for row in selected:
                    with st.container(border=True):
                        st.caption(
                            f"`{row['chunk_id']}` · score {row['score']} · "
                            f"{row['source_type']} · {row['fact_type']}"
                        )
                        st.write(row["text"])

            checks = gate.get("checks") or []
            if checks:
                st.markdown("**Evidence-gate checks**")
                st.dataframe(pd.DataFrame(checks), width="stretch", hide_index=True)

        with validation_tab:
            validation = response.get("validation", {}) or {}
            left, middle, right = st.columns(3)
            left.metric("Passed", "yes" if validation.get("passed") else "no")
            middle.metric("Reason", str(validation.get("reason") or "—"))
            right.metric("Sentences", int(validation.get("sentence_count", 0) or 0))
            checks = validation_rows(response)
            if checks:
                st.dataframe(
                    pd.DataFrame(checks), width="stretch", hide_index=True
                )
            else:
                st.info("No detailed validator checks were recorded for this response.")
            if response.get("answer_type") == "fact":
                st.caption(
                    "Fact answers must resolve one approved official citation and ground "
                    "every number in the cited chunk."
                )

        with prompt_tab:
            st.caption(
                f"Provider: `{response.get('provider') or '—'}` · "
                f"Model: `{response.get('model') or '—'}` · "
                f"Fallback: `{response.get('fallback_used')}`"
            )
            st.caption(f"Prompt hash: `{response.get('prompt_hash') or '—'}`")
            if prompt:
                st.markdown("**User prompt and evidence**")
                st.caption("The system prompt is intentionally hidden.")
                st.code(user_prompt_text(prompt), language="text")
            else:
                st.info(
                    "No prompt artifact exists for this trace. Guardrail, clarification, "
                    "and no-evidence paths stop before generation."
                )
            if raw:
                responses = raw.get("responses", []) or []
                if responses:
                    st.markdown("**Latest raw provider response**")
                    st.code(str(responses[-1]), language="text")
                else:
                    st.info("The provider returned no raw text.")
            if response.get("fallback_reason"):
                st.caption(f"Fallback reason: `{response['fallback_reason']}`")

        with artifacts_tab:
            st.markdown("**Artifact paths**")
            st.code(
                f"Retrieval: {trace_path}\n"
                f"Answer:    {answer_path}\n"
                f"Prompt:    {settings.path('answers') / (trace_id + '.prompt.txt')}\n"
                f"Raw LLM:   {settings.path('answers') / (trace_id + '.llm_raw.json')}",
                language="text",
            )
            st.caption(f"Trace ID: `{trace_id}`")
            st.json(response)


def _run_answer(settings: Settings, question: str) -> None:
    with st.spinner("Searching..."):
        try:
            response = answer_question(question, settings)
        except Exception:  # noqa: BLE001 - never expose a traceback or secrets in the UI
            st.error(
                "The answer pipeline could not complete this request. "
                "Check the terminal logs, then inspect the Pipeline tab for the last artifact."
            )
            return
    st.session_state["current_response"] = response.model_dump(mode="json")
    st.session_state["current_question_redacted"] = response.question_redacted
    st.session_state["current_trace_id"] = response.trace_id
    if response.intent == "pii":
        # Keep PII out of the visible input after the refusal is rendered once.
        st.session_state["clear_question_input"] = True
        st.rerun()


def render(settings: Settings) -> None:
    _handle_pending_question()

    st.markdown("<div class='mf-kicker'>Facts-only RAG demo</div>", unsafe_allow_html=True)
    st.title("HDFC Mutual Fund Facts Assistant")
    st.markdown(
        "Ask a factual question about **HDFC Large Cap**, **Flexi Cap**, "
        "**ELSS Tax Saver**, **Mid Cap**, or **HDFC Mutual Fund**. "
        "I answer from public official sources and always show one source."
    )

    _render_examples()

    max_chars = int(settings.data.get("app", {}).get("max_question_chars", 500))
    with st.form("ask_form", clear_on_submit=False):
        question = st.text_input(
            "Your question",
            key="question_input",
            max_chars=max_chars,
            placeholder="What is the exit load for HDFC Mid Cap Fund Direct Growth?",
        )
        submitted = st.form_submit_button("Ask", type="primary", width="content")
    pending_submit = bool(st.session_state.pop("pending_submit", False))

    if submitted or pending_submit:
        if not question or not question.strip():
            st.warning("Enter a question first.", icon="⚠️")
        else:
            _run_answer(settings, question)

    st.caption(
        f"Questions are limited to {max_chars} characters. "
        "PAN, Aadhaar, account numbers, OTPs, emails, and phone numbers are blocked."
    )

    response = st.session_state.get("current_response")
    if response:
        st.divider()
        st.markdown("### Current answer")
        render_answer_card(response)
        _render_inspect(settings, response)
    else:
        st.info(
            "Ask one question to see the answer, evidence, validation, and one source link.",
            icon="ℹ️",
        )
