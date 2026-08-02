"""
Analytics Agent - AI assistant for ClickHouse analytics queries.
"""

from typing import Any
from app.clickhouse.client import build_clickhouse_client
from app.core.config import get_settings


class AnalyticsAgentService:
    """Service wrapper for analytics queries."""
    
    def __init__(self):
        self.settings = get_settings()
    
    async def query_events(
        self,
        event_names: list[str] | None = None,
        limit: int = 100
    ) -> dict[str, Any]:
        """
        Query event data from ClickHouse.
        
        Args:
            event_names: Optional list of event names to filter
            limit: Maximum number of events to return
            
        Returns:
            Event data with counts and samples
        """
        try:
            client = build_clickhouse_client(self.settings)
            if event_names:
                placeholders = ", ".join([f"%({i})s" for i in range(len(event_names))])
                params = {str(i): name for i, name in enumerate(event_names)}
                query = f"""
                    SELECT 
                        event_name,
                        count() as event_count,
                        min(timestamp) as first_seen,
                        max(timestamp) as last_seen
                    FROM events
                    WHERE event_name IN ({placeholders})
                    GROUP BY event_name
                    ORDER BY event_count DESC
                    LIMIT {limit}
                """
            else:
                query = f"""
                    SELECT 
                        event_name,
                        count() as event_count,
                        min(timestamp) as first_seen,
                        max(timestamp) as last_seen
                    FROM events
                    GROUP BY event_name
                    ORDER BY event_count DESC
                    LIMIT {limit}
                """
                params = {}
            
            results = client.query(query, parameters=params)
            
            return {
                "success": True,
                "events": [dict(zip(results.column_names, row)) for row in results.result_rows],
                "total": len(results.result_rows)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def execute_sql(self, sql: str) -> dict[str, Any]:
        """
        Execute a raw SQL query against ClickHouse.
        
        Args:
            sql: The SQL query to execute
            
        Returns:
            Query results
        """
        try:
            # Safety check - only allow SELECT queries
            if not sql.strip().upper().startswith("SELECT"):
                return {
                    "success": False,
                    "error": "Only SELECT queries are allowed"
                }
            
            client = build_clickhouse_client(self.settings)
            results = client.query(sql)
            
            return {
                "success": True,
                "rows": [dict(zip(results.column_names, row)) for row in results.result_rows],
                "row_count": len(results.result_rows)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_metrics_summary(self) -> dict[str, Any]:
        """
        Get a summary of available metrics and their latest values.
        
        Returns:
            Summary of metrics from context_metrics table
        """
        try:
            client = build_clickhouse_client(self.settings)
            query = """
                SELECT 
                    metric_name,
                    metric_type,
                    count() as data_points,
                    max(recorded_at) as last_recorded
                FROM context_metrics
                GROUP BY metric_name, metric_type
                ORDER BY last_recorded DESC
                LIMIT 50
            """
            
            results = client.query(query)
            
            return {
                "success": True,
                "metrics": [dict(zip(results.column_names, row)) for row in results.result_rows],
                "total": len(results.result_rows)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_pipeline_runs(self, limit: int = 10) -> dict[str, Any]:
        """
        Get recent pipeline run results.
        
        Args:
            limit: Number of runs to return
            
        Returns:
            Recent pipeline runs with status and timing
        """
        try:
            client = build_clickhouse_client(self.settings)
            query = f"""
                SELECT 
                    run_id,
                    status,
                    started_at,
                    completed_at,
                    error_message
                FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT {limit}
            """
            
            results = client.query(query)
            
            return {
                "success": True,
                "runs": [dict(zip(results.column_names, row)) for row in results.result_rows],
                "total": len(results.result_rows)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
analytics_agent = AnalyticsAgentService()
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_metrics_summary(self) -> dict[str, Any]:
        """
        Get a summary of available metrics and their latest values.
        
        Returns:
            Summary of metrics from context_metrics table
        """
        try:
            async with get_clickhouse_client() as client:
                query = """
                    SELECT 
                        metric_name,
                        metric_type,
                        count() as data_points,
                        max(recorded_at) as last_recorded
                    FROM context_metrics
                    GROUP BY metric_name, metric_type
                    ORDER BY last_recorded DESC
                    LIMIT 50
                """
                
                results = await client.query(query)
                
                return {
                    "success": True,
                    "metrics": [dict(row) for row in results],
                    "total": len(results)
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_pipeline_runs(self, limit: int = 10) -> dict[str, Any]:
        """
        Get recent pipeline run results.
        
        Args:
            limit: Number of runs to return
            
        Returns:
            Recent pipeline runs with status and timing
        """
        try:
            async with get_clickhouse_client() as client:
                query = """
                    SELECT 
                        run_id,
                        status,
                        started_at,
                        completed_at,
                        error_message
                    FROM pipeline_runs
                    ORDER BY started_at DESC
                    LIMIT %(limit)s
                """
                
                results = await client.query(query, {"limit": limit})
                
                return {
                    "success": True,
                    "runs": [dict(row) for row in results],
                    "total": len(results)
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
analytics_agent = AnalyticsAgentService()
