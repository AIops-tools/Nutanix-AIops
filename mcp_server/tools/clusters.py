"""Cluster / host / utilization MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import clusters as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cluster_list(target: Optional[str] = None) -> list:
    """[READ] List registered clusters (extId, name, AOS version, hypervisors, nodes).

    Call this first — most other tools need a clusterExtId from here.

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_clusters(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cluster_health(cluster_ext_id: str, target: Optional[str] = None) -> dict:
    """[READ] One cluster's health: services, resiliency/fault-tolerance, upgrade state.

    Args:
        cluster_ext_id: Cluster extId as returned by cluster_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.get_cluster_health(_get_connection(target), cluster_ext_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def host_list(target: Optional[str] = None) -> list:
    """[READ] List hosts across all clusters (extId, cluster, hypervisor, CPU/mem).

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_hosts(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cluster_utilization(cluster_ext_id: str, target: Optional[str] = None) -> dict:
    """[READ] Point-in-time CPU / memory / storage / IOPS utilization for a cluster.

    Args:
        cluster_ext_id: Cluster extId as returned by cluster_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.get_cluster_utilization(_get_connection(target), cluster_ext_id)
