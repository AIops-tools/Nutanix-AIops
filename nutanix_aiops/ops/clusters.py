"""Cluster, host, and utilization inventory (read-only).

Reads Prism Central's cluster and host inventory via the clustermgmt v4 API and
folds it into stable shapes so downstream analysis never has to special-case
raw payload field names. All server text passes through ``sanitize`` at the
``_util`` layer.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops._util import _seg, as_obj, ext_id, s

_CLUSTERS = "/api/clustermgmt/v4.0/config/clusters"
_HOSTS = "/api/clustermgmt/v4.0/config/hosts"


def _norm_cluster(raw: dict) -> dict:
    """Fold one raw cluster record into the stable inventory shape."""
    cfg = raw.get("config") or {}
    nodes = raw.get("nodes") or {}
    return {
        "extId": ext_id(raw),
        "name": s(raw.get("name") or cfg.get("name")),
        "aosVersion": s(cfg.get("buildInfo", {}).get("version") or cfg.get("aosVersion")),
        "hypervisorTypes": [s(h) for h in (cfg.get("hypervisorTypes") or [])],
        "nodeCount": nodes.get("numberOfNodes") if isinstance(nodes, dict) else None,
        "clusterFunction": [s(f) for f in (cfg.get("clusterFunction") or [])],
    }


def list_clusters(conn: Any) -> list[dict]:
    """[READ] All registered clusters, normalised (auto-paginated)."""
    return [_norm_cluster(r) for r in conn.list_all(_CLUSTERS)]


def get_cluster_health(conn: Any, cluster_ext_id: str) -> dict:
    """[READ] One cluster's health summary: services, resiliency, node rollup.

    Resilient: a failing detail call yields an ``error`` field rather than a
    raised traceback (a health probe must survive the thing it probes).
    """
    try:
        raw = as_obj(conn.get(f"{_CLUSTERS}/{_seg(cluster_ext_id)}"))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200), "clusterExtId": s(cluster_ext_id)}
    cluster = _norm_cluster(raw)
    upgrade = raw.get("upgradeStatus")
    return {
        **cluster,
        "upgradeStatus": s(upgrade) if upgrade else None,
        "resiliencyState": s((raw.get("config") or {}).get("faultToleranceState")),
    }


def _norm_host(raw: dict) -> dict:
    """Fold one raw host record into the stable inventory shape."""
    return {
        "extId": ext_id(raw),
        "name": s(raw.get("hostName") or raw.get("name")),
        "clusterExtId": s((raw.get("cluster") or {}).get("uuid") or raw.get("clusterExtId")),
        "hypervisor": s((raw.get("hypervisor") or {}).get("type") or raw.get("hypervisorType")),
        "numCpuCores": raw.get("numberOfCpuCores"),
        "memoryBytes": raw.get("memorySizeBytes"),
        "bootTimeUsecs": raw.get("bootTimeUsecs"),
    }


def list_hosts(conn: Any) -> list[dict]:
    """[READ] All hosts across clusters, normalised (auto-paginated)."""
    return [_norm_host(r) for r in conn.list_all(_HOSTS)]


def get_cluster_utilization(conn: Any, cluster_ext_id: str) -> dict:
    """[READ] Point-in-time CPU / memory / storage utilization for one cluster.

    Reads the cluster's ``stats`` block if present; absent fields come back as
    ``None`` rather than invented values.
    """
    try:
        raw = as_obj(conn.get(f"{_CLUSTERS}/{_seg(cluster_ext_id)}"))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200), "clusterExtId": s(cluster_ext_id)}
    stats = raw.get("stats") or {}
    return {
        "clusterExtId": ext_id(raw),
        "name": s(raw.get("name")),
        "cpuUsagePercent": stats.get("hypervisorCpuUsagePpm"),
        "memoryUsagePercent": stats.get("hypervisorMemoryUsagePpm"),
        "storageUsageBytes": stats.get("storageUsageBytes"),
        "storageCapacityBytes": stats.get("storageCapacityBytes"),
        "iops": stats.get("controllerNumIops"),
    }
