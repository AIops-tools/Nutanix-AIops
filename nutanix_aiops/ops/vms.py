"""VM inventory and lifecycle (read + guarded writes).

Every mutating call auto-fetches the VM's current ETag (via
``conn.get_with_etag``) and sends it back as ``If-Match`` on the mutation — the
Prism v4 footgun handled once, here, so no tool has to. Reversible writes
capture the VM's BEFORE state into ``priorState`` so the harness can record a
faithful undo (e.g. re-power, restore the prior CPU/memory).

``list_vms`` surfaces BOTH AHV and ESXi-backed VMs (Prism Central sees both in
hypervisor-migration estates); the ``hypervisor`` field distinguishes them so
an agent isn't blind to the half of the estate still on ESXi.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops._util import (
    DEFAULT_LIST_LIMIT,
    _seg,
    as_obj,
    envelope,
    ext_id,
    fetch_page,
    opt,
    s,
)

_VMS = "/api/vmm/v4.0/ahv/config/vms"


def _norm_vm(raw: dict) -> dict:
    """Fold one raw VM record into the stable inventory shape."""
    cluster = raw.get("cluster") or {}
    host = raw.get("host") or {}
    nics = raw.get("nics") or []
    ips: list[str] = []
    for nic in nics:
        for ip in ((nic.get("networkInfo") or {}).get("ipv4Config") or {}).get("ipAddress", []) \
                if isinstance(nic, dict) else []:
            if isinstance(ip, dict) and ip.get("value"):
                ips.append(s(ip["value"]))
    hypervisor = raw.get("hypervisorType") or (raw.get("source") or {}).get("entityType") or "AHV"
    return {
        "extId": ext_id(raw),
        "name": opt(raw.get("name")),
        "powerState": opt(raw.get("powerState")),
        "hypervisor": s(hypervisor),
        "numSockets": raw.get("numSockets"),
        "numCoresPerSocket": raw.get("numCoresPerSocket"),
        "memoryBytes": raw.get("memorySizeBytes"),
        "clusterExtId": opt(cluster.get("extId")),
        "hostExtId": opt(host.get("extId")),
        "ipAddresses": ips,
    }


def list_vms(
    conn: Any, include_esxi: bool = True, limit: int = DEFAULT_LIST_LIMIT
) -> dict:
    """[READ] VMs (AHV + ESXi), normalised, in a truncation-aware envelope.

    Set ``include_esxi=False`` to return only AHV-native VMs. Note the ESXi
    filter is applied *after* the page is fetched, so ``returned`` can be lower
    than ``limit`` while ``truncated`` is still true.
    """
    raw, truncated = fetch_page(conn, _VMS, limit)
    rows = [_norm_vm(r) for r in raw]
    if not include_esxi:
        rows = [r for r in rows if (r["hypervisor"] or "").upper() == "AHV"]
    return envelope("vms", rows, limit, truncated)


def get_vm(conn: Any, vm_ext_id: str) -> dict:
    """[READ] One VM by extId, normalised, with its current ETag surfaced.

    The ``_etag`` is what any downstream mutation needs for If-Match; exposing it
    on the read lets an agent chain get→update without a second round trip.
    """
    raw, etag = conn.get_with_etag(f"{_VMS}/{_seg(vm_ext_id)}")
    obj = as_obj(raw)
    if not obj:
        raise KeyError(f"VM '{vm_ext_id}' not found.")
    result = _norm_vm(obj)
    result["_etag"] = opt(etag)
    return result


def _vm_raw(conn: Any, vm_ext_id: str) -> tuple[dict, str]:
    """Fetch a VM's raw record + ETag, raising KeyError if absent."""
    raw, etag = conn.get_with_etag(f"{_VMS}/{_seg(vm_ext_id)}")
    obj = as_obj(raw)
    if not obj:
        raise KeyError(f"VM '{vm_ext_id}' not found.")
    return obj, etag


def _power_action(conn: Any, vm_ext_id: str, action: str) -> dict:
    """Run a power ``$action`` with the VM's current ETag, capturing prior power state."""
    obj, etag = _vm_raw(conn, vm_ext_id)
    prior = opt(obj.get("powerState"))
    conn.post(f"{_VMS}/{_seg(vm_ext_id)}/$actions/{action}", etag=etag, json={})
    return {
        "action": action,
        "extId": s(vm_ext_id),
        "name": opt(obj.get("name")),
        "priorState": {"powerState": prior},
    }


def power_on(conn: Any, vm_ext_id: str) -> dict:
    """[WRITE] Power on a VM (reversible → power-off)."""
    return _power_action(conn, vm_ext_id, "power-on")


def power_off(conn: Any, vm_ext_id: str) -> dict:
    """[WRITE] Hard power off a VM (reversible → power-on)."""
    return _power_action(conn, vm_ext_id, "power-off")


def guest_shutdown(conn: Any, vm_ext_id: str) -> dict:
    """[WRITE] Graceful in-guest shutdown (reversible → power-on)."""
    return _power_action(conn, vm_ext_id, "shutdown")


def reboot_vm(conn: Any, vm_ext_id: str) -> dict:
    """[WRITE] Reboot a VM (no distinct inverse)."""
    return _power_action(conn, vm_ext_id, "reboot")


def create_vm(
    conn: Any,
    name: str,
    cluster_ext_id: str,
    num_sockets: int = 1,
    memory_bytes: int = 4 * 1024**3,
) -> dict:
    """[WRITE] Create a minimal VM on a cluster."""
    spec = {
        "name": name,
        "cluster": {"extId": cluster_ext_id},
        "numSockets": num_sockets,
        "numCoresPerSocket": 1,
        "memorySizeBytes": memory_bytes,
    }
    resp = as_obj(conn.post(_VMS, json=spec))
    return {"action": "create_vm", "name": s(name), "clusterExtId": s(cluster_ext_id),
            "taskExtId": ext_id(resp)}


def update_vm(
    conn: Any,
    vm_ext_id: str,
    num_sockets: int | None = None,
    memory_bytes: int | None = None,
) -> dict:
    """[WRITE] Resize a VM's CPU sockets and/or memory (reversible → prior values)."""
    obj, etag = _vm_raw(conn, vm_ext_id)
    prior = {"numSockets": obj.get("numSockets"), "memoryBytes": obj.get("memorySizeBytes")}
    body = dict(obj)
    if num_sockets is not None:
        body["numSockets"] = num_sockets
    if memory_bytes is not None:
        body["memorySizeBytes"] = memory_bytes
    conn.put(f"{_VMS}/{_seg(vm_ext_id)}", etag=etag, json=body)
    return {"action": "update_vm", "extId": s(vm_ext_id), "name": opt(obj.get("name")),
            "priorState": prior}


def clone_vm(conn: Any, vm_ext_id: str, new_name: str) -> dict:
    """[WRITE] Clone a VM to a new name (reversible → delete the clone)."""
    obj, etag = _vm_raw(conn, vm_ext_id)
    resp = as_obj(conn.post(f"{_VMS}/{_seg(vm_ext_id)}/$actions/clone", etag=etag,
                            json={"name": new_name}))
    return {"action": "clone_vm", "sourceExtId": s(vm_ext_id), "newName": s(new_name),
            "taskExtId": ext_id(resp)}


def delete_vm(conn: Any, vm_ext_id: str) -> dict:
    """[WRITE][high] Delete a VM — captures the prior name/power state for the audit trail."""
    obj, etag = _vm_raw(conn, vm_ext_id)
    conn.delete(f"{_VMS}/{_seg(vm_ext_id)}", etag=etag)
    return {"action": "delete_vm", "extId": s(vm_ext_id), "name": opt(obj.get("name")),
            "priorState": {"powerState": opt(obj.get("powerState"))}}


def migrate_vm(conn: Any, vm_ext_id: str, target_host_ext_id: str) -> dict:
    """[WRITE][high] Live-migrate a VM to another host."""
    obj, etag = _vm_raw(conn, vm_ext_id)
    prior_host = opt((obj.get("host") or {}).get("extId"))
    resp = as_obj(conn.post(
        f"{_VMS}/{_seg(vm_ext_id)}/$actions/migrate",
        etag=etag,
        json={"targetHost": {"extId": target_host_ext_id}},
    ))
    return {"action": "migrate_vm", "extId": s(vm_ext_id), "name": opt(obj.get("name")),
            "targetHostExtId": s(target_host_ext_id),
            "priorState": {"hostExtId": prior_host}, "taskExtId": ext_id(resp)}
