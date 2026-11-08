"""Evaluation tab: gold-set results and metrics when artifacts exist."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from src.config import Settings
from src.ui.artifacts import read_json, read_jsonl


def _as_metrics(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _metric_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render(settings: Settings) -> None:
    st.markdown("<div class='mf-kicker'>Quality checks</div>", unsafe_allow_html=True)
    st.title("Evaluation")
    st.caption(
        "Evaluation artifacts are read from `data/08_eval/`. The Ask and Pipeline tabs work "
        "even when no gold set has been run."
    )

    eval_dir = settings.path("evaluation")
    metrics = _as_metrics(read_json(eval_dir / "metrics.json", {}))
    results = read_jsonl(eval_dir / "results.jsonl")
    gold_set = read_jsonl(eval_dir / "gold_set.jsonl")

    if not metrics and not results and not gold_set:
        st.info(
            "No evaluation artifacts are present yet. Expected files are "
            "`data/08_eval/gold_set.jsonl`, `results.jsonl`, and `metrics.json`.",
            icon="ℹ️",
        )
        st.markdown(
            "The evaluation harness is a separate build step. Until it runs, use the "
            "Pipeline tab to inspect the same retrieval, validation, and answer artifacts."
        )
        return

    columns = st.columns(4)
    columns[0].metric("Gold questions", len(gold_set))
    columns[1].metric("Results", len(results))
    columns[2].metric("Metrics", len(metrics))
    columns[3].metric("Artifacts", "available")

    if metrics:
        st.markdown("**Metrics**")
        metric_rows = [
            {"metric": key, "value": _metric_value(value)}
            for key, value in sorted(metrics.items())
        ]
        st.dataframe(
            pd.DataFrame(metric_rows), width="stretch", hide_index=True
        )
        with st.expander("Raw metrics"):
            st.json(metrics)

    if results:
        st.markdown("**Results**")
        frame = pd.DataFrame(results)
        st.dataframe(frame, width="stretch", hide_index=True)
        if "passed" in frame.columns:
            passed = int(frame["passed"].fillna(False).sum())
            st.caption(f"{passed} of {len(frame)} recorded results passed.")
    elif gold_set:
        st.markdown("**Gold set**")
        st.dataframe(
            pd.DataFrame(gold_set), width="stretch", hide_index=True
        )
        st.info("Gold set loaded, but no `results.jsonl` has been written yet.")
