"""Streamlit entry point: Ask, Pipeline, Sources, and Evaluation tabs.

Run with:

    streamlit run app.py

The UI never exposes API keys, the system prompt, or raw PII. It stores only the
current question/answer in session state and reads stage artifacts from data/.
"""

from __future__ import annotations

import streamlit as st

from src.config import Settings, load_settings
from src.ui import ask, evaluation, pipeline, sources
from src.ui.common import inject_css, render_footer, render_sidebar


@st.cache_resource(show_spinner=False)
def get_settings() -> Settings:
    return load_settings()


def main() -> None:
    st.set_page_config(
        page_title="HDFC Mutual Fund Facts Assistant",
        page_icon="📄",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    inject_css()
    settings = get_settings()
    render_sidebar(settings)

    ask_tab, pipeline_tab, sources_tab, evaluation_tab = st.tabs(
        ["Ask", "Pipeline", "Sources", "Evaluation"]
    )
    with ask_tab:
        ask.render(settings)
    with pipeline_tab:
        pipeline.render(settings)
    with sources_tab:
        sources.render(settings)
    with evaluation_tab:
        evaluation.render(settings)

    render_footer(settings)


if __name__ == "__main__":
    main()
