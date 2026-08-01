from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SNAPSHOT_NAMESPACE = uuid.UUID("02d08eb5-6486-5cb4-bff3-5b964f6f27a5")
BASELINE_METRICS_VERSION = "atlys_eight_tables_v1"
ATLYS_SOURCE_TABLES = (
    "destination_card_clicked",
    "application_started",
    "document_uploaded",
    "purchase_completed",
    "search_typed",
    "landing_page_scrolled",
    "auth_completed",
    "pay_now_clicked",
)


@dataclass(frozen=True)
class BaselineMetric:
    metric_id: str
    definition: str
    result: list[dict[str, Any]]
    sql_sha256: str
    result_sha256: str
    latency_ms: int
    evidence_id: str

    def compact(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "definition": self.definition,
            "result": self.result,
            "result_sha256": self.result_sha256,
            "evidence_id": self.evidence_id,
        }


@dataclass(frozen=True)
class BaselineMetricSnapshot:
    snapshot_id: uuid.UUID
    source_database: str
    source_fingerprint_sha256: str
    metrics: tuple[BaselineMetric, ...]
    computed_at: datetime

    @property
    def evidence_ids(self) -> list[str]:
        return sorted(metric.evidence_id for metric in self.metrics)

    def compact(self) -> dict[str, Any]:
        return {
            "version": BASELINE_METRICS_VERSION,
            "snapshot_id": str(self.snapshot_id),
            "source_database": self.source_database,
            "source_fingerprint_sha256": self.source_fingerprint_sha256,
            "computed_at": self.computed_at.isoformat(),
            "metrics": [metric.compact() for metric in self.metrics],
            "evidence_ids": self.evidence_ids,
            "usage_policy": {
                "aggregate_evidence_only": True,
                "design_guidance_not_causality": True,
            },
        }


class BaselineMetricsService:
    def __init__(
        self,
        client_factory: Callable[[], Any],
        source_database: str,
        metadata_database: str,
    ) -> None:
        if not _IDENTIFIER.fullmatch(source_database):
            raise ValueError("source database must be a safe ClickHouse identifier")
        if not _IDENTIFIER.fullmatch(metadata_database):
            raise ValueError("metadata database must be a safe ClickHouse identifier")
        self._client_factory = client_factory
        self._source_database = source_database
        self._metadata_database = metadata_database
        self._client: Any | None = None

    def precompute(self) -> BaselineMetricSnapshot:
        client = self._get_client()
        fingerprint = self._source_fingerprint(client)
        existing = self._latest_for_fingerprint(client, fingerprint)
        if existing is not None:
            return existing

        computed_at = datetime.now(UTC)
        pending = []
        for metric_id, definition, sql in self._queries():
            started = time.perf_counter()
            result = client.query(sql)
            rows = _result_rows(result)
            latency_ms = round((time.perf_counter() - started) * 1000)
            result_json = _stable_json(rows)
            pending.append(
                {
                    "metric_id": metric_id,
                    "definition": definition,
                    "result": rows,
                    "sql_sha256": _sha256(sql),
                    "result_sha256": _sha256(result_json),
                    "latency_ms": latency_ms,
                }
            )

        snapshot_seed = _stable_json(
            {
                "version": BASELINE_METRICS_VERSION,
                "source_database": self._source_database,
                "source_fingerprint_sha256": fingerprint,
                "metrics": pending,
            }
        )
        snapshot_id = uuid.uuid5(_SNAPSHOT_NAMESPACE, _sha256(snapshot_seed))
        metrics = tuple(
            BaselineMetric(
                **item,
                evidence_id=f"baseline:{item['metric_id']}:{snapshot_id}",
            )
            for item in pending
        )
        snapshot = BaselineMetricSnapshot(
            snapshot_id=snapshot_id,
            source_database=self._source_database,
            source_fingerprint_sha256=fingerprint,
            metrics=metrics,
            computed_at=computed_at,
        )
        self._persist(client, snapshot)
        return snapshot

    def latest(self) -> BaselineMetricSnapshot | None:
        client = self._get_client()
        table = self._table("baseline_metric_snapshots")
        result = client.query(
            "SELECT snapshot_id, source_database, source_fingerprint_sha256, metrics_json, "
            f"computed_at FROM {table} WHERE source_database = {{database:String}} "
            "ORDER BY computed_at DESC LIMIT 1",
            parameters={"database": self._source_database},
        )
        return _snapshot_from_rows(result.result_rows)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _table(self, name: str) -> str:
        return f"`{self._metadata_database}`.`{name}`"

    def _source_fingerprint(self, client: Any) -> str:
        result = client.query(
            "SELECT table, sum(rows) AS rows, min(min_time) AS first_event_time, "
            "max(max_time) AS last_event_time FROM system.parts "
            "WHERE active AND database = {database:String} AND table IN {tables:Array(String)} "
            "GROUP BY table ORDER BY table",
            parameters={"database": self._source_database, "tables": list(ATLYS_SOURCE_TABLES)},
        )
        inventory = _result_rows(result)
        found = {str(row.get("table")) for row in inventory}
        missing = sorted(set(ATLYS_SOURCE_TABLES) - found)
        if missing:
            raise RuntimeError(f"missing required Atlys source tables: {missing}")
        return _sha256(_stable_json(inventory))

    def _latest_for_fingerprint(
        self, client: Any, fingerprint: str
    ) -> BaselineMetricSnapshot | None:
        table = self._table("baseline_metric_snapshots")
        result = client.query(
            "SELECT snapshot_id, source_database, source_fingerprint_sha256, metrics_json, "
            f"computed_at FROM {table} WHERE source_database = {{database:String}} "
            "AND source_fingerprint_sha256 = {fingerprint:String} "
            "ORDER BY computed_at DESC LIMIT 1",
            parameters={"database": self._source_database, "fingerprint": fingerprint},
        )
        return _snapshot_from_rows(result.result_rows)

    def _persist(self, client: Any, snapshot: BaselineMetricSnapshot) -> None:
        metrics_json = _stable_json([_metric_record(metric) for metric in snapshot.metrics])
        client.insert(
            self._table("baseline_metric_snapshots"),
            [
                [
                    snapshot.snapshot_id,
                    snapshot.source_database,
                    snapshot.source_fingerprint_sha256,
                    metrics_json,
                    _stable_json(snapshot.evidence_ids),
                    snapshot.computed_at,
                ]
            ],
            column_names=[
                "snapshot_id",
                "source_database",
                "source_fingerprint_sha256",
                "metrics_json",
                "evidence_ids_json",
                "computed_at",
            ],
        )

    def _queries(self) -> tuple[tuple[str, str, str], ...]:
        database = f"`{self._source_database}`"
        return (
            (
                "session_funnel",
                "Ordered session funnel from destination click through purchase",
                _session_funnel_sql(database),
            ),
            (
                "application_funnel",
                "Ordered application funnel from application start through purchase",
                _application_funnel_sql(database),
            ),
            (
                "pay_click_purchase_rate",
                "Ordered pay-now click to purchase rate by application",
                _pay_click_purchase_sql(database),
            ),
            (
                "passport_capture_pass_rate",
                "Document uploads not crossing the failed-attempt threshold",
                _passport_capture_sql(database),
            ),
            (
                "revenue_by_currency",
                "Completed-purchase revenue grouped by event currency without FX mixing",
                _revenue_sql(database),
            ),
            (
                "source_table_health",
                "Row count, event-ID uniqueness, time coverage, and OS null rate by source table",
                _table_health_sql(database),
            ),
        )


def _session_funnel_sql(database: str) -> str:
    return f"""WITH journeys AS
(
    SELECT app_session_id,
           windowFunnel(604800)(timestamp,
               event_name = 'destination_card_clicked',
               event_name = 'application_started',
               event_name = 'document_uploaded',
               event_name = 'purchase_completed') AS stage
    FROM
    (
        SELECT app_session_id, timestamp, 'destination_card_clicked' AS event_name
        FROM {database}.`destination_card_clicked`
        UNION ALL
        SELECT app_session_id, timestamp, 'application_started'
        FROM {database}.`application_started`
        UNION ALL
        SELECT app_session_id, timestamp, 'document_uploaded'
        FROM {database}.`document_uploaded`
        UNION ALL
        SELECT app_session_id, timestamp, 'purchase_completed'
        FROM {database}.`purchase_completed`
    )
    WHERE app_session_id IS NOT NULL AND app_session_id != ''
    GROUP BY app_session_id
)
SELECT countIf(stage >= 1) AS clicked_sessions,
       countIf(stage >= 2) AS started_sessions,
       countIf(stage >= 3) AS documented_sessions,
       countIf(stage >= 4) AS purchased_sessions,
       round(100.0 * purchased_sessions / nullIf(clicked_sessions, 0), 2) AS overall_conversion_pct
FROM journeys"""


def _application_funnel_sql(database: str) -> str:
    return f"""WITH journeys AS
(
    SELECT application_id,
           windowFunnel(604800)(timestamp,
               event_name = 'application_started',
               event_name = 'document_uploaded',
               event_name = 'purchase_completed') AS stage
    FROM
    (
        SELECT application_id, timestamp, 'application_started' AS event_name
        FROM {database}.`application_started`
        UNION ALL
        SELECT application_id, timestamp, 'document_uploaded'
        FROM {database}.`document_uploaded`
        UNION ALL
        SELECT application_id, timestamp, 'purchase_completed'
        FROM {database}.`purchase_completed`
    )
    WHERE application_id IS NOT NULL AND application_id != ''
    GROUP BY application_id
)
SELECT countIf(stage >= 1) AS started_applications,
       countIf(stage >= 2) AS documented_applications,
       countIf(stage >= 3) AS purchased_applications,
       round(100.0 * documented_applications /
             nullIf(started_applications, 0), 2) AS start_to_document_pct,
       round(100.0 * purchased_applications /
             nullIf(documented_applications, 0), 2) AS document_to_purchase_pct,
       round(100.0 * purchased_applications /
             nullIf(started_applications, 0), 2) AS overall_conversion_pct
FROM journeys"""


def _pay_click_purchase_sql(database: str) -> str:
    return f"""WITH journeys AS
(
    SELECT application_id,
           windowFunnel(172800)(timestamp,
               event_name = 'pay_now_clicked',
               event_name = 'purchase_completed') AS stage
    FROM
    (
        SELECT application_id, timestamp, 'pay_now_clicked' AS event_name
        FROM {database}.`pay_now_clicked`
        UNION ALL
        SELECT application_id, timestamp, 'purchase_completed'
        FROM {database}.`purchase_completed`
    )
    WHERE application_id IS NOT NULL AND application_id != ''
    GROUP BY application_id
)
SELECT countIf(stage >= 1) AS pay_clicked_applications,
       countIf(stage >= 2) AS purchased_applications,
       round(100.0 * purchased_applications /
             nullIf(pay_clicked_applications, 0), 2) AS conversion_pct
FROM journeys"""


def _passport_capture_sql(database: str) -> str:
    return f"""SELECT uniqExact(id) AS uploads,
       uniqExactIf(id, coalesce(is_crossed_failed_attempt_threshold, 0) = 0) AS passed_uploads,
       round(100.0 * passed_uploads / nullIf(uploads, 0), 2) AS pass_rate_pct
FROM {database}.`document_uploaded`"""


def _revenue_sql(database: str) -> str:
    return f"""SELECT coalesce(currency, 'UNKNOWN') AS currency,
       uniqExact(id) AS purchases,
       round(sum(coalesce(value, 0)), 2) AS revenue,
       round(avgIf(value, value IS NOT NULL), 2) AS average_order_value
FROM {database}.`purchase_completed`
GROUP BY currency
ORDER BY purchases DESC"""


def _table_health_sql(database: str) -> str:
    selects = [
        f"SELECT '{table}' AS table, count() AS rows, uniqExact(id) AS unique_event_ids, "
        "min(timestamp) AS first_seen, max(timestamp) AS last_seen, "
        "round(100.0 * countIf(os IS NULL OR os = '') / nullIf(rows, 0), 2) "
        f"AS null_os_pct FROM {database}.`{table}`"
        for table in ATLYS_SOURCE_TABLES
    ]
    return "\nUNION ALL\n".join(selects) + "\nORDER BY table"


def _result_rows(result: Any) -> list[dict[str, Any]]:
    columns = [str(value) for value in result.column_names]
    return [
        {
            columns[index] if index < len(columns) else f"col_{index}": _json_value(value)
            for index, value in enumerate(row)
        }
        for row in result.result_rows
    ]


def _snapshot_from_rows(rows: list[list[Any]]) -> BaselineMetricSnapshot | None:
    if not rows:
        return None
    snapshot_id, source_database, fingerprint, metrics_json, computed_at = rows[0]
    metrics = tuple(BaselineMetric(**item) for item in json.loads(metrics_json))
    return BaselineMetricSnapshot(
        snapshot_id=uuid.UUID(str(snapshot_id)),
        source_database=_text_value(source_database),
        source_fingerprint_sha256=_text_value(fingerprint),
        metrics=metrics,
        computed_at=computed_at,
    )


def _metric_record(metric: BaselineMetric) -> dict[str, Any]:
    return {
        "metric_id": metric.metric_id,
        "definition": metric.definition,
        "result": metric.result,
        "sql_sha256": metric.sql_sha256,
        "result_sha256": metric.result_sha256,
        "latency_ms": metric.latency_ms,
        "evidence_id": metric.evidence_id,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return str(value)


def _text_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
