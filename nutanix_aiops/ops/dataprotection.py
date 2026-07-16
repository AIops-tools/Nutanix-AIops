"""VM snapshots, recovery points, and protection domains (read + guarded writes).

The "snapshot sprawl" + DR surface. Reads normalise snapshots / recovery points
/ protection policies into stable shapes so an agent can spot lingering
snapshots without special-casing raw payloads. Every mutating call auto-fetches
the relevant entity's ETag (via ``conn.get_with_etag``) and sends it back as
``If-Match`` — the Prism v4 footgun handled once, here. Destructive writes
(snapshot delete / restore, PD failover) capture BEFORE-state into
``priorState`` for the audit trail; a snapshot *restore* reverts the VM and is
NOT safely reversible, so no undo is offered for it.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops._util import _seg, as_obj, ext_id, s

_VMS = "/api/vmm/v4.0/ahv/config/vms"
_RECOVERY_POINTS = "/api/dataprotection/v4.0/config/recovery-points"
_PROTECTION_POLICIES = "/api/dataprotection/v4.0/config/protection-policies"


def _snapshots_path(vm_ext_id: str) -> str:
    """Snapshots collection path for one VM."""
    return f"{_VMS}/{_seg(vm_ext_id)}/snapshots"


# ── reads ────────────────────────────────────────────────────────────────


def _norm_snapshot(raw: dict, vm_ext_id: str) -> dict:
    """Fold one raw VM snapshot record into the stable shape."""
    return {
        "extId": ext_id(raw),
        "name": s(raw.get("name")),
        "createTimeUsecs": raw.get("createTimeUsecs"),
        "vmExtId": s(raw.get("vmExtId") or vm_ext_id),
    }


def list_snapshots(conn: Any, vm_ext_id: str) -> list[dict]:
    """[READ] All snapshots for one VM, normalised (auto-paginated)."""
    return [_norm_snapshot(r, vm_ext_id) for r in conn.list_all(_snapshots_path(vm_ext_id))]


def _norm_recovery_point(raw: dict) -> dict:
    """Fold one raw recovery-point record into the stable shape."""
    return {
        "extId": ext_id(raw),
        "vmExtId": s(raw.get("vmExtId")),
        "createTimeUsecs": raw.get("createTimeUsecs"),
        "expirationTimeUsecs": raw.get("expirationTimeUsecs"),
        "locationType": s(raw.get("locationType")),
    }


def list_recovery_points(conn: Any) -> list[dict]:
    """[READ] All recovery points, normalised (auto-paginated)."""
    return [_norm_recovery_point(r) for r in conn.list_all(_RECOVERY_POINTS)]


def _norm_protection_domain(raw: dict) -> dict:
    """Fold one raw protection-policy record into the stable PD shape."""
    return {
        "extId": ext_id(raw),
        "name": s(raw.get("name")),
        "replicationType": s(raw.get("replicationType")),
    }


def list_protection_domains(conn: Any) -> list[dict]:
    """[READ] All protection domains / policies, normalised (auto-paginated)."""
    return [_norm_protection_domain(r) for r in conn.list_all(_PROTECTION_POLICIES)]


# ── writes ───────────────────────────────────────────────────────────────


def create_snapshot(conn: Any, vm_ext_id: str, name: str) -> dict:
    """[WRITE][low] Create a snapshot of a VM (reversible → delete the snapshot).

    The v4 create is async and returns a TASK extId, which is useless as an undo
    target. Best-effort: resolve the real snapshot extId by listing and matching
    the (caller-chosen) name — ``snapshotExtId`` is "" when the snapshot has not
    materialised yet, in which case no undo is recorded (honest degradation).
    """
    _raw, etag = conn.get_with_etag(f"{_VMS}/{_seg(vm_ext_id)}")
    resp = as_obj(conn.post(_snapshots_path(vm_ext_id), etag=etag, json={"name": name}))
    snapshot_ext_id = ""
    try:
        for snap in list_snapshots(conn, vm_ext_id):
            if snap.get("name") == name:
                snapshot_ext_id = snap.get("extId", "")
                break
    except Exception:  # noqa: BLE001 — best-effort resolution must not fail the write
        pass
    return {"action": "create_snapshot", "vmExtId": s(vm_ext_id), "name": s(name),
            "taskExtId": ext_id(resp), "snapshotExtId": s(snapshot_ext_id)}


def delete_snapshot(conn: Any, vm_ext_id: str, snapshot_ext_id: str) -> dict:
    """[WRITE][high] Delete a VM snapshot — captures the prior name for the audit trail."""
    path = f"{_snapshots_path(vm_ext_id)}/{_seg(snapshot_ext_id)}"
    raw, etag = conn.get_with_etag(path)
    obj = as_obj(raw)
    conn.delete(path, etag=etag)
    return {"action": "delete_snapshot", "vmExtId": s(vm_ext_id), "extId": s(snapshot_ext_id),
            "priorState": {"name": s(obj.get("name"))}}


def restore_snapshot(conn: Any, vm_ext_id: str, snapshot_ext_id: str) -> dict:
    """[WRITE][high] Revert a VM to a snapshot — destructive, NOT safely undoable."""
    raw, etag = conn.get_with_etag(f"{_VMS}/{_seg(vm_ext_id)}")
    obj = as_obj(raw)
    resp = as_obj(conn.post(f"{_VMS}/{_seg(vm_ext_id)}/$actions/revert", etag=etag,
                            json={"snapshotExtId": snapshot_ext_id}))
    return {"action": "restore_snapshot", "vmExtId": s(vm_ext_id),
            "snapshotExtId": s(snapshot_ext_id),
            "priorState": {"powerState": s(obj.get("powerState"))},
            "taskExtId": ext_id(resp)}


def protect_vm(conn: Any, vm_ext_id: str, policy_ext_id: str) -> dict:
    """[WRITE][medium] Associate a VM with a protection policy."""
    conn.post(f"{_PROTECTION_POLICIES}/{_seg(policy_ext_id)}/$actions/associate-vm",
              json={"vmExtId": vm_ext_id})
    return {"action": "protect_vm", "vmExtId": s(vm_ext_id), "policyExtId": s(policy_ext_id)}


def failover_pd(conn: Any, policy_ext_id: str, cluster_ext_id: str) -> dict:
    """[WRITE][high] Fail a protection domain over to a target cluster (DR event)."""
    resp = as_obj(conn.post(f"{_PROTECTION_POLICIES}/{_seg(policy_ext_id)}/$actions/failover",
                            json={"targetClusterExtId": cluster_ext_id}))
    return {"action": "failover_pd", "policyExtId": s(policy_ext_id),
            "targetClusterExtId": s(cluster_ext_id), "taskExtId": ext_id(resp)}
