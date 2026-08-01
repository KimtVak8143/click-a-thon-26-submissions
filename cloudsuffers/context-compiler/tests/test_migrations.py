from pathlib import Path

from app.clickhouse.migrations import MigrationRunner


class RecordingMigrationRepository:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def test_runner_applies_all_migrations_in_filename_order() -> None:
    repository = RecordingMigrationRepository()
    migrations_directory = Path(__file__).parents[1] / "migrations"

    results = MigrationRunner(repository, migrations_directory).run()

    assert [result.name for result in results] == [
        "001_compiler_meta_database.sql",
        "010_pipeline_runs.sql",
        "020_analytics_contracts.sql",
        "030_schema_versions.sql",
        "040_context_versions.sql",
        "050_context_issues.sql",
        "060_query_evidence.sql",
    ]
    assert len(repository.statements) == 7
    assert all(statement.startswith("CREATE ") for statement in repository.statements)
