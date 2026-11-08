"""CLI for the data-loading and retrieval phases.

Commands:
    check
    validate-sources
    load
    clean
    chunk
    embed
    index
    smoke
    retrieve "question"
    answer "question"
    ingest --stage load|clean|chunk|embed|index|all
    report
    clean-logs
"""

from __future__ import annotations

import argparse
import json
import sys
from src.chunker import chunk_all, load_clean_documents, write_artifacts
from src.cleaner import clean_all
from src.embedder import embed_chunks, load_chunks, write_embedding_artifacts
from src.config import Settings, load_settings
from src.evaluator import run_evaluation
from src.providers import resolve_provider
from src.retriever import retrieve
from src.service import answer_question
from src.vector_store import build_vector_store, run_smoke
from src.loader import load_all, read_snapshot_meta
from src.source_registry import is_url_allowed, load_registry


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def _write_checks(settings: Settings, source_errors: list[str]) -> None:
    rows = []
    registry_path = settings.root / settings.sources["registry"]
    if registry_path.exists():
        import csv

        with registry_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

    checks = []
    for row in rows:
        source_id = row.get("source_id", "")
        meta_path = settings.path("raw") / f"{_safe_name(source_id)}.meta.json"
        clean_path = settings.path("clean") / f"{_safe_name(source_id)}.json"
        check = {
            "source_id": source_id,
            "url": row.get("url"),
            "source_type": row.get("source_type"),
            "citable": str(row.get("citable", "")).strip().lower() in {"1", "true", "yes", "y"},
            "raw_saved": meta_path.exists(),
            "clean_saved": clean_path.exists(),
        }
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            check.update(
                {
                    "status": meta.get("status", "ok"),
                    "reason": meta.get("reason"),
                    "http_status": meta.get("http_status"),
                    "final_url": meta.get("final_url"),
                    "bytes": meta.get("bytes_written"),
                    "content_hash": meta.get("content_hash"),
                    "fetched_at": meta.get("fetched_at"),
                    "links_followed": meta.get("links_followed", 0),
                    "internal_links_discovered": meta.get("internal_links_discovered", 0),
                }
            )
        checks.append(check)

    report = {"source_errors": source_errors, "sources": checks}
    out = settings.path("sources") / "source_checks.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_sources_or_fail(settings: Settings):
    sources, errors = load_registry(settings)
    if errors:
        print("Source registry validation failed:")
        for error in errors:
            print(f"  - {error}")
        return None
    return sources


def cmd_check(settings: Settings) -> int:
    print(f"Config: {settings.root / 'config.yaml'}")
    print(f"Config hash: {settings.config_hash[:16]}")
    print(f"Phase: {settings.data.get('pipeline', {}).get('phase', 'unknown')}")
    for key in ["sources", "raw", "clean", "chunks", "embeddings", "vector", "retrieval", "answers"]:
        path = settings.path(key)
        status = "ok" if path.exists() else "missing"
        print(f"{key:12} {status:7} {path}")
    provider, model, error = resolve_provider(settings)
    if error:
        print(f"LLM:         extractive ({error})")
    else:
        print(f"LLM:         {getattr(provider, 'name', '?')} / {model}")
    sources = _load_sources_or_fail(settings)
    if sources is None:
        return 2
    seed_count = sum(1 for source in sources if source.source_type == "seed")
    official_count = sum(
        1
        for source in sources
        if source.source_type in {"amc", "sebi", "amfi", "registrar"}
    )
    print(
        f"Sources: {len(sources)} approved rows "
        f"({official_count} official, {seed_count} seed)"
    )
    return 0


def cmd_validate_sources(settings: Settings) -> int:
    sources, errors = load_registry(settings)
    if errors:
        print("Source registry validation failed:")
        for error in errors:
            print(f"  - {error}")
        _write_checks(settings, errors)
        return 1

    print(f"Source registry valid: {len(sources)} sources")
    for source in sources:
        citable = "citable" if source.citable else "not-citable"
        print(f"  {source.source_id:24} {source.source_type:6} {citable}  {source.url}")

    for source in sources:
        if not is_url_allowed(source.url, settings.sources.get("allowed_domains", [])):
            print(f"  [error] non-allowlisted URL: {source.url}")
            return 1
    _write_checks(settings, [])
    return 0


def cmd_load(settings: Settings) -> int:
    sources = _load_sources_or_fail(settings)
    if sources is None:
        return 2

    snapshots, errors = load_all(sources, settings)
    _write_checks(settings, errors)
    print(f"\nLoaded {len(snapshots)}/{len(sources)} sources")
    if errors:
        print("Load errors:")
        for error in errors:
            print(f"  - {error}")
        return 1
    return 0


def cmd_clean(settings: Settings) -> int:
    sources = _load_sources_or_fail(settings)
    if sources is None:
        return 2

    docs, errors = clean_all(sources, settings)
    _write_checks(settings, errors)
    print(f"\nCleaned {len(docs)}/{len(sources)} sources")
    if errors:
        print("Clean errors:")
        for error in errors:
            print(f"  - {error}")
        return 1
    return 0


def cmd_chunk(settings: Settings) -> int:
    sources = _load_sources_or_fail(settings)
    if sources is None:
        return 2

    docs, load_errors = load_clean_documents(sources, settings)
    if load_errors:
        print("Clean document errors:")
        for error in load_errors:
            print(f"  - {error}")
        return 1

    try:
        chunks, facts = chunk_all(docs, settings)
        report = write_artifacts(docs, chunks, facts, settings)
    except Exception as exc:  # noqa: BLE001 - CLI should report, not crash
        print(f"Chunking failed: {exc}")
        return 1

    print(
        f"\nChunked {len(docs)}/{len(sources)} sources: "
        f"{report['chunk_count']} chunks, {report['fact_count']} facts"
    )
    print(
        "Token distribution: "
        f"min={report['token_distribution']['min']} "
        f"median={report['token_distribution']['median']} "
        f"p95={report['token_distribution']['p95']} "
        f"max={report['token_distribution']['max']}"
    )
    print(f"Fact status: {report['fact_status_counts']}")
    print(f"Missing facts: {len(report['missing_facts'])}")
    return 0


def cmd_embed(settings: Settings) -> int:
    try:
        chunks = load_chunks(settings)
    except Exception as exc:  # noqa: BLE001 - CLI should report, not crash
        print(f"Embedding input error: {exc}")
        return 1

    try:
        all_embeddings, citable_embeddings, manifest = embed_chunks(chunks, settings)
        write_embedding_artifacts(
            chunks, all_embeddings, citable_embeddings, manifest, settings
        )
    except Exception as exc:  # noqa: BLE001 - CLI should report, not crash
        print(f"Embedding failed: {exc}")
        return 1

    print(
        f"\nEmbedded {manifest['chunk_count']} chunks "
        f"({manifest['citable_chunk_count']} citable): "
        f"shape={all_embeddings.shape}, dim={manifest['dimension']}"
    )
    print(
        f"Model: {manifest['embedding_model']} @ {manifest['resolved_revision']}\n"
        f"Cache: {manifest['cache_hits']} hits / {manifest['cache_misses']} misses\n"
        f"Manifest: {manifest['manifest_hash']}"
    )
    return 0


def cmd_index(settings: Settings) -> int:
    try:
        report = build_vector_store(settings)
    except Exception as exc:  # noqa: BLE001 - CLI should report, not crash
        print(f"Vector indexing failed: {exc}")
        return 1

    print(
        f"\nIndexed main collection {report['collection_name']}: "
        f"{report['collection_count']}/{report['expected_citable_count']} citable chunks"
    )
    print(
        f"Seed inspection collection: {report['seed_inspection_count']} chunks "
        f"({report['seed_inspection_collection']})"
    )
    print(
        f"Smoke: {report['smoke_mode']} | "
        f"{sum(1 for item in report['smoke_queries'] if item['passed'])}/"
        f"{len(report['smoke_queries'])} passed"
    )
    for warning in report["warnings"]:
        print(f"[warn] {warning}")
    return 0


def cmd_smoke(settings: Settings) -> int:
    try:
        report = run_smoke(settings)
    except Exception as exc:  # noqa: BLE001 - CLI should report, not crash
        print(f"Smoke query failed: {exc}")
        return 1

    print(
        f"\nSmoke mode: {report['smoke_mode']} | "
        f"main={report['collection_count']} citable | "
        f"seed inspection={report['seed_inspection_count']}"
    )
    for item in report["smoke_queries"]:
        status = "ok" if item["passed"] else "FAIL"
        print(
            f"[{status}] {item['scheme_slug']} / {item['fact_type']} -> "
            f"{item['returned_chunk_ids']}"
        )
    for warning in report["warnings"]:
        print(f"[warn] {warning}")
    return 0 if report["smoke_passed"] else 1


def cmd_eval(settings: Settings, *, limit: int | None = None) -> int:
    try:
        metrics, results = run_evaluation(settings, limit=limit)
    except Exception as exc:  # noqa: BLE001 - CLI should report, not crash
        print(f"Evaluation failed: {exc}")
        return 1

    print(
        f"\nGold set: {metrics['gold_questions']} questions | "
        f"passed={metrics['passed']} failed={metrics['failed']} "
        f"pass_rate={metrics['pass_rate']}"
    )
    print(
        f"Facts: {metrics['fact_pass_rate']} | "
        f"Refusals: {metrics['refusal_pass_rate']} | "
        f"Citations: {metrics['citation_rate']} | "
        f"PII absent: {metrics['pii_absent_rate']}"
    )
    print(
        f"Provider/model: {metrics.get('provider') or 'extractive'} / "
        f"{metrics.get('model') or 'n/a'}"
    )
    print(f"Artifacts: {settings.path('evaluation')}")

    failures = [row for row in results if not row["passed"]]
    if failures:
        print("Failures:")
        for row in failures:
            failed = [key for key, passed in row["checks"].items() if not passed]
            print(f"  - {row['id']} ({row.get('trace_id', 'no trace')}): {', '.join(failed)}")
        return 1
    return 0


def cmd_retrieve(settings: Settings, question: str, *, as_json: bool = False) -> int:
    trace = retrieve(question, settings)
    if as_json:
        print(trace.model_dump_json(indent=2))
    else:
        print(f"\nTrace: {trace.trace_id}")
        print(
            f"Intent: {trace.intent} | scheme={trace.scheme_slug or '-'} | "
            f"fact={trace.fact_type or '-'}"
        )
        if trace.needs_clarification:
            print(f"Clarification: {trace.clarification}")
        print(
            f"Filters: {trace.filters} | attempts={len(trace.filter_attempts)}"
        )
        print(f"Candidates: {len(trace.candidates)} | selected: {len(trace.selected)}")
        for hit in trace.candidates[:12]:
            print(
                f"  {hit.chunk_id} similarity={hit.similarity:.3f} "
                f"score={hit.score:.3f} "
                f"source={hit.metadata.get('source_type', '-')}"
            )
        print(f"Evidence gate: {trace.evidence_gate}")
        if trace.no_answer_reason:
            print(f"No answer: {trace.no_answer_reason}")
        print(f"Timings (ms): {trace.timings_ms}")

    if trace.evidence_gate.get("passed"):
        return 0
    if trace.intent in {"advice", "performance", "pii", "out_of_scope", "invalid", "ambiguous"}:
        return 0
    return 1


def cmd_answer(settings: Settings, question: str, *, as_json: bool = False) -> int:
    response = answer_question(question, settings)
    if as_json:
        print(response.model_dump_json(indent=2))
    else:
        print(f"\nAnswer trace: {response.trace_id}")
        print(f"Type: {response.answer_type} | intent={response.intent}")
        print(f"Answer: {response.answer}")
        if response.citation:
            print(f"Source: {response.citation.title}")
            print(f"URL: {response.citation.url}")
            print(f"Badge: {response.source_badge}")
        else:
            print("Source: unavailable (no approved official citation yet)")
        print(f"Last updated from sources: {response.last_updated}")
        if response.fallback_used:
            print(f"Extractive answer — {response.fallback_reason}")
        print(f"Validation: {response.validation.get('reason')} | "
              f"passed={response.validation.get('passed')}")
        print(f"Timings (ms): {response.timings_ms}")

    if response.answer_type in {"fact", "refusal", "clarification"}:
        return 0
    return 1


def cmd_report(settings: Settings) -> int:
    raw_dir = settings.path("raw")
    clean_dir = settings.path("clean")
    raw_files = sorted(raw_dir.glob("*")) if raw_dir.exists() else []
    clean_files = sorted(clean_dir.glob("*.json")) if clean_dir.exists() else []

    print(f"Raw files:   {len([p for p in raw_files if p.suffix in {'.html', '.pdf'}])}")
    print(f"Raw meta:    {len(list(raw_dir.glob('*.meta.json'))) if raw_dir.exists() else 0}")
    print(f"Clean JSON:  {len(clean_files)}")
    chunks_path = settings.path("chunks") / "chunks.jsonl"
    facts_path = settings.path("chunks") / "facts.jsonl"
    chunk_count = sum(1 for _ in chunks_path.open(encoding="utf-8")) if chunks_path.exists() else 0
    fact_count = sum(1 for _ in facts_path.open(encoding="utf-8")) if facts_path.exists() else 0
    print(f"Chunks:      {chunk_count}")
    print(f"Facts:       {fact_count}")
    manifest_path = settings.path("embeddings") / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            f"Embeddings:  {manifest.get('chunk_count', 0)} "
            f"(citable={manifest.get('citable_chunk_count', 0)}, "
            f"dim={manifest.get('dimension')})"
        )
    else:
        print("Embeddings:  0")
    vector_report_path = settings.path("vector") / "collection_report.json"
    if vector_report_path.exists():
        vector_report = json.loads(vector_report_path.read_text(encoding="utf-8"))
        print(
            f"Vector DB:   {vector_report.get('collection_count', 0)} citable "
            f"(seed inspection={vector_report.get('seed_inspection_count', 0)}, "
            f"status={vector_report.get('status')})"
        )
    else:
        print("Vector DB:   0")
    retrieval_dir = settings.path("retrieval")
    traces = sorted(retrieval_dir.glob("tr_*.json")) if retrieval_dir.exists() else []
    print(f"Retrieval:   {len(traces)} traces")
    if traces:
        latest = json.loads(traces[-1].read_text(encoding="utf-8"))
        print(
            f"Latest trace: {latest.get('trace_id')} | "
            f"intent={latest.get('intent')} | "
            f"selected={len(latest.get('selected', []))} | "
            f"gate={latest.get('evidence_gate', {}).get('reason')}"
        )
    answers_dir = settings.path("answers")
    answers = (
        sorted(
            path
            for path in answers_dir.glob("tr_*.json")
            if not path.name.endswith(".llm_raw.json")
        )
        if answers_dir.exists()
        else []
    )
    print(f"Answers:     {len(answers)}")
    if answers:
        latest_answer = json.loads(answers[-1].read_text(encoding="utf-8"))
        print(
            f"Latest answer: {latest_answer.get('trace_id')} | "
            f"type={latest_answer.get('answer_type')} | "
            f"fallback={latest_answer.get('fallback_used')} | "
            f"citation={bool(latest_answer.get('citation'))}"
        )
    print("")
    for path in clean_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(
            f"- {data['source_id']}: {data['title']} | "
            f"{len(data.get('sections', []))} sections | "
            f"{len(data.get('clean_text', ''))} chars | "
            f"source={data.get('source_type')}"
        )
    return 0


def cmd_clean_logs(settings: Settings) -> int:
    """Delete retrieval and answer logs while keeping the corpus intact."""
    removed = 0
    for key in ("retrieval", "answers"):
        directory = settings.path(key)
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.name != ".gitkeep":
                path.unlink()
                removed += 1
    print(f"Removed {removed} retrieval/answer log files")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HDFC MF facts assistant data loading")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Check config and directories")
    sub.add_parser("validate-sources", help="Validate sources.csv")
    sub.add_parser("load", help="Fetch approved source URLs")
    sub.add_parser("clean", help="Clean raw HTML into section JSON")
    sub.add_parser("chunk", help="Build chunks and fact cards")
    sub.add_parser("embed", help="Embed chunks with MiniLM")
    sub.add_parser("index", help="Build the Chroma vector collections")
    sub.add_parser("smoke", help="Run vector-store smoke queries")
    eval_parser = sub.add_parser("eval", help="Run the gold-set evaluation")
    eval_parser.add_argument(
        "--limit", type=int, default=None, help="Run only the first N gold questions"
    )
    retrieve_parser = sub.add_parser(
        "retrieve", help="Run guardrails, routing, retrieval, and the evidence gate"
    )
    retrieve_parser.add_argument("question", help="One factual question")
    retrieve_parser.add_argument("--json", action="store_true", help="Print the full trace")
    answer_parser = sub.add_parser(
        "answer", help="Retrieve, generate, validate, cite, and store one answer"
    )
    answer_parser.add_argument("question", help="One factual question")
    answer_parser.add_argument("--json", action="store_true", help="Print the answer response")
    sub.add_parser("report", help="Show stored artifact counts")
    sub.add_parser("clean-logs", help="Delete retrieval and answer logs")

    ingest = sub.add_parser("ingest", help="Run a loading stage")
    ingest.add_argument(
        "--stage",
        choices=["load", "clean", "chunk", "embed", "index", "all"],
        required=True,
    )

    args = parser.parse_args(argv)
    settings = load_settings(args.config)

    if args.command == "check":
        return cmd_check(settings)
    if args.command == "validate-sources":
        return cmd_validate_sources(settings)
    if args.command == "load":
        return cmd_load(settings)
    if args.command == "clean":
        return cmd_clean(settings)
    if args.command == "chunk":
        return cmd_chunk(settings)
    if args.command == "embed":
        return cmd_embed(settings)
    if args.command == "index":
        return cmd_index(settings)
    if args.command == "smoke":
        return cmd_smoke(settings)
    if args.command == "eval":
        return cmd_eval(settings, limit=args.limit)
    if args.command == "retrieve":
        return cmd_retrieve(settings, args.question, as_json=args.json)
    if args.command == "answer":
        return cmd_answer(settings, args.question, as_json=args.json)
    if args.command == "report":
        return cmd_report(settings)
    if args.command == "clean-logs":
        return cmd_clean_logs(settings)
    if args.command == "ingest":
        if args.stage == "load":
            return cmd_load(settings)
        if args.stage == "clean":
            return cmd_clean(settings)
        if args.stage == "chunk":
            return cmd_chunk(settings)
        if args.stage == "embed":
            return cmd_embed(settings)
        if args.stage == "index":
            return cmd_index(settings)
        code = cmd_load(settings)
        if code != 0:
            return code
        code = cmd_clean(settings)
        if code != 0:
            return code
        code = cmd_chunk(settings)
        if code != 0:
            return code
        code = cmd_embed(settings)
        if code != 0:
            return code
        return cmd_index(settings)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
