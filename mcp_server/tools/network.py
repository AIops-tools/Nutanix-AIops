"""Subnet / network inventory + lifecycle MCP tools (read + guarded writes).

Create/delete run through the governance harness. ETag handling is automatic in
the ops layer. The destructive delete is risk=high and accepts a ``dry_run``
preview.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import network as ops

# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def subnet_list(target: Optional[str] = None) -> list:
    """[READ] List subnets (extId, name, type, VLAN, cluster, IP config).

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_subnets(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def subnet_get(ext_id: str, target: Optional[str] = None) -> dict:
    """[READ] One subnet by extId, with its current ETag surfaced for downstream writes.

    Args:
        ext_id: Subnet extId as returned by subnet_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.get_subnet(_get_connection(target), ext_id)


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def subnet_create(
    name: str,
    cluster_ext_id: str,
    vlan_id: int,
    subnet_type: str = "VLAN",
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Create a subnet on a cluster.

    Args:
        name: New subnet name.
        cluster_ext_id: Target cluster extId (from cluster_list).
        vlan_id: VLAN / network id for the subnet.
        subnet_type: Subnet type (default VLAN).
        target: Prism Central target name from config; omit for the default.
    """
    return ops.create_subnet(_get_connection(target), name, cluster_ext_id,
                             vlan_id, subnet_type=subnet_type)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def subnet_delete(ext_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Delete a subnet. Destructive — pass dry_run=True to preview first.

    Requires an approver (set NUTANIX_AUDIT_APPROVED_BY) under the graduated-autonomy
    policy. Captures the subnet's prior name on the audit trail.

    Args:
        ext_id: Subnet extId as returned by subnet_list.
        dry_run: If True, return what WOULD be deleted without deleting.
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        subnet = ops.get_subnet(conn, ext_id)
        return {"dryRun": True, "wouldDelete": {"extId": subnet["extId"],
                                                "name": subnet["name"]}}
    return ops.delete_subnet(conn, ext_id)
