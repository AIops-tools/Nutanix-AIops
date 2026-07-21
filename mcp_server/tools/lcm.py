"""LCM (Life Cycle Manager) MCP tools (read + guarded writes).

Surfaces the LCM upgrade inventory and the two LCM actions. Precheck is a
read-safe validation action (risk=medium); update is the actual firmware/software
upgrade (risk=high), accepts a ``dry_run`` preview, and is refused unless the
precheck's task extId is handed back and that task reached SUCCEEDED. Everything
runs through the governance harness.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import lcm as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def lcm_inventory(limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List LCM-managed entities: current/available versions and updateAvailable.

    Args:
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_lcm_inventory(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def lcm_precheck(
    cluster_ext_id: str,
    entity_ext_ids: list[str],
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Run LCM prechecks for entities on a cluster (read-safe validation).

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
    precheck_task_ext_id: str = "",
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Perform an LCM firmware/software update. Pass dry_run=True to preview.

    Destructive and NOT undoable — tagged risk=high (audit tier 'review');
    NUTANIX_AUDIT_APPROVED_BY / NUTANIX_AUDIT_RATIONALE optionally annotate
    who/why. Requires a precheck that passed: run lcm_precheck first and pass
    the taskExtId it returned. The update is refused without it. The dry_run
    preview enforces the same requirement, so a preview can never report an
    update the real call would refuse.

    Args:
        cluster_ext_id: Cluster extId as returned by cluster_list.
        entity_ext_ids: LCM entity extIds (from lcm_inventory) to update.
        precheck_task_ext_id: taskExtId returned by lcm_precheck for these entities,
            once that task has reached SUCCEEDED (check with task_list). Required —
            the update is refused without it.
        dry_run: If True, return what WOULD be updated without updating.
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        # The preview runs the precheck requirement too: if the real update
        # would be refused, the dry-run must say so rather than report wouldUpdate.
        return {
            "dryRun": True,
            "wouldUpdate": ops.preview_update(conn, cluster_ext_id, entity_ext_ids,
                                              precheck_task_ext_id),
        }
    return ops.perform_update(conn, cluster_ext_id, entity_ext_ids, precheck_task_ext_id)
