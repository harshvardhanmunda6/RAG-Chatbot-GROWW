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
  .block-container {padding-top: 1.6rem; padding-bottom: 4.25rem; max-width: 1080px;}
  .mf-kicker {letter-spacing: .08em; text-transform: uppercase; font-size: .72rem;
              color: #6b7280; margin-bottom: .15rem;}
  .mf-subtle {color: #6b7280; font-size: .9rem;}
  .mf-answer-label {font-size: .76rem; letter-spacing: .08em; text-transform: uppercase;
                    color: #6b7280; margin-bottom: .25rem;}
  .mf-pill {display: inline-block; padding: .12rem .5rem; border-radius: 999px;
            border: 1px solid #d1d5db; font-size: .74rem; color: #374151;}
  .mf-source-line {border-top: 1px solid #e5e7eb; margin-top: .9rem; padding-top: .55rem;
                   color: #6b7280; font-size: .82rem; line-height: 1.5;}
  .mf-source-line a {color: #0f766e; text-decoration: none; font-weight: 600;}
  .mf-source-line a:hover {text-decoration: underline;}
  .mf-source-date {display: inline-block; margin-left: .3rem;}
  .mf-fixed-footer {position: fixed; left: 0; right: 0; bottom: 0; z-index: 999;
                    padding: .42rem 1rem; text-align: center; font-size: .78rem;
                    color: #4b5563; background: rgba(255,255,255,.96);
                    border-top: 1px solid #e5e7eb; backdrop-filter: blur(8px);}
  @media (max-width: 480px) {
    .block-container {padding-left: 1rem; padding-right: 1rem; padding-top: 1rem;
                      padding-bottom: 4rem;}
    h1 {font-size: 1.55rem !important;}
    .mf-source-line {font-size: .78rem;}
    .mf-fixed-footer {font-size: .74rem; padding: .35rem .75rem;}
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
        with st.expander("System status", expanded=False):
            if corpus["state"] == "ready":
                st.success(corpus["message"], icon="✅")
            elif corpus["state"] == "seed_only":
                st.warning(corpus["message"], icon="⚠️")
            else:
                st.error(corpus["message"], icon="⛔")
            st.caption(
                f"{STATUS_ICON.get(corpus['state'], '•')} {corpus['status']} · "
                f"{corpus['count']} citable · {corpus['seed_count']} seed"
            )

            st.divider()
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

        source_parts: list[str] = []
        if citation and citation.get("url"):
            source_parts.append(
                f"<span class='mf-pill'>{escaped(badge)}</span>"
            )
            source_parts.append(
                f"<a href='{escaped(citation.get('url'))}' target='_blank' "
                f"rel='noopener noreferrer'>"
                f"{escaped(citation.get('title') or 'Official source')}</a>"
            )
        elif citation:
            source_parts.append("Citation has no approved URL.")
        elif answer_type == "clarification":
            source_parts.append("No citation yet — clarify the scheme or fact first.")
        elif answer_type == "invalid":
            source_parts.append("No source is needed for this input.")
        else:
            source_parts.append(
                "Source unavailable — no approved official citation is configured yet."
            )

        source_parts.append(
            f"<span class='mf-source-date'>Last updated from sources: "
            f"{escaped(response.get('last_updated') or '—')}</span>"
        )
        st.markdown(
            "<div class='mf-source-line'>"
            + " <span aria-hidden='true'>·</span> ".join(source_parts)
            + "</div>",
            unsafe_allow_html=True,
        )

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
    st.caption(
        "Answers are based on public official sources and are not a recommendation to buy, "
        "sell, or hold any scheme. Mutual fund investments are subject to market risks; "
        "read all scheme-related documents carefully."
    )
    st.markdown(
        f"<div class='mf-fixed-footer'>{escaped(disclaimer)}</div>",
        unsafe_allow_html=True,
    )
