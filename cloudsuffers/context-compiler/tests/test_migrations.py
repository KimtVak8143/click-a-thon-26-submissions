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
        "070_context_sources.sql",
        "071_context_versions_provenance.sql",
        "072_context_entities.sql",
        "073_context_metrics.sql",
        "074_context_relationships.sql",
        "075_context_changelog.sql",
        "080_analytics_insights.sql",
    ]
    assert len(repository.statements) == 14
    assert all(statement.startswith(("CREATE ", "ALTER ")) for statement in repository.statements)
    assert all("{metadata_database}" not in statement for statement in repository.statements)
    assert all("compiler_meta" in statement for statement in repository.statements)


def test_runner_uses_configured_metadata_database() -> None:
    repository = RecordingMigrationRepository()
    migrations_directory = Path(__file__).parents[1] / "migrations"

    MigrationRunner(repository, migrations_directory, "custom_meta").run()

    assert all("compiler_meta" not in statement for statement in repository.statements)
    assert all("custom_meta" in statement for statement in repository.statements)
