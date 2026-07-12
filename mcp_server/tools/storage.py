"""Storage container inventory + lifecycle MCP tools (read + guarded writes).

Create/update/delete run through the governance harness. ETag handling is
automatic in the ops layer. The reversible update records an undo descriptor;
delete is risk=high and accepts a ``dry_run`` preview.
"""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import storage as ops

# ── undo callbacks ───────────────────────────────────────────────────────


def _update_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of update_storage_container: restore the captured prior values."""
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    if prior.get("maxCapacityBytes") is None and prior.get("replicationFactor") is None:
        return None
    return {
        "tool": "storage_container_update",
        "params": {"ext_id": params.get("ext_id"),
                   "max_capacity_bytes": prior.get("maxCapacityBytes"),
                   "replication_factor": prior.get("replicationFactor")},
        "note": "Restore the container's prior capacity / replication factor.",
    }


# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def storage_container_list(target: Optional[str] = None) -> list:
    """[READ] List storage containers (extId, name, cluster, capacity, usage, RF).

    Args:
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_storage_containers(_get_connection(target))


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def storage_container_create(
    name: str,
    cluster_ext_id: str,
    replication_factor: int = 2,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Create a storage container on a cluster.

    Args:
        name: New storage container name.
        cluster_ext_id: Target cluster extId (from cluster_list).
        replication_factor: Copies kept per block (default 2).
        target: Prism Central target name from config; omit for the default.
    """
    return ops.create_storage_container(_get_connection(target), name, cluster_ext_id,
                                        replication_factor=replication_factor)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_update_undo)
@tool_errors("dict")
def storage_container_update(
    ext_id: str,
    max_capacity_bytes: Optional[int] = None,
    replication_factor: Optional[int] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Resize a container's capacity / replication (reversible → prior).

    Args:
        ext_id: Storage container extId as returned by storage_container_list.
        max_capacity_bytes: New max capacity in bytes (omit to leave unchanged).
        replication_factor: New replication factor (omit to leave unchanged).
        target: Prism Central target name from config; omit for the default.
    """
    return ops.update_storage_container(_get_connection(target), ext_id,
                                       max_capacity_bytes=max_capacity_bytes,
                                       replication_factor=replication_factor)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def storage_container_delete(
    ext_id: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Delete a storage container. Destructive — pass dry_run=True first.

    Requires an approver (set NUTANIX_AUDIT_APPROVED_BY) under the graduated-autonomy
    policy. Captures the container's prior capacity/RF on the audit trail.

    Args:
        ext_id: Storage container extId as returned by storage_container_list.
        dry_run: If True, return what WOULD be deleted without deleting.
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        obj, _ = ops._container_raw(conn, ext_id)
        row = ops._norm_container(obj)
        return {"dryRun": True, "wouldDelete": {
            "extId": row["extId"] or ext_id, "name": row["name"],
            "maxCapacityBytes": row["maxCapacityBytes"],
            "replicationFactor": row["replicationFactor"]}}
    return ops.delete_storage_container(conn, ext_id)
