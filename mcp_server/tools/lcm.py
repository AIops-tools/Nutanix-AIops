"""LCM (Life Cycle Manager) MCP tools (read + guarded writes).

Surfaces the LCM upgrade inventory and the two LCM actions. Precheck is a
read-safe validation action (risk=low); update is the actual firmware/software
upgrade (risk=high) and accepts a ``dry_run`` preview. Everything runs through
the governance harness.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import lcm as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def lcm_inventory(target: Optional[str] = None) -> list:
    """[READ] List LCM-managed entities: current/available versions and updateAvailable.

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_lcm_inventory(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def lcm_precheck(
    cluster_ext_id: str,
    entity_ext_ids: list[str],
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=low] Run LCM prechecks for entities on a cluster (read-safe validation).

    Args:
        cluster_ext_id: Cluster extId as returned by cluster_list.
        entity_ext_ids: LCM entity extIds (from lcm_inventory) to precheck.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.run_precheck(_get_connection(target), cluster_ext_id, entity_ext_ids)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def lcm_update(
    cluster_ext_id: str,
    entity_ext_ids: list[str],
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Perform an LCM firmware/software update. Pass dry_run=True to preview.

    Destructive — requires an approver (set NUTANIX_AUDIT_APPROVED_BY) under the
    graduated-autonomy policy. Run lcm_precheck first.

    Args:
        cluster_ext_id: Cluster extId as returned by cluster_list.
        entity_ext_ids: LCM entity extIds (from lcm_inventory) to update.
        dry_run: If True, return what WOULD be updated without updating.
        target: Prism Central target name from config; omit for the default.
    """
    if dry_run:
        return {"dryRun": True, "wouldUpdate": {"clusterExtId": cluster_ext_id,
                                                "entities": entity_ext_ids}}
    return ops.perform_update(_get_connection(target), cluster_ext_id, entity_ext_ids)
