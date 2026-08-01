import argparse
from pathlib import Path

from app.clickhouse.client import build_clickhouse_client
from app.context.bootstrap import BaseContextBootstrapper
from app.context.repository import ClickHouseContextRepository
from app.core.config import get_settings
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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "profile":
        _profile(args.events, args.output)
    elif args.command == "bootstrap-context":
        _bootstrap_context(args.source)


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


if __name__ == "__main__":
    main()
