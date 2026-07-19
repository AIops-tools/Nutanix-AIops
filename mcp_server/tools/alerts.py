"""Alerts / events / audits + RCA MCP tools (read + guarded writes).

Acknowledge/resolve run through the governance harness; ETag handling is
automatic in the ops layer. ``alert_analyze`` is the flagship value-add: a
resilient, deterministic root-cause summary that correlates an alert with the
recent events on the same affected entity.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import alerts as ops

# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def alert_list(
    severity: Optional[str] = None, limit: int = 500, target: Optional[str] = None
) -> dict:
    """[READ] List alerts (extId, title, severity, impact, ack/resolved, affected entity).

    Args:
        severity: Filter to one severity (e.g. CRITICAL); omit for all severities.
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_alerts(_get_connection(target), severity=severity, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def event_list(limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List events (extId, title, creation time, source entity).

    Args:
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_events(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def audit_list(limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List config audit records (extId, operation type, user, creation time).

    Args:
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_audits(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def analyze_alert(alert_ext_id: str, target: Optional[str] = None) -> dict:
    """[READ] Root-cause analysis: correlate an alert with recent same-entity events.

    Returns a probable cause and suggested actions plus the related events. Resilient
    and deterministic — safe to run on a live, alerting cluster.

    Args:
        alert_ext_id: Alert extId as returned by alert_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.analyze_alert(_get_connection(target), alert_ext_id)


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def alert_acknowledge(alert_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Acknowledge an alert. Auto-handles ETag; captures prior state.

    Args:
        alert_ext_id: Alert extId as returned by alert_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.acknowledge_alert(_get_connection(target), alert_ext_id)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def alert_resolve(alert_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Resolve an alert. Auto-handles ETag; captures prior state.

    Args:
        alert_ext_id: Alert extId as returned by alert_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.resolve_alert(_get_connection(target), alert_ext_id)
