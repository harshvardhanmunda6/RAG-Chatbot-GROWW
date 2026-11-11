"""Chat page for the FundFacts facts-only mutual fund assistant."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from src.config import Settings
from src.guardrails import check_question
from src.service import answer_question
from src.ui.common import SUGGESTIONS, escaped


def _safe_id(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value or "turn"))


def _redacted_question(settings: Settings, question: str) -> str:
    guardrail = check_question(question, settings)
    return (guardrail.redacted_question or question).strip()


def _render_hero() -> None:
    st.markdown(
        """
<div class="ff-hero">
  <h1>Mutual Fund Facts Assistant</h1>
  <p>Ask anything factual about selected HDFC Mutual Fund schemes.</p>
</div>
""",
        unsafe_allow_html=True,
    )


def _render_suggestions() -> None:
    with st.container(key="ff_suggestions"):
        for index, (label, question) in enumerate(SUGGESTIONS):
            if st.button(label, key=f"suggestion_{index}", width="stretch"):
                st.session_state["pending_question"] = question
                st.rerun()


def _render_banner() -> None:
    st.markdown(
        """
<div class="ff-banner">
  <b>Facts-only. No investment advice.</b> Answers come from public mutual fund
  pages with a source link. It does not recommend buying, selling, or holding
  any scheme.
</div>
""",
        unsafe_allow_html=True,
    )


def _render_user_message(turn_id: str, question: str) -> None:
    with st.container(key=f"ff_user_{turn_id}"):
        with st.chat_message("user", avatar=None):
            st.markdown(
                f"<div class='ff-user-text'>{escaped(question)}</div>",
                unsafe_allow_html=True,
            )


def _answer_label(response: dict[str, Any]) -> tuple[str, str]:
    answer_type = str(response.get("answer_type") or "")
    if answer_type == "fact":
        return "answer", "✓ Verified from source"
    if answer_type == "refusal":
        return "refusal", "🛡 Facts-only policy"
    if answer_type == "error":
        return "error", "Temporarily unavailable"
    return "not_found", "Not in sources"


def _render_assistant_message(turn_id: str, response: dict[str, Any]) -> None:
    label_class, label = _answer_label(response)
    answer = str(response.get("answer") or "No answer was generated.")
    citation = response.get("citation") or {}
    url = str(citation.get("url") or "").strip()
    title = str(citation.get("title") or "").strip()
    last_updated = str(response.get("last_updated") or "").strip()

    source_html = ""
    if url.startswith("https://"):
        source_html = (
            f"<div class='ff-source'>🔗 <a href='{escaped(url)}' target='_blank' "
            f"rel='noopener noreferrer' title='{escaped(title)}'>{escaped(url)}</a></div>"
        )
    updated_html = (
        f"<div class='ff-updated'>Last updated from sources: {escaped(last_updated)}</div>"
        if last_updated
        else ""
    )

    with st.container(key=f"ff_assistant_{turn_id}"):
        with st.chat_message("assistant", avatar=None):
            st.markdown(
                f"<div class='ff-label {label_class}'>{label}</div>"
                f"<div class='ff-text'>{escaped(answer)}</div>"
                f"{source_html}{updated_html}",
                unsafe_allow_html=True,
            )


def _render_loading() -> None:
    with st.container(key="ff_assistant_pending"):
        with st.chat_message("assistant", avatar=None):
            st.markdown(
                "<div class='ff-dots' role='status' aria-label='Checking sources'>"
                "<span></span><span></span><span></span></div>",
                unsafe_allow_html=True,
            )


def _sync_send_button() -> None:
    st.iframe(
        """
<script>
(() => {
  const doc = window.parent.document;
  const composer = doc.querySelector('.st-key-ff_composer');
  if (!composer) return;
  const input = composer.querySelector('input');
  const button = composer.querySelector('[data-testid="stFormSubmitButton"] button');
  if (!input || !button) return;
  const sync = () => {
    const empty = !input.value.trim();
    button.disabled = empty;
    button.setAttribute('aria-disabled', String(empty));
    button.style.background = empty ? '#eaedff' : '#006c4f';
    button.style.color = empty ? '#8a8fa8' : '#ffffff';
    button.style.cursor = empty ? 'default' : 'pointer';
  };
  if (input._ffAbort) input._ffAbort.abort();
  const controller = new AbortController();
  input._ffAbort = controller;
  input.addEventListener('input', sync, {signal: controller.signal});
  input.addEventListener('change', sync, {signal: controller.signal});
  sync();
})();
</script>
""",
        width=1,
        height=1,
        alt="Send button state",
    )


def _render_composer(settings: Settings, max_chars: int) -> None:
    def _capture() -> None:
        question = str(st.session_state.get("question_input") or "").strip()
        st.session_state["question_input"] = ""
        if question:
            st.session_state["pending_question"] = question[:max_chars]

    with st.container(key="ff_composer"):
        with st.form("ask_form", clear_on_submit=False):
            input_col, send_col = st.columns([0.92, 0.08], vertical_alignment="center")
            with input_col:
                st.text_input(
                    "Ask about a fund",
                    key="question_input",
                    max_chars=max_chars,
                    placeholder="Ask about a fund...",
                    label_visibility="collapsed",
                )
            with send_col:
                submitted = st.form_submit_button(
                    "↑",
                    type="primary",
                    help="Send question",
                    width="stretch",
                    on_click=_capture,
                )
        if submitted and not st.session_state.get("pending_question"):
            # Fallback for browsers that submit the form without firing the
            # submit-button callback. The next run processes and clears it.
            question = str(st.session_state.get("question_input") or "").strip()
            if question:
                st.session_state["pending_question"] = question[:max_chars]
                st.rerun()
        _sync_send_button()


def _run_answer(settings: Settings, question: str, redacted: str) -> None:
    try:
        response = answer_question(question, settings)
        payload = response.model_dump(mode="json")
        stored_question = response.question_redacted or redacted or question
    except Exception:  # noqa: BLE001 - never expose a traceback or secrets in the UI
        payload = {
            "answer_type": "error",
            "answer": "The assistant could not complete that request. Please try again.",
        }
        stored_question = redacted or question

    history: list[dict[str, Any]] = st.session_state.setdefault("chat_history", [])
    history.append(
        {
            "id": str(payload.get("trace_id") or f"error_{len(history)}"),
            "question": stored_question,
            "response": payload,
        }
    )


def render(settings: Settings) -> None:
    max_chars = int(settings.data.get("app", {}).get("max_question_chars", 500))
    pending_question = st.session_state.pop("pending_question", None)
    if pending_question:
        pending_question = " ".join(str(pending_question).split())[:max_chars].strip()
        st.session_state["question_input"] = ""

    history: list[dict[str, Any]] = st.session_state.setdefault("chat_history", [])
    redacted = _redacted_question(settings, pending_question) if pending_question else ""

    with st.container(key="ff_chat"):
        if not history and not pending_question:
            with st.container(key="ff_hero_block"):
                _render_hero()
                _render_suggestions()

        _render_banner()

        for turn in history:
            turn_id = _safe_id(turn.get("id"))
            _render_user_message(turn_id, str(turn.get("question") or ""))
            _render_assistant_message(turn_id, turn.get("response") or {})

        if pending_question:
            _render_user_message("pending", redacted)
            _render_loading()

    _render_composer(settings, max_chars)

    if pending_question:
        _run_answer(settings, pending_question, redacted)
        st.rerun()
