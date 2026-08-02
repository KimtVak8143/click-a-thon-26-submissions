"""
Context Compiler Agent - AI assistant with access to context compiler tools.
"""

from typing import Any
from app.core.config import get_settings
from app.clickhouse.client import build_clickhouse_client


class ContextAgentService:
    """Service wrapper for context compiler capabilities."""
    
    def __init__(self):
        self.settings = get_settings()
    
    async def analyze_specification(self, specification: str) -> dict[str, Any]:
        """
        Analyze a product feature specification.
        
        Args:
            specification: The feature specification text to analyze
            
        Returns:
            Analysis results with entities, relationships, and insights
        """
        try:
            # Simplified analysis - in production this would use LLM
            return {
                "success": True,
                "specification": specification,
                "analysis": "Specification analysis requires full pipeline run with /pipeline/run endpoint",
                "entities": [],
                "relationships": [],
                "insights": [],
                "summary": f"Received specification of {len(specification)} characters"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def generate_contract(
        self,
        specification: str,
        events_summary: str | None = None
    ) -> dict[str, Any]:
        """
        Generate an analytics contract from a specification.
        
        Args:
            specification: Feature specification text
            events_summary: Optional summary of observed events
            
        Returns:
            Generated contract with metrics and dimensions
        """
        try:
            return {
                "success": True,
                "message": "Use /pipeline/run endpoint for full contract generation",
                "specification": specification[:100] + "..." if len(specification) > 100 else specification,
                "contract": {},
                "metrics": [],
                "dimensions": []
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_context_info(self, context_key: str | None = None) -> dict[str, Any]:
        """
        Get information about context versions.
        
        Args:
            context_key: Optional context key to filter by
            
        Returns:
            Context version information
        """
        try:
            client = build_clickhouse_client(self.settings)
            if context_key:
                query = f"""
                    SELECT 
                        context_key,
                        version,
                        created_at,
                        length(context_data) as data_size
                    FROM context_versions
                    WHERE context_key = '{context_key}'
                    ORDER BY version DESC
                    LIMIT 10
                """
            else:
                query = """
                    SELECT 
                        context_key,
                        count() as version_count,
                        max(version) as latest_version,
                        max(created_at) as last_updated
                    FROM context_versions
                    GROUP BY context_key
                    ORDER BY last_updated DESC
                    LIMIT 20
                """
            
            results = client.query(query)
            
            return {
                "success": True,
                "contexts": [dict(zip(results.column_names, row)) for row in results.result_rows],
                "total": len(results.result_rows)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
context_agent = ContextAgentService()
