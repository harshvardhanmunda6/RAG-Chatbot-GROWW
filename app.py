"""Streamlit entry point for the FundFacts mutual fund Q&A interface.

Run with:

    streamlit run app.py

The UI never exposes API keys, the system prompt, or raw PII. It stores only the
current chat turns in session state and reads stage artifacts from data/.
"""

from __future__ import annotations

import streamlit as st

from src.config import Settings, load_settings
from src.ui import ask, evaluation, pipeline, sources
from src.ui.common import inject_css, render_sidebar


@st.cache_resource(show_spinner=False)
def get_settings() -> Settings:
    return load_settings()


def main() -> None:
    st.set_page_config(
        page_title="FundFacts",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="auto",
    )
    inject_css()
    settings = get_settings()
    view = render_sidebar(settings)

    if view == "Pipeline":
        pipeline.render(settings)
    elif view == "Sources":
        sources.render(settings)
    elif view == "Evaluation":
        evaluation.render(settings)
    else:
        ask.render(settings)


if __name__ == "__main__":
    main()
