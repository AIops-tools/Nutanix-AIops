"""VM inventory + lifecycle MCP tools (read + guarded writes).

Power/update/clone/migrate/delete all run through the governance harness. ETag
handling is automatic in the ops layer. Reversible writes record an undo
descriptor; destructive writes (delete, migrate) are risk=high and accept a
``dry_run`` preview.
"""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import vms as ops

# ── undo callbacks ───────────────────────────────────────────────────────


def _power_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of a power action: restore the captured prior power state."""
    if not isinstance(result, dict):
        return None
    prior = ((result.get("priorState") or {}).get("powerState") or "").upper()
    ext = params.get("vm_ext_id")
    if prior == "ON":
        return {"tool": "vm_power_on", "params": {"vm_ext_id": ext},
                "note": "VM was ON before; power it back on."}
    if prior == "OFF":
        return {"tool": "vm_power_off", "params": {"vm_ext_id": ext},
                "note": "VM was OFF before; power it back off."}
    return None


def _update_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of update_vm: restore the captured prior CPU/memory."""
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    if prior.get("numSockets") is None and prior.get("memoryBytes") is None:
        return None
    return {
        "tool": "vm_update",
        "params": {"vm_ext_id": params.get("vm_ext_id"),
                   "num_sockets": prior.get("numSockets"),
                   "memory_bytes": prior.get("memoryBytes")},
        "note": "Restore the VM's prior CPU sockets / memory.",
    }


def _migrate_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of migrate_vm: migrate back to the prior host."""
    if not isinstance(result, dict):
        return None
    prior_host = (result.get("priorState") or {}).get("hostExtId")
    if not prior_host:
        return None
    return {"tool": "vm_migrate",
            "params": {"vm_ext_id": params.get("vm_ext_id"),
                       "target_host_ext_id": prior_host},
            "note": "Migrate the VM back to the host it ran on before."}


# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def vm_list(
    include_esxi: bool = True, limit: int = 500, target: Optional[str] = None
) -> dict:
    """[READ] List VMs — AHV and (by default) ESXi-backed too; hypervisor field distinguishes.

    Args:
        include_esxi: Include ESXi-backed VMs Prism Central can see (default True).
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_vms(_get_connection(target), include_esxi=include_esxi, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def vm_get(vm_ext_id: str, target: Optional[str] = None) -> dict:
    """[READ] One VM by extId, with its current ETag surfaced for downstream writes.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.get_vm(_get_connection(target), vm_ext_id)


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_power_undo)
@tool_errors("dict")
def vm_power_on(vm_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Power on a VM (reversible → power-off). Auto-handles ETag.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.power_on(_get_connection(target), vm_ext_id)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_power_undo)
@tool_errors("dict")
def vm_guest_shutdown(vm_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Graceful in-guest shutdown (reversible → power-on).

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.guest_shutdown(_get_connection(target), vm_ext_id)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_power_undo)
@tool_errors("dict")
def vm_power_off(vm_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Hard power off a VM (reversible → power-on).

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.power_off(_get_connection(target), vm_ext_id)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def vm_reboot(vm_ext_id: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Reboot a VM (no distinct inverse).

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.reboot_vm(_get_connection(target), vm_ext_id)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def vm_create(
    name: str,
    cluster_ext_id: str,
    num_sockets: int = 1,
    memory_bytes: int = 4294967296,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Create a minimal VM on a cluster.

    Args:
        name: New VM name.
        cluster_ext_id: Target cluster extId (from cluster_list).
        num_sockets: vCPU sockets (default 1).
        memory_bytes: RAM in bytes (default 4 GiB).
        target: Prism Central target name from config; omit for the default.
    """
    return ops.create_vm(_get_connection(target), name, cluster_ext_id,
                         num_sockets=num_sockets, memory_bytes=memory_bytes)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_update_undo)
@tool_errors("dict")
def vm_update(
    vm_ext_id: str,
    num_sockets: Optional[int] = None,
    memory_bytes: Optional[int] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Resize a VM's CPU sockets / memory (reversible → prior values).

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        num_sockets: New vCPU socket count (omit to leave unchanged).
        memory_bytes: New RAM in bytes (omit to leave unchanged).
        target: Prism Central target name from config; omit for the default.
    """
    return ops.update_vm(_get_connection(target), vm_ext_id,
                        num_sockets=num_sockets, memory_bytes=memory_bytes)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def vm_clone(vm_ext_id: str, new_name: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Clone a VM to a new name.

    Args:
        vm_ext_id: Source VM extId as returned by vm_list.
        new_name: Name for the clone.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.clone_vm(_get_connection(target), vm_ext_id, new_name)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def vm_delete(vm_ext_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Delete a VM. Destructive — pass dry_run=True to preview first.

    Requires an approver (set NUTANIX_AUDIT_APPROVED_BY) under the graduated-autonomy
    policy. Captures the VM's prior name/power state on the audit trail.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        dry_run: If True, return what WOULD be deleted without deleting.
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        # The preview runs the self-lockout guard too: if the real delete would
        # be refused, the dry-run must say so rather than report wouldDelete.
        return {"dryRun": True, "wouldDelete": ops.preview_delete_vm(conn, vm_ext_id)}
    return ops.delete_vm(conn, vm_ext_id)


@mcp.tool()
@governed_tool(risk_level="high", undo=_migrate_undo)
@tool_errors("dict")
def vm_migrate(
    vm_ext_id: str,
    target_host_ext_id: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Live-migrate a VM to another host (reversible → prior host).

    Pass dry_run=True to preview. Requires an approver under the policy.

    Args:
        vm_ext_id: VM extId as returned by vm_list.
        target_host_ext_id: Destination host extId (from host_list).
        dry_run: If True, preview the migration without performing it.
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        vm = ops.get_vm(conn, vm_ext_id)
        return {"dryRun": True, "vm": vm["name"], "fromHost": vm["hostExtId"],
                "toHost": target_host_ext_id}
    return ops.migrate_vm(conn, vm_ext_id, target_host_ext_id)
