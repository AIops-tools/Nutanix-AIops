"""Tasks + capacity-runway MCP tools (read-only).

Both tools are non-destructive reads (risk=low). ``capacity_runway`` is a
deterministic forecast: identical inputs always yield an identical answer, and
it reports ``insufficient-data`` rather than inventing a growth rate.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import capacity as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def task_list(
    status: Optional[str] = None, limit: int = 500, target: Optional[str] = None
) -> dict:
    """[READ] List Prism Central tasks (extId, operation, status, %complete, entity).

    Args:
        status: Optional status filter, e.g. RUNNING / SUCCEEDED / FAILED.
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_tasks(_get_connection(target), status=status, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def capacity_runway(
    cluster_ext_id: str,
    daily_growth_bytes: Optional[int] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] Deterministic storage-runway estimate for a cluster (days-to-full).

    Supply daily_growth_bytes (bytes/day) to project daysToFull; without it the
    forecast is 'insufficient-data'. No clock or randomness — same inputs, same
    answer.

    Args:
        cluster_ext_id: Cluster extId as returned by cluster_list.
        daily_growth_bytes: Assumed daily storage growth in bytes (omit to skip forecast).
        target: Prism Central target name from config; omit for the default.
    """
    return ops.get_capacity_runway(
        _get_connection(target), cluster_ext_id, daily_growth_bytes=daily_growth_bytes
    )
