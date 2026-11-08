"""Shared Streamlit presentation helpers."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src.config import Settings
from src.ui.artifacts import corpus_state, llm_state

BADGE_LABELS = {
    "amc": "AMC",
    "sebi": "SEBI",
    "amfi": "AMFI",
    "registrar": "Registrar",
}

STATUS_ICON = {
    "ready": "✅",
    "seed_only": "⚠️",
    "stale": "⛔",
    "missing": "⛔",
}


def inject_css() -> None:
    st.markdown(
        """
<style>
  .block-container {padding-top: 1.6rem; padding-bottom: 2.5rem; max-width: 1080px;}
  .mf-kicker {letter-spacing: .08em; text-transform: uppercase; font-size: .72rem;
              color: #6b7280; margin-bottom: .15rem;}
  .mf-subtle {color: #6b7280; font-size: .9rem;}
  .mf-answer-label {font-size: .76rem; letter-spacing: .08em; text-transform: uppercase;
                    color: #6b7280; margin-bottom: .25rem;}
  .mf-pill {display: inline-block; padding: .12rem .5rem; border-radius: 999px;
            border: 1px solid #d1d5db; font-size: .74rem; color: #374151;}
  @media (max-width: 480px) {
    .block-container {padding-left: 1rem; padding-right: 1rem; padding-top: 1rem;}
    h1 {font-size: 1.55rem !important;}
  }
</style>
""",
        unsafe_allow_html=True,
    )


def escaped(value: Any) -> str:
    return html.escape(str(value or ""))


def source_badge(source_type: str | None, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    return BADGE_LABELS.get(str(source_type or "").lower(), str(source_type or "Source"))


def answer_type_label(answer_type: str | None) -> str:
    return {
        "fact": "Fact",
        "refusal": "Refusal",
        "clarification": "Needs clarification",
        "no_answer": "No answer",
        "invalid": "Invalid question",
    }.get(str(answer_type or ""), "Answer")


def render_sidebar(settings: Settings) -> None:
    corpus = corpus_state(settings)
    llm = llm_state(settings)
    with st.sidebar:
        st.markdown("### System status")
        st.markdown(
            f"**Corpus** {STATUS_ICON.get(corpus['state'], '•')}  \n"
            f"<span class='mf-subtle'>{escaped(corpus['status'])} · "
            f"{corpus['count']} citable · {corpus['seed_count']} seed</span>",
            unsafe_allow_html=True,
        )
        if llm["mode"] == "extractive":
            st.warning(llm["label"], icon="⚠️")
            st.caption(llm["detail"])
        else:
            st.success(llm["label"], icon="✅")
            st.caption(llm["detail"])

        st.divider()
        if st.button("Clear session", width="stretch"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
        st.caption("No login · No uploads · No chat history")
        st.caption("Artifacts are read from local `data/` folders.")


def render_answer_card(response: dict[str, Any]) -> None:
    answer = response.get("answer") or "No answer was generated."
    citation = response.get("citation")
    answer_type = response.get("answer_type")
    badge = source_badge(
        citation.get("source_type") if citation else None,
        response.get("source_badge"),
    )

    with st.container(border=True):
        st.markdown(
            f"<div class='mf-answer-label'>{escaped(answer_type_label(answer_type))}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(answer)

        if citation and citation.get("url"):
            st.markdown(f"**Source badge:** <span class='mf-pill'>{escaped(badge)}</span>",
                        unsafe_allow_html=True)
            st.link_button(
                citation.get("title") or "Official source",
                citation.get("url") or "",
                width="content",
            )
        elif citation:
            st.caption("Citation has no approved URL.")
        elif answer_type == "clarification":
            st.caption("No citation yet — clarify the scheme or fact first.")
        elif answer_type == "invalid":
            st.caption("No source is needed for this input.")
        else:
            st.caption("Source unavailable — no approved official citation is configured yet.")

        st.caption(f"Last updated from sources: {response.get('last_updated') or '—'}")

    if response.get("fallback_used"):
        reason = str(response.get("fallback_reason") or "")
        normalized = reason.lower()
        if (
            normalized in {"missing_model", "missing_api_key", "llm_unavailable"}
            or normalized.startswith("llm_failed")
            or normalized.startswith("provider_error")
        ):
            st.info("Extractive answer — LLM unavailable")
        else:
            st.info("Extractive answer — validation fallback")
        if reason:
            st.caption(f"Fallback reason: `{reason}`")


def render_footer(settings: Settings) -> None:
    disclaimer = settings.data.get("app", {}).get(
        "disclaimer", "Facts-only. No investment advice."
    )
    st.divider()
    st.markdown(f"**{disclaimer}**")
    st.caption(
        "Answers are based on public official sources and are not a recommendation to buy, "
        "sell, or hold any scheme. Mutual fund investments are subject to market risks; "
        "read all scheme-related documents carefully."
    )
