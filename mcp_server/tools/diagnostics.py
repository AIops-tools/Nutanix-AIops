"""Diagnostics / RCA MCP tools: estate health and alert triage (read-only).

Both tools are read-only signature analyses (``risk_level="low"``). Each collects
the normalised Prism Central inventory once and hands it to a pure analysis
function in ``nutanix_aiops.ops.diagnostics`` — so the heuristics stay
unit-testable without a live Prism Central, and the collection stays here where
the connection lives.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import alerts as al
from nutanix_aiops.ops import clusters as cl
from nutanix_aiops.ops import diagnostics as diag
from nutanix_aiops.ops import storage as st


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cluster_health_rca(target: Optional[str] = None) -> dict:
    """[READ] Estate health RCA: resiliency state, storage headroom, nodes down.

    Collects every cluster's health + utilization, all hosts, and all storage
    containers, then reports worst-first findings: degraded fault-tolerance
    state, cluster storage pools or containers over 80% (warning) / 90%
    (critical), hosts whose nodeStatus is not healthy, and clusters reporting
    fewer visible hosts than their nodeCount. Every finding cites the measured
    percentage or the raw Prism state string that tripped it.

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    return diag.cluster_health_findings(
        cl.list_cluster_reports(conn),
        cl.list_hosts(conn)["hosts"],
        st.list_storage_containers(conn)["containers"],
    )


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def alert_triage_rca(target: Optional[str] = None) -> dict:
    """[READ] Triage active Prism alerts: per-severity counts + oldest unresolved.

    Groups every unresolved alert by severity with a count per level, flags
    critical alerts nobody has acknowledged, and surfaces the oldest unresolved
    alert with its age in days (aged against the newest alert in the same feed,
    so the analysis is clock-free and reproducible). Use analyze_alert on an
    individual extId for the per-alert event correlation.

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    return diag.alert_triage_findings(al.list_alerts(conn)["alerts"])
