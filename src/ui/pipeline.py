"""Pipeline tab: read-only inspection of every stored RAG stage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.config import Settings
from src.ui.artifacts import (
    answer_artifact,
    clean_documents,
    corpus_state,
    file_age_label,
    format_bytes,
    format_timestamp,
    latest_answer,
    latest_retrieval_trace,
    prompt_artifact,
    raw_llm_artifact,
    read_json,
    read_jsonl,
    registry_rows,
    selected_rows,
    short_hash,
    sorted_files,
    source_checks,
    trace_rows,
    user_prompt_text,
    validation_rows,
)


def _metric_row(values: list[tuple[str, Any]]) -> None:
    columns = st.columns(len(values) or 1)
    for column, (label, value) in zip(columns, values):
        column.metric(label, value)


def _trace_options(settings: Settings) -> list[str]:
    return [
        path.stem
        for path in sorted_files(settings.path("retrieval"), "tr_*.json")
    ]


def _answer_options(settings: Settings) -> list[str]:
    return [
        path.stem for path in sorted_files(settings.path("answers"), "tr_*.json")
    ]


def _sources_stage(settings: Settings) -> None:
    st.subheader("1 · Source registry")
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
    _metric_row(
        [
            ("Sources", total),
            ("Approved", approved),
            ("Citable", citable),
            ("Seed", seed),
            ("Blocked", blocked),
        ]
    )
    st.caption(f"{official} official source rows configured")
    if errors:
        st.error("Source validation errors: " + "; ".join(errors))
    if rows:
        frame = pd.DataFrame(rows)
        st.dataframe(
            frame[
                [
                    "source_id",
                    "title",
                    "publisher",
                    "source_type",
                    "status",
                    "citable",
                    "purpose",
                    "url",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption("Seed rows are retained for cross-checking only and can never be cited.")
    else:
        st.info("No source registry rows found.")

    if checks:
        st.markdown("**Fetch checks**")
        frame = pd.DataFrame(checks)
        preferred = [
            column
            for column in [
                "source_id",
                "status",
                "http_status",
                "bytes",
                "raw_saved",
                "clean_saved",
                "fetched_at",
                "links_followed",
            ]
            if column in frame.columns
        ]
        st.dataframe(frame[preferred], width="stretch", hide_index=True)


def _load_clean_stage(settings: Settings) -> None:
    st.subheader("2 · Loading and cleaning")
    raw_dir = settings.path("raw")
    clean_dir = settings.path("clean")
    raw_files = (
        sorted(
            [
                path
                for path in raw_dir.glob("*")
                if path.suffix.lower() in {".html", ".pdf", ".htm"}
            ]
        )
        if raw_dir.exists()
        else []
    )
    meta_files = sorted(raw_dir.glob("*.meta.json")) if raw_dir.exists() else []
    clean_rows = clean_documents(settings)
    clean_files = sorted(clean_dir.glob("*.json")) if clean_dir.exists() else []

    _metric_row(
        [
            ("Raw files", len(raw_files)),
            ("Raw metadata", len(meta_files)),
            ("Clean JSON", len(clean_files)),
            ("Clean docs", len(clean_rows)),
        ]
    )

    checks = source_checks(settings)
    if checks:
        st.markdown("**Raw snapshots**")
        rows = []
        for row in checks:
            rows.append(
                {
                    "source_id": row.get("source_id", ""),
                    "status": row.get("status", ""),
                    "http": row.get("http_status"),
                    "bytes": format_bytes(row.get("bytes")),
                    "fetched_at": format_timestamp(row.get("fetched_at")),
                    "hash": short_hash(row.get("content_hash")),
                    "links_followed": row.get("links_followed", 0),
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    if clean_rows:
        st.markdown("**Clean documents**")
        frame = pd.DataFrame(clean_rows)
        st.dataframe(
            frame[
                [
                    "source_id",
                    "title",
                    "source_type",
                    "sections",
                    "chars",
                    "removed_sections",
                    "clean_text_hash",
                ]
            ],
            width="stretch",
            hide_index=True,
        )

        selected = st.selectbox(
            "Inspect a clean document",
            options=[row["source_id"] for row in clean_rows],
            key="pipeline_clean_doc",
        )
        chosen = next((row for row in clean_rows if row["source_id"] == selected), None)
        if chosen:
            payload = read_json(Path(chosen["path"]), {}) or {}
            sections = payload.get("sections", []) or []
            st.caption(
                f"{chosen['title']} · {len(sections)} sections · "
                f"{chosen['chars']:,} chars · hash {short_hash(chosen['clean_text_hash'])}"
            )
            if sections:
                section_rows = [
                    {
                        "order": section.get("order"),
                        "heading": section.get("heading", ""),
                        "chars": len(section.get("text", "") or ""),
                        "section_id": section.get("section_id", ""),
                    }
                    for section in sections
                ]
                st.dataframe(
                    pd.DataFrame(section_rows),
                    width="stretch",
                    hide_index=True,
                )
            meta_path = settings.path("raw") / f"{selected}.meta.json"
            meta = read_json(meta_path, None)
            if meta:
                with st.expander("Raw metadata (no page body)"):
                    st.json(meta)
    else:
        st.info("No clean documents found. Run `python3 -m src.cli ingest --stage clean`.")


def _chunks_stage(settings: Settings) -> None:
    st.subheader("3 · Chunks and facts")
    report = read_json(settings.path("chunks") / "chunk_report.json", {}) or {}
    chunks = read_jsonl(settings.path("chunks") / "chunks.jsonl")
    facts = read_jsonl(settings.path("chunks") / "facts.jsonl")
    token_dist = report.get("token_distribution", {}) or {}
    _metric_row(
        [
            ("Chunks", report.get("chunk_count", len(chunks))),
            ("Facts", report.get("fact_count", len(facts))),
            ("Max tokens", token_dist.get("max", "—")),
            ("Rejected sections", report.get("rejected_sections", "—")),
        ]
    )
    st.caption(
        f"Median {token_dist.get('median', '—')} tokens · p95 {token_dist.get('p95', '—')} · "
        f"hard cap 240 · chunker {report.get('chunker_version', '—')}"
    )

    coverage = report.get("coverage", {}) or {}
    if coverage:
        st.markdown("**Coverage matrix**")
        coverage_frame = pd.DataFrame.from_dict(coverage, orient="index").reset_index()
        coverage_frame = coverage_frame.rename(columns={"index": "scheme_slug"})
        st.dataframe(coverage_frame, width="stretch", hide_index=True)

    missing = report.get("missing_facts", []) or []
    if missing:
        st.markdown("**Explicitly missing facts**")
        st.dataframe(pd.DataFrame(missing), width="stretch", hide_index=True)

    if chunks:
        st.markdown("**Chunk sample**")
        labels = [
            f"{chunk.get('chunk_id')} · {chunk.get('scheme_slug')} · {chunk.get('fact_type')}"
            for chunk in chunks
        ]
        selected = st.selectbox(
            "Chunk", options=range(len(chunks)), format_func=lambda index: labels[index],
            key="pipeline_chunk",
        )
        chunk = chunks[selected]
        metadata = {
            key: chunk.get(key)
            for key in [
                "chunk_id",
                "source_id",
                "source_type",
                "citable",
                "scheme",
                "scheme_slug",
                "fact_type",
                "section",
                "page_type",
                "token_count",
                "char_count",
                "chunker_version",
                "content_hash",
            ]
        }
        left, right = st.columns([2, 1])
        with left:
            st.code(chunk.get("text", ""), language="text")
        with right:
            st.json(metadata)

    if facts:
        st.markdown("**Fact cards**")
        fact_rows = []
        for fact in facts:
            fact_rows.append(
                {
                    "scheme": fact.get("scheme", ""),
                    "fact_type": fact.get("fact_type", ""),
                    "value": fact.get("value_text") or "—",
                    "verified": fact.get("verified"),
                    "status": fact.get("status", ""),
                    "chunk_id": fact.get("chunk_id", ""),
                    "source_type": fact.get("source_type", ""),
                }
            )
        st.dataframe(
            pd.DataFrame(fact_rows), width="stretch", hide_index=True
        )


def _embeddings_stage(settings: Settings) -> None:
    st.subheader("4 · Embeddings")
    manifest = read_json(settings.path("embeddings") / "manifest.json", {}) or {}
    _metric_row(
        [
            ("Dimension", manifest.get("dimension", "—")),
            ("Chunks", manifest.get("chunk_count", "—")),
            ("Citable", manifest.get("citable_chunk_count", "—")),
            ("Cache hits", manifest.get("cache_hits", "—")),
        ]
    )
    st.caption(
        f"{manifest.get('embedding_model', '—')} · revision "
        f"{short_hash(manifest.get('resolved_revision'))} · "
        f"{manifest.get('normalization', '—')} · {manifest.get('dtype', '—')}"
    )
    st.caption(
        f"Manifest {short_hash(manifest.get('manifest_hash'))} · "
        f"corpus {short_hash(manifest.get('corpus_hash'))} · "
        f"config {short_hash(manifest.get('config_hash'))}"
    )
    files = [
        settings.path("embeddings") / "embeddings.npy",
        settings.path("embeddings") / "embeddings_citable.npy",
        settings.path("embeddings") / "chunk_id_map.json",
        settings.path("embeddings") / "chunk_id_map_citable.json",
    ]
    st.markdown("**Artifact files**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "file": path.name,
                    "exists": path.exists(),
                    "size": format_bytes(path.stat().st_size) if path.exists() else "—",
                    "modified": file_age_label(path),
                }
                for path in files
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    with st.expander("Manifest"):
        st.json(manifest)


def _chroma_stage(settings: Settings, corpus: dict[str, Any]) -> None:
    st.subheader("5 · Chroma collection")
    report = corpus["report"]
    _metric_row(
        [
            ("Citable", corpus["count"]),
            ("Seed inspection", corpus["seed_count"]),
            ("Distance", report.get("distance", "—")),
            ("Smoke", "passed" if report.get("smoke_passed") else "not run"),
        ]
    )
    st.caption(
        f"Main `{corpus['collection_name']}` · status `{corpus['status']}` · "
        f"manifest {short_hash(corpus['manifest_hash'])}"
    )
    if corpus["state"] != "ready":
        st.warning(corpus["message"], icon="⚠️")
    warnings = report.get("warnings", []) or []
    for warning in warnings:
        st.info(warning, icon="ℹ️")

    smoke = report.get("smoke_queries", []) or []
    if smoke:
        rows = []
        for item in smoke:
            rows.append(
                {
                    "scheme_slug": item.get("scheme_slug", ""),
                    "fact_type": item.get("fact_type", ""),
                    "expected": item.get("expected_chunk_id", ""),
                    "returned": ", ".join(item.get("returned_chunk_ids", []) or []),
                    "passed": item.get("passed"),
                    "mode": item.get("mode", ""),
                }
            )
        st.markdown("**Smoke queries**")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    with st.expander("Collection report"):
        st.json(report)


def _retrieval_stage(settings: Settings) -> None:
    st.subheader("6 · Retrieval trace")
    options = _trace_options(settings)
    if not options:
        st.info("No retrieval traces yet. Ask a question or run `python3 -m src.cli retrieve ...`.")
        return
    selected = st.selectbox("Trace", options=options, key="pipeline_trace")
    trace = read_json(settings.path("retrieval") / f"{selected}.json", {}) or {}
    gate = trace.get("evidence_gate", {}) or {}
    _metric_row(
        [
            ("Intent", trace.get("intent", "—")),
            ("Candidates", len(trace.get("candidates", []) or [])),
            ("Selected", len(trace.get("selected", []) or [])),
            ("Gate", "passed" if gate.get("passed") else gate.get("reason", "—")),
        ]
    )
    st.caption(
        f"Scheme `{trace.get('scheme') or '—'}` · fact `{trace.get('fact_type') or '—'}` · "
        f"question: {trace.get('question_redacted', '—')}"
    )
    st.caption(
        f"Timings {trace.get('timings_ms', {})} · manifest "
        f"{short_hash(trace.get('manifest_hash'))}"
    )

    filters = trace.get("filters") or {}
    if filters:
        st.markdown("**Primary filter**")
        st.json(filters)
    attempts = trace.get("filter_attempts") or []
    if attempts:
        st.markdown("**Fallback attempts**")
        st.dataframe(pd.DataFrame(attempts), width="stretch", hide_index=True)

    rows = trace_rows(trace)
    if rows:
        st.markdown("**Candidates**")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("No candidates were retrieved for this trace.")

    selected_rows_data = selected_rows(trace)
    if selected_rows_data:
        st.markdown("**Selected evidence**")
        for row in selected_rows_data:
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
    with st.expander("Trace JSON"):
        st.json(trace)


def _generation_stage(settings: Settings) -> None:
    st.subheader("7 · Prompt, LLM, validation, and answer")
    options = _answer_options(settings)
    if not options:
        st.info(
            "No answer artifacts yet. Ask a question in the Ask tab or run "
            "`python3 -m src.cli answer ...`."
        )
        return
    selected = st.selectbox("Answer trace", options=options, key="pipeline_answer")
    answer = answer_artifact(settings, selected)
    if not answer:
        st.warning("Answer artifact could not be read.")
        return
    trace = read_json(settings.path("retrieval") / f"{selected}.json", {}) or {}
    prompt = prompt_artifact(settings, selected)
    raw = raw_llm_artifact(settings, selected)

    validation = answer.get("validation", {}) or {}
    _metric_row(
        [
            ("Answer type", answer.get("answer_type", "—")),
            ("Validation", "passed" if validation.get("passed") else "failed"),
            ("Fallback", "yes" if answer.get("fallback_used") else "no"),
            ("Prompt hash", "present" if answer.get("prompt_hash") else "—"),
        ]
    )
    st.caption(
        f"Provider `{answer.get('provider') or '—'}` · model `{answer.get('model') or '—'}` · "
        f"last updated {answer.get('last_updated') or '—'}"
    )
    if answer.get("fallback_reason"):
        st.caption(f"Fallback reason: `{answer['fallback_reason']}`")
    if answer.get("answer"):
        with st.container(border=True):
            st.markdown(answer["answer"])
    citation = answer.get("citation")
    if citation:
        st.caption(
            f"Citation: {citation.get('title')} · {citation.get('source_type')} · "
            f"{citation.get('last_updated')}"
        )
    else:
        st.caption("Citation: none recorded for this answer.")

    checks = validation_rows(answer)
    if checks:
        st.markdown("**Validation checks**")
        st.dataframe(pd.DataFrame(checks), width="stretch", hide_index=True)

    if prompt:
        st.markdown("**User prompt and evidence**")
        st.caption("System prompt hidden. Prompt hash: `" + str(answer.get("prompt_hash") or "—") + "`")
        st.code(user_prompt_text(prompt), language="text")
    else:
        st.info("No prompt artifact exists for this trace.")
    if raw:
        responses = raw.get("responses", []) or []
        st.markdown("**Raw provider output**")
        if responses:
            st.code(str(responses[-1]), language="text")
        else:
            st.info("No raw provider text was recorded.")
    with st.expander("Answer artifact"):
        st.json(answer)
    if trace:
        st.caption(
            f"Retrieval gate: `{(trace.get('evidence_gate') or {}).get('reason', '—')}` · "
            f"selected: {trace.get('selected', [])}"
        )


def render(settings: Settings) -> None:
    st.markdown("<div class='mf-kicker'>Read-only artifacts</div>", unsafe_allow_html=True)
    st.title("Pipeline inspector")
    st.caption(
        "Every stage is read from local `data/` artifacts. This tab never calls Chroma, "
        "the embedding model, or the LLM."
    )

    corpus = corpus_state(settings)
    latest_trace = latest_retrieval_trace(settings)
    latest_answer_row = latest_answer(settings)
    _metric_row(
        [
            ("Citable chunks", corpus["count"]),
            ("Seed chunks", corpus["seed_count"]),
            ("Latest trace", latest_trace.get("trace_id", "—") if latest_trace else "—"),
            ("Latest answer", latest_answer_row.get("answer_type", "—") if latest_answer_row else "—"),
        ]
    )
    if corpus["state"] != "ready":
        st.warning(corpus["message"], icon="⚠️")

    sources_tab, load_tab, chunks_tab, embeddings_tab, chroma_tab, retrieval_tab, generation_tab = (
        st.tabs(
            [
                "Sources",
                "Load & clean",
                "Chunks & facts",
                "Embeddings",
                "Chroma",
                "Retrieval",
                "Generation",
            ]
        )
    )
    with sources_tab:
        _sources_stage(settings)
    with load_tab:
        _load_clean_stage(settings)
    with chunks_tab:
        _chunks_stage(settings)
    with embeddings_tab:
        _embeddings_stage(settings)
    with chroma_tab:
        _chroma_stage(settings, corpus)
    with retrieval_tab:
        _retrieval_stage(settings)
    with generation_tab:
        _generation_stage(settings)
