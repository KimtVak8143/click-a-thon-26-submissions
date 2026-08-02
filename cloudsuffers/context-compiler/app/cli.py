import argparse
import asyncio
import json
from pathlib import Path

from app.benchmarks.atlys import run_atlys_benchmark
from app.clickhouse.client import build_clickhouse_client
from app.context.bootstrap import BaseContextBootstrapper
from app.context.repository import ClickHouseContextRepository
from app.core.config import get_settings
from app.metrics.baseline import BaselineMetricsService
from app.profiling.profiler import ProfilerOptions, SourceProfiler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="context-compiler")
    commands = parser.add_subparsers(dest="command", required=True)
    profile = commands.add_parser("profile", help="Profile an NDJSON events file")
    profile.add_argument("--events", type=Path, required=True, help="Input NDJSON path")
    profile.add_argument("--output", type=Path, required=True, help="Output JSON path")
    bootstrap = commands.add_parser(
        "bootstrap-context", help="Import the official base context as approved version 1"
    )
    bootstrap.add_argument("--source", type=Path, default=None)
    baseline = commands.add_parser(
        "precompute-baseline",
        help="Compute and persist metrics over the eight existing Atlys event tables",
    )
    baseline.add_argument(
        "--json", action="store_true", help="Print the compact persisted snapshot as JSON"
    )
    benchmark = commands.add_parser(
        "benchmark-atlys",
        help="Run every discovered spec.md/events.ndjson package through the instrumentation agent",
    )
    benchmark.add_argument("--specs-root", type=Path, default=Path("Atlys/specs"))
    benchmark.add_argument("--output", type=Path, default=Path("artifacts/atlys_benchmark.json"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "profile":
        _profile(args.events, args.output)
    elif args.command == "bootstrap-context":
        _bootstrap_context(args.source)
    elif args.command == "precompute-baseline":
        _precompute_baseline(as_json=args.json)
    elif args.command == "benchmark-atlys":
        _benchmark_atlys(args.specs_root, args.output)


def _profile(events_path: Path, output_path: Path) -> None:
    if not events_path.is_file():
        raise SystemExit(f"events file does not exist: {events_path}")
    if events_path.suffix.lower() != ".ndjson":
        raise SystemExit("events file must use the .ndjson extension")
    if events_path.resolve() == output_path.resolve():
        raise SystemExit("output path must differ from events path")

    settings = get_settings()
    if events_path.stat().st_size > settings.profile_max_upload_bytes:
        raise SystemExit(f"events file exceeds the {settings.profile_max_upload_bytes}-byte limit")
    profiler = SourceProfiler(
        ProfilerOptions(
            example_limit=settings.profile_example_limit,
            distinct_limit=settings.profile_distinct_limit,
            example_string_length=settings.profile_example_string_length,
        )
    )
    profile = profiler.profile(events_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{profile.stable_json(indent=2)}\n", encoding="utf-8")


def _bootstrap_context(source_path: Path | None) -> None:
    settings = get_settings()
    repository = ClickHouseContextRepository(
        lambda: build_clickhouse_client(settings), settings.clickhouse_metadata_database
    )
    try:
        result = BaseContextBootstrapper(repository).bootstrap(
            source_path or settings.base_context_path
        )
    finally:
        repository.close()
    print(
        f"context_version_id={result.context_version_id} "
        f"content_sha256={result.content_sha256} created={str(result.created).lower()}"
    )


def _precompute_baseline(*, as_json: bool = False) -> None:
    settings = get_settings()
    service = BaselineMetricsService(
        lambda: build_clickhouse_client(settings),
        settings.clickhouse_database,
        settings.clickhouse_metadata_database,
    )
    try:
        snapshot = service.precompute()
    finally:
        service.close()
    if as_json:
        print(json.dumps(snapshot.compact(), indent=2, sort_keys=True))
    else:
        print(
            f"snapshot_id={snapshot.snapshot_id} "
            f"source_fingerprint_sha256={snapshot.source_fingerprint_sha256} "
            f"metrics={len(snapshot.metrics)}"
        )


def _benchmark_atlys(specs_root: Path, output_path: Path) -> None:
    if not specs_root.is_dir():
        raise SystemExit(f"specs root does not exist: {specs_root}")
    result = asyncio.run(run_atlys_benchmark(get_settings(), specs_root))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(result, indent=2, sort_keys=True)}\n", encoding="utf-8")
    print(
        f"packages={result['discovered_package_count']} "
        f"all_valid={str(result['all_valid']).lower()} output={output_path}"
    )


if __name__ == "__main__":
    main()
