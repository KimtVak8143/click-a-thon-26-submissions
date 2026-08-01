import argparse
from dataclasses import dataclass
from pathlib import Path

from app.clickhouse.client import build_clickhouse_client
from app.clickhouse.repository import (
    ClickHouseMigrationRepository,
    ClickHouseMigrationRepositoryProtocol,
)
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

DEFAULT_MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"
logger = get_logger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    name: str


class MigrationRunner:
    def __init__(
        self,
        repository: ClickHouseMigrationRepositoryProtocol,
        migrations_directory: Path = DEFAULT_MIGRATIONS_DIRECTORY,
    ) -> None:
        self._repository = repository
        self._migrations_directory = migrations_directory

    def run(self) -> list[MigrationResult]:
        migration_paths = sorted(self._migrations_directory.glob("*.sql"))
        if not migration_paths:
            raise RuntimeError(f"no migrations found in {self._migrations_directory}")

        results = []
        for path in migration_paths:
            statement = path.read_text(encoding="utf-8").strip()
            if not statement:
                raise RuntimeError(f"migration is empty: {path.name}")
            self._repository.execute(statement)
            logger.info("migration_applied", extra={"migration": path.name})
            results.append(MigrationResult(name=path.name))
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Context Compiler ClickHouse migrations")
    parser.add_argument(
        "--directory",
        type=Path,
        default=DEFAULT_MIGRATIONS_DIRECTORY,
        help="Directory containing ordered .sql migration files",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    repository = ClickHouseMigrationRepository(build_clickhouse_client(settings))
    try:
        applied = MigrationRunner(repository, args.directory).run()
    finally:
        repository.close()
    logger.info("migrations_complete", extra={"migration_count": len(applied)})


if __name__ == "__main__":
    main()
