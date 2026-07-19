"""One-shot Prism Central estate overview (read-only).

Folds clusters, hosts, and VMs into a single summary an agent can call first:
how many clusters/hosts/VMs, the AHV-vs-ESXi split (the migration signal), and
the VM power-state spread. Resilient — a failing sub-call degrades to a partial
summary with an ``errors`` list rather than a raised traceback.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops import clusters as cl
from nutanix_aiops.ops import vms as vm


def _spread(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        value = str(r.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def fleet_overview(conn: Any) -> dict:
    """[READ] Estate summary: cluster/host/VM counts + hypervisor & power spread.

    ``truncated`` names any inventory that hit its row cap, so the counts are
    never silently understated.
    """
    errors: list[str] = []

    truncated: list[str] = []

    def _safe(fn: Any, label: str, key: str) -> list[dict]:
        """Call a list op, unwrap its envelope, and note if it was truncated."""
        try:
            result = fn(conn)
        except Exception as exc:  # noqa: BLE001 — collect, keep going
            errors.append(f"{label}: {str(exc)[:120]}")
            return []
        if result.get("truncated"):
            truncated.append(label)
        return result.get(key, [])

    clusters = _safe(cl.list_clusters, "clusters", "clusters")
    hosts = _safe(cl.list_hosts, "hosts", "hosts")
    vm_rows = _safe(vm.list_vms, "vms", "vms")

    return {
        "clusters": len(clusters),
        "hosts": len(hosts),
        "vms": len(vm_rows),
        "hypervisorSpread": _spread(vm_rows, "hypervisor"),
        "powerStateSpread": _spread(vm_rows, "powerState"),
        "truncated": truncated,
        "errors": errors,
    }
