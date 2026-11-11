"""Shared Streamlit presentation helpers for the FundFacts prototype."""

from __future__ import annotations

import base64
import html
from typing import Any

import streamlit as st

from src.config import PROJECT_ROOT, Settings
from src.ui.artifacts import (
    corpus_state,
    llm_state,
    registry_rows,
)

TOPICS = [
    ("💸 Expense ratio", "What is the expense ratio of HDFC Large Cap Fund?"),
    ("🚪 Exit load", "What is the exit load of HDFC Mid Cap Fund?"),
    ("📅 Minimum SIP", "What is the minimum SIP of HDFC ELSS Tax Saver?"),
    ("🔒 ELSS lock-in", "What is the lock-in for HDFC ELSS Tax Saver?"),
    ("🌡️ Riskometer", "What is the riskometer rating of HDFC Small Cap Fund?"),
    ("📊 Benchmark", "What is the benchmark of HDFC Flexi Cap?"),
    ("🏆 Top holdings", "What are the top holdings of HDFC Large Cap Fund?"),
    ("👤 Fund manager", "Who is the fund manager of HDFC Mid Cap Fund?"),
    ("🏢 About HDFC Mutual Fund", "Who is the CEO of HDFC Mutual Fund?"),
]

SUGGESTIONS = [
    ("🔍 What is the lock-in for HDFC ELSS Tax Saver?", "What is the lock-in for HDFC ELSS Tax Saver?"),
    ("％ What is the exit load of HDFC Flexi Cap?", "What is the exit load of HDFC Flexi Cap?"),
    ("🛡️ Should I invest in HDFC Mid Cap right now?", "Should I invest in HDFC Mid Cap right now?"),
]

_STATUS_ICONS = {
    "ready": "✅",
    "seed_only": "⚠️",
    "stale": "⛔",
    "missing": "⛔",
}


def _logo_data_uri() -> str | None:
    """Return the optional local logo as a data URI so it works in sidebar HTML."""
    assets = PROJECT_ROOT / "assets"
    for name, mime in (
        ("logo.svg", "image/svg+xml"),
        ("logo.png", "image/png"),
        ("logo.jpg", "image/jpeg"),
        ("logo.jpeg", "image/jpeg"),
        ("logo.webp", "image/webp"),
    ):
        path = assets / name
        if path.is_file():
            try:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError:
                continue
            return f"data:{mime};base64,{encoded}"
    return None


def inject_css() -> None:
    """Inject the single stylesheet used by the FundFacts interface."""
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Plus+Jakarta+Sans:wght@600;700&display=swap');

:root {
  --ff-surface: #faf8ff;
  --ff-low: #f2f3ff;
  --ff-mid: #eaedff;
  --ff-ink: #131b2e;
  --ff-muted: #3c4a43;
  --ff-primary: #006c4f;
  --ff-accent: #00b386;
  --ff-link: #005ac2;
  --ff-err: #ba1a1a;
  --ff-notfound: #004395;
  --ff-card: #ffffff;
}

html, body, .stApp, button, input, textarea, [class*="css"] {
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
}
.stApp, [data-testid="stAppViewContainer"], .stMain {
  background: var(--ff-surface);
  color: var(--ff-ink);
}
[data-testid="stHeader"] {background: transparent;}
[data-testid="stToolbar"], #MainMenu, footer,
[data-testid="stDecoration"], [data-testid="stStatusWidget"] {
  display: none !important;
  visibility: hidden !important;
}
[data-testid="stAppViewBlockContainer"], .stMainBlockContainer {
  max-width: 768px;
  margin: 0 auto;
  padding: 1.5rem 1rem 8.5rem;
}

/* Sidebar */
[data-testid="stSidebar"] {
  width: 300px !important;
  min-width: 300px !important;
  max-width: 300px !important;
  background: var(--ff-low);
  border-right: 0;
}
[data-testid="stSidebarHeader"] {
  height: 0;
  min-height: 0;
  padding: 0;
}
[data-testid="stSidebarCollapseButton"] {display: none;}
[data-testid="stSidebarContent"] {padding: 0;}
[data-testid="stSidebarUserContent"] {padding: 1.1rem;}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap: .45rem;}
[data-testid="stSidebarResizeHandle"] {display: none !important;}
.ff-brand {
  display: flex;
  align-items: center;
  gap: .6rem;
  margin-bottom: .5rem;
}
.ff-logo {
  width: 42px;
  height: 42px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.ff-logo img {display: block; width: 100%; height: 100%; object-fit: contain;}
.ff-logo-placeholder {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: var(--ff-accent);
  color: #fff;
  font-size: 1.4rem;
  font-weight: 700;
  line-height: 1;
}
.ff-brand b {
  display: block;
  font: 700 1.15rem/1.2 'Plus Jakarta Sans', sans-serif;
  color: var(--ff-primary);
}
.ff-brand span {font-size: .72rem; color: var(--ff-muted);}

[data-testid="stSidebar"] .stButton button {
  background: #fff;
  border: 0 !important;
  border-radius: 999px;
  padding: .42rem .8rem;
  min-height: 34px;
  height: auto;
  font-size: .82rem;
  font-weight: 400;
  color: var(--ff-ink);
  box-shadow: none;
  text-align: left;
  justify-content: flex-start;
  transition: background .15s, color .15s, transform .15s;
}
[data-testid="stSidebar"] .stButton button p {
  margin: 0;
  font-size: .82rem;
  font-weight: 400;
  line-height: 1.2;
  color: inherit !important;
}
[data-testid="stSidebar"] .stButton button:hover {
  background: var(--ff-mid);
  color: var(--ff-primary);
  transform: translateY(-1px);
  border: 0 !important;
}
[data-testid="stSidebar"] .stButton button:focus:not(:active) {
  border: 0 !important;
  color: var(--ff-primary);
}
.ff-stats {display: flex; gap: .5rem; margin: .2rem 0;}
.ff-stats div {
  flex: 1;
  background: #fff;
  border-radius: .8rem;
  padding: .55rem .6rem;
  font-size: .72rem;
  line-height: 1.2;
  color: var(--ff-muted);
}
.ff-stats b {
  display: block;
  font: 700 1.35rem/1.2 'Plus Jakarta Sans', sans-serif;
  color: var(--ff-primary);
}
.ff-section-title {
  font-size: .68rem;
  font-weight: 600;
  letter-spacing: .06em;
  color: var(--ff-muted);
  margin: .6rem 0 .05rem;
}
.ff-note {font-size: .78rem; line-height: 1.7;}
[data-testid="stSidebar"] [data-testid="stExpander"] {
  border: 0;
  background: transparent;
  margin-top: .6rem;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  font-size: .7rem;
  color: var(--ff-muted);
  padding: .25rem 0;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {color: var(--ff-primary);}

/* Main column */
.st-key-ff_chat {max-width: 768px; margin: 0 auto;}
.st-key-ff_chat > [data-testid="stVerticalBlock"] {gap: 1rem;}
.st-key-ff_hero_block > [data-testid="stVerticalBlock"] {gap: 0;}
.ff-hero {text-align: center; padding: 1rem 0 .25rem;}
.ff-hero h1 {
  margin: 0;
  color: var(--ff-ink);
  font: 700 2.1rem/1.2 'Plus Jakarta Sans', sans-serif;
  letter-spacing: -.02em;
}
.ff-hero p {
  margin: .45rem 0 1rem;
  color: var(--ff-muted);
  font-size: .95rem;
}
.st-key-ff_suggestions {margin-top: .6rem;}
.st-key-ff_suggestions > [data-testid="stVerticalBlock"] {gap: .6rem;}
.st-key-ff_suggestions .stButton button {
  display: block;
  width: 100%;
  background: var(--ff-low);
  border: 0 !important;
  border-radius: 14px;
  padding: .7rem 1rem;
  min-height: 0;
  height: auto;
  font-size: .88rem;
  font-weight: 400;
  color: var(--ff-ink);
  text-align: left;
  justify-content: flex-start;
  box-shadow: none;
  transition: background .15s, color .15s;
}
.st-key-ff_suggestions .stButton button p {
  margin: 0;
  font-size: .88rem;
  font-weight: 400;
  line-height: 1.35;
  color: inherit !important;
}
.st-key-ff_suggestions .stButton button:hover {
  background: var(--ff-mid);
  color: var(--ff-primary);
  border: 0 !important;
}
.ff-banner {
  background: linear-gradient(90deg, #e2e7ff, #d8f3f0);
  border-radius: 1rem;
  padding: .8rem 1.1rem;
  font-size: .84rem;
  line-height: 1.5;
  color: var(--ff-ink);
}
.ff-banner b {font-weight: 600;}

/* Chat */
[data-testid="stChatMessage"] {
  gap: 0;
  padding: 0;
  margin: 0;
  background: transparent;
}
[data-testid="stChatMessage"] [data-testid^="stChatMessageAvatar"] {display: none !important;}
[data-testid="stChatMessageContent"] {
  padding: 0;
  gap: 0;
  border-radius: 1rem;
  background: transparent;
}
[class*="st-key-ff_user_"] [data-testid="stChatMessage"] {
  flex-direction: row;
  justify-content: flex-end;
}
[class*="st-key-ff_user_"] [data-testid="stChatMessageContent"] {
  display: block;
  max-width: 85%;
  margin-left: auto;
  background: #e4f6ef;
  border-radius: 1rem;
  padding: .85rem 1rem;
}
[class*="st-key-ff_user_"] [data-testid="stMarkdownContainer"] p {margin: 0;}
.ff-user-text {
  color: var(--ff-ink);
  font-size: .95rem;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
[class*="st-key-ff_assistant_"] [data-testid="stChatMessageContent"] {
  width: 100%;
  background: #fff;
  border-radius: 1rem;
  padding: .85rem 1rem;
  box-shadow: 0 2px 10px rgba(19, 27, 46, .04);
  animation: ff-rise .22s ease-out;
}
.ff-label {font-weight: 600; font-size: .8rem; margin-bottom: .2rem;}
.ff-label.answer {color: var(--ff-primary);}
.ff-label.refusal, .ff-label.error {color: var(--ff-err);}
.ff-label.not_found {color: var(--ff-notfound);}
.ff-text {
  color: var(--ff-ink);
  font-size: .95rem;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.ff-source {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: .5rem;
  background: var(--ff-low);
  border-radius: .6rem;
  padding: .3rem .7rem;
  font-size: .8rem;
}
.ff-source a {color: var(--ff-link); text-decoration: none;}
.ff-source a:hover {text-decoration: underline;}
.ff-updated {color: var(--ff-muted); font-size: .74rem; margin-top: .35rem;}
.ff-dots span {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 4px;
  border-radius: 50%;
  background: var(--ff-accent);
  animation: ff-pulse 1s infinite;
}
.ff-dots span:nth-child(2) {animation-delay: .15s;}
.ff-dots span:nth-child(3) {animation-delay: .3s;}
@keyframes ff-rise {
  from {opacity: 0; transform: translateY(6px);}
  to {opacity: 1; transform: none;}
}
@keyframes ff-pulse {
  50% {opacity: .3; transform: scale(.8);}
}

/* Composer */
.st-key-ff_composer {
  position: fixed;
  bottom: 0;
  left: calc(50% + 150px);
  transform: translateX(-50%);
  width: min(768px, calc(100vw - 300px - 2rem));
  padding: .6rem 0 1rem;
  background: var(--ff-surface);
  z-index: 1000;
}
.st-key-ff_composer [data-testid="stForm"] {
  border: 0;
  border-radius: 1rem;
  background: #fff;
  box-shadow: 0 8px 24px rgba(0, 108, 79, .12);
  padding: .55rem .6rem .55rem 1rem;
}
.st-key-ff_composer [data-testid="stForm"]:focus-within {
  box-shadow: 0 0 0 2px var(--ff-primary), 0 8px 24px rgba(0, 108, 79, .12);
}
.st-key-ff_composer [data-testid="stHorizontalBlock"] {align-items: center; gap: .5rem;}
.st-key-ff_composer [data-testid="stTextInputRootElement"],
.st-key-ff_composer [data-testid="stTextInputField"] {
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}
.st-key-ff_composer input {
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  color: var(--ff-ink) !important;
  font-size: .95rem;
  line-height: 1.4;
  padding: .35rem 0 !important;
}
.st-key-ff_composer input::placeholder {color: var(--ff-muted);}
.st-key-ff_composer [data-testid="stFormSubmitButton"] {display: flex; justify-content: flex-end;}
.st-key-ff_composer [data-testid="stFormSubmitButton"] button {
  width: 38px;
  height: 38px;
  min-height: 38px;
  padding: 0;
  border: 0;
  border-radius: .7rem;
  background: var(--ff-primary);
  color: #fff;
  font-size: 1.05rem;
  font-weight: 600;
  box-shadow: none;
}
.st-key-ff_composer [data-testid="stFormSubmitButton"] button p {
  margin: 0;
  color: #fff !important;
  font-size: 1.05rem;
}
.st-key-ff_composer [data-testid="stFormSubmitButton"] button:hover {
  background: #005a42;
  color: #fff;
}
.st-key-ff_composer [data-testid="stIFrame"] {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

@media (prefers-reduced-motion: reduce) {
  * {animation: none !important; transition: none !important; scroll-behavior: auto !important;}
}

/* Mobile */
@media (max-width: 767px) {
  [data-testid="stSidebar"] {
    width: min(86vw, 300px) !important;
    min-width: min(86vw, 300px) !important;
    max-width: min(86vw, 300px) !important;
  }
  [data-testid="stSidebarHeader"] {
    height: auto;
    min-height: auto;
    padding: .55rem .55rem 0;
  }
  [data-testid="stSidebarCollapseButton"] {display: flex;}
  [data-testid="stExpandSidebarButton"] {
    width: 40px;
    height: 40px;
    border-radius: .7rem;
    background: var(--ff-low);
    color: var(--ff-ink);
    margin: .6rem 0 0 .75rem;
  }
  .stMainBlockContainer {
    padding: 3.6rem .9rem 7.5rem;
  }
  .st-key-ff_composer {
    left: 50%;
    width: calc(100vw - 2rem);
    padding: .5rem 0 .75rem;
  }
  .ff-hero h1 {font-size: 1.6rem;}
  .ff-hero p {font-size: .9rem;}
  .ff-source {max-width: 100%;}
}
</style>
""",
        unsafe_allow_html=True,
    )


def escaped(value: Any) -> str:
    return html.escape(str(value or ""))


def _brand_markup() -> str:
    logo = _logo_data_uri()
    if logo:
        logo_markup = f"<div class='ff-logo'><img src='{logo}' alt='FundFacts logo'/></div>"
    else:
        logo_markup = "<div class='ff-logo ff-logo-placeholder'>f</div>"
    return (
        f"<div class='ff-brand'>{logo_markup}"
        "<div><b>FundFacts</b><span>Mutual fund assistant</span></div></div>"
    )


def _sidebar_stats(settings: Settings) -> str:
    rows, _ = registry_rows(settings)
    schemes = set()
    for row in rows:
        for slug in str(row.get("scheme_slugs") or "").split(","):
            slug = slug.strip()
            if slug and slug != "hdfc-mutual-fund":
                schemes.add(slug)
    fund_count = len(schemes)
    source_count = len(rows)
    return (
        "<div class='ff-stats'>"
        f"<div><b>{fund_count}</b>HDFC funds</div>"
        f"<div><b>{source_count}</b>Public sources</div>"
        "</div>"
    )


def render_sidebar(settings: Settings) -> str:
    """Render the FundFacts sidebar and return the selected developer view."""
    with st.sidebar:
        st.markdown(_brand_markup(), unsafe_allow_html=True)

        if st.button("✨ New chat", key="new_chat", width="stretch"):
            st.session_state["chat_history"] = []
            st.session_state.pop("pending_question", None)
            st.session_state["question_input"] = ""
            st.session_state["app_view"] = "Chat"
            st.rerun()

        st.markdown(_sidebar_stats(settings), unsafe_allow_html=True)
        st.markdown("<div class='ff-section-title'>EXPLORE A TOPIC</div>", unsafe_allow_html=True)
        for index, (label, question) in enumerate(TOPICS):
            if st.button(label, key=f"topic_{index}", width="stretch"):
                st.session_state["pending_question"] = question
                st.rerun()

        st.markdown("<div class='ff-section-title'>WHY TRUST IT</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='ff-note'>"
            "✓ Answers only from public sources<br>"
            "✓ A source link with every answer<br>"
            "✓ Shows when sources were last updated<br>"
            "✓ Never gives investment advice"
            "</div>",
            unsafe_allow_html=True,
        )

        corpus = corpus_state(settings)
        llm = llm_state(settings)
        with st.expander("System status", expanded=False):
            if corpus["state"] == "ready":
                st.success(corpus["message"], icon="✅")
            elif corpus["state"] == "seed_only":
                st.warning(corpus["message"], icon="⚠️")
            else:
                st.error(corpus["message"], icon="⛔")
            st.caption(
                f"{_STATUS_ICONS.get(corpus['state'], '•')} {corpus['status']} · "
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
            st.radio(
                "Developer view",
                ["Chat", "Pipeline", "Sources", "Evaluation"],
                key="app_view",
                label_visibility="collapsed",
            )

    return str(st.session_state.get("app_view", "Chat"))
