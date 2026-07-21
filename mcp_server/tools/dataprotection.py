"""Snapshot / recovery-point / protection-domain MCP tools (read + guarded writes).

The "snapshot sprawl" + DR surface. Snapshot create is reversible (→ delete the
snapshot) and records an undo descriptor. Destructive writes (snapshot delete,
snapshot restore, PD failover) are risk=high and accept a ``dry_run`` preview.
Snapshot restore reverts the VM and is NOT safely undoable, so it records no
undo. ETag handling is automatic in the ops layer.
"""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import dataprotection as ops

# ── undo callbacks ───────────────────────────────────────────────────────


def _create_snapshot_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of create_snapshot: delete the snapshot that was just created.

    Uses the RESOLVED snapshot extId (the taskExtId from the async create is a
    task id, not a deletable entity). When resolution failed (snapshot not yet
    materialised), returns None — recording an unreplayable undo would be worse
    than recording none.
    """
    if not isinstance(result, dict):
        return None
    snap = result.get("snapshotExtId")
    vm = params.get("vm_ext_id")
    if not snap or not vm:
        return None
    return {"tool": "snapshot_delete",
            "params": {"vm_ext_id": vm, "snapshot_ext_id": snap},
            "note": "Delete the snapshot that was just created."}


# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def snapshot_list(vm_ext_id: str, limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List a VM's snapshots (extId, name, createTime) — spot lingering snapshots.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_snapshots(_get_connection(target), vm_ext_id, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def recovery_point_list(limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List recovery points (extId, vmExtId, create/expiration time, locationType).

    Args:
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_recovery_points(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def protection_domain_list(limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List protection domains / policies (extId, name, replicationType).

    Args:
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_protection_domains(_get_connection(target), limit=limit)


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_create_snapshot_undo)
@tool_errors("dict")
def snapshot_create(vm_ext_id: str, name: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Snapshot a VM (reversible → delete the snapshot). Auto-handles ETag.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        name: Name for the new snapshot.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.create_snapshot(_get_connection(target), vm_ext_id, name)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def snapshot_delete(
    vm_ext_id: str,
    snapshot_ext_id: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Delete a VM snapshot. Destructive — pass dry_run=True to preview.

    Tagged risk=high (audit tier 'review'); NUTANIX_AUDIT_APPROVED_BY /
    NUTANIX_AUDIT_RATIONALE optionally annotate who/why. Captures the
    snapshot's prior name on the audit trail.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        snapshot_ext_id: Snapshot extId as returned by snapshot_list.
        dry_run: If True, return what WOULD be deleted without deleting.
        target: Prism Central target name from config; omit for the default.
    """
    if dry_run:
        return {"dryRun": True, "wouldDelete": {"vmExtId": vm_ext_id,
                                                "snapshotExtId": snapshot_ext_id}}
    return ops.delete_snapshot(_get_connection(target), vm_ext_id, snapshot_ext_id)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def snapshot_restore(
    vm_ext_id: str,
    snapshot_ext_id: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Revert a VM to a snapshot. Destructive & NOT undoable — preview first.

    Reverting overwrites the VM's current state; pass dry_run=True to preview.
    Tagged risk=high (audit tier 'review'); NUTANIX_AUDIT_APPROVED_BY /
    NUTANIX_AUDIT_RATIONALE optionally annotate who/why.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        snapshot_ext_id: Snapshot extId as returned by snapshot_list.
        dry_run: If True, preview the revert without performing it.
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        # The preview runs the self-lockout guard too: if the real revert would
        # be refused, the dry-run must say so rather than report wouldRevert.
        return {
            "dryRun": True,
            "wouldRevert": ops.preview_restore_snapshot(conn, vm_ext_id, snapshot_ext_id),
        }
    return ops.restore_snapshot(conn, vm_ext_id, snapshot_ext_id)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def vm_protect(vm_ext_id: str, policy_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Associate a VM with a protection policy.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        policy_ext_id: Protection policy extId as returned by protection_domain_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.protect_vm(_get_connection(target), vm_ext_id, policy_ext_id)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def pd_failover(
    policy_ext_id: str,
    cluster_ext_id: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Fail a protection domain over to a target cluster (DR event).

    Destructive DR operation; pass dry_run=True to preview. Tagged risk=high
    (audit tier 'review'); NUTANIX_AUDIT_APPROVED_BY / NUTANIX_AUDIT_RATIONALE
    optionally annotate who/why.

    Args:
        policy_ext_id: Protection policy extId as returned by protection_domain_list.
        cluster_ext_id: Target cluster extId (from cluster_list).
        dry_run: If True, preview the failover without performing it.
        target: Prism Central target name from config; omit for the default.
    """
    if dry_run:
        return {"dryRun": True, "wouldFailover": {"policyExtId": policy_ext_id,
                                                  "targetClusterExtId": cluster_ext_id}}
    return ops.failover_pd(_get_connection(target), policy_ext_id, cluster_ext_id)
