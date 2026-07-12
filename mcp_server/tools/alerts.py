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
def alert_list(severity: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] List alerts (extId, title, severity, impact, ack/resolved, affected entity).

    Args:
        severity: Filter to one severity (e.g. CRITICAL); omit for all severities.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_alerts(_get_connection(target), severity=severity)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def event_list(target: Optional[str] = None) -> list:
    """[READ] List events (extId, title, creation time, source entity).

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_events(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def audit_list(target: Optional[str] = None) -> list:
    """[READ] List config audit records (extId, operation type, user, creation time).

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_audits(_get_connection(target))


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
@governed_tool(risk_level="low")
@tool_errors("dict")
def alert_acknowledge(alert_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=low] Acknowledge an alert. Auto-handles ETag; captures prior state.

    Args:
        alert_ext_id: Alert extId as returned by alert_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.acknowledge_alert(_get_connection(target), alert_ext_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def alert_resolve(alert_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=low] Resolve an alert. Auto-handles ETag; captures prior state.

    Args:
        alert_ext_id: Alert extId as returned by alert_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.resolve_alert(_get_connection(target), alert_ext_id)
