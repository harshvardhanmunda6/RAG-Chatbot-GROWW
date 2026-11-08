"""Sources tab: registry, fetch status, and fact coverage."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import Settings
from src.ui.artifacts import (
    format_bytes,
    format_timestamp,
    read_json,
    registry_rows,
    short_hash,
    source_checks,
)


def render(settings: Settings) -> None:
    st.markdown("<div class='mf-kicker'>Provenance</div>", unsafe_allow_html=True)
    st.title("Sources")
    st.markdown(
        "Only approved official sources can be cited. Groww rows are **seed/cross-check** "
        "pages: they are stored for comparison and are never used as answer citations."
    )

    rows, errors = registry_rows(settings)
    checks = source_checks(settings)
    total = len(rows)
    official = sum(
        1 for row in rows if row.get("source_type") in {"amc", "sebi", "amfi", "registrar"}
    )
    approved = sum(1 for row in rows if row.get("status") == "approved")
    citable = sum(1 for row in rows if row.get("citable"))
    seed = sum(1 for row in rows if row.get("source_type") == "seed")
    blocked = sum(1 for row in checks if row.get("status") == "blocked")

    columns = st.columns(5)
    columns[0].metric("Total", total)
    columns[1].metric("Approved", approved)
    columns[2].metric("Citable", citable)
    columns[3].metric("Seed", seed)
    columns[4].metric("Blocked", blocked)
    st.caption(f"{official} official source rows configured · {citable} citable in answers")

    if errors:
        st.error("Registry validation errors: " + "; ".join(errors))
    if not official:
        st.warning(
            "No official citable sources are configured yet. The assistant can still refuse "
            "advice/performance/PII and show retrieval traces, but factual answers cannot cite "
            "a source until official rows are added.",
            icon="⚠️",
        )

    if rows:
        frame = pd.DataFrame(rows)
        st.markdown("**Registry rows**")
        st.dataframe(
            frame[
                [
                    "source_id",
                    "title",
                    "publisher",
                    "source_type",
                    "status",
                    "citable",
                    "scheme_slugs",
                    "purpose",
                    "url",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Citable rows must be `approved`, `https`, allowlisted, and backed by a raw snapshot."
        )
    else:
        st.info("No source rows found in `data/00_sources/sources.csv`.")

    if checks:
        st.markdown("**Fetch checks**")
        fetch_rows = []
        for row in checks:
            fetch_rows.append(
                {
                    "source_id": row.get("source_id", ""),
                    "status": row.get("status", ""),
                    "http": row.get("http_status"),
                    "final_url": row.get("final_url", ""),
                    "bytes": format_bytes(row.get("bytes")),
                    "fetched_at": format_timestamp(row.get("fetched_at")),
                    "raw_saved": row.get("raw_saved"),
                    "clean_saved": row.get("clean_saved"),
                    "links_followed": row.get("links_followed", 0),
                    "content_hash": short_hash(row.get("content_hash")),
                }
            )
        st.dataframe(
            pd.DataFrame(fetch_rows), width="stretch", hide_index=True
        )

    report = read_json(settings.path("chunks") / "chunk_report.json", {}) or {}
    coverage = report.get("coverage", {}) or {}
    if coverage:
        st.markdown("**Fact coverage**")
        coverage_frame = pd.DataFrame.from_dict(coverage, orient="index").reset_index()
        coverage_frame = coverage_frame.rename(columns={"index": "scheme_slug"})
        st.dataframe(coverage_frame, width="stretch", hide_index=True)
        st.caption(
            "`seed_only` facts were found only in non-citable rows. `missing` facts are explicit; "
            "the assistant must not infer them."
        )

    missing = report.get("missing_facts", []) or []
    if missing:
        st.markdown("**Missing required facts**")
        st.dataframe(pd.DataFrame(missing), width="stretch", hide_index=True)
