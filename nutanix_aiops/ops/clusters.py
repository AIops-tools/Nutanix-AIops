"""Cluster, host, and utilization inventory (read-only).

Reads Prism Central's cluster and host inventory via the clustermgmt v4 API and
folds it into stable shapes so downstream analysis never has to special-case
raw payload field names. All server text passes through ``sanitize`` at the
``_util`` layer.
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

_CLUSTERS = "/api/clustermgmt/v4.0/config/clusters"
_HOSTS = "/api/clustermgmt/v4.0/config/hosts"


def _norm_cluster(raw: dict) -> dict:
    """Fold one raw cluster record into the stable inventory shape."""
    cfg = raw.get("config") or {}
    nodes = raw.get("nodes") or {}
    return {
        "extId": ext_id(raw),
        "name": opt(raw.get("name") or cfg.get("name")),
        "aosVersion": opt(cfg.get("buildInfo", {}).get("version") or cfg.get("aosVersion")),
        "hypervisorTypes": [s(h) for h in (cfg.get("hypervisorTypes") or [])],
        "nodeCount": nodes.get("numberOfNodes") if isinstance(nodes, dict) else None,
        "clusterFunction": [s(f) for f in (cfg.get("clusterFunction") or [])],
    }


def list_clusters(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """[READ] Registered clusters, normalised, in a truncation-aware envelope."""
    raw, truncated = fetch_page(conn, _CLUSTERS, limit)
    return envelope("clusters", [_norm_cluster(r) for r in raw], limit, truncated)


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
        "resiliencyState": opt((raw.get("config") or {}).get("faultToleranceState")),
    }


def _norm_host(raw: dict) -> dict:
    """Fold one raw host record into the stable inventory shape."""
    return {
        "extId": ext_id(raw),
        "name": opt(raw.get("hostName") or raw.get("name")),
        "clusterExtId": opt((raw.get("cluster") or {}).get("uuid") or raw.get("clusterExtId")),
        "hypervisor": opt((raw.get("hypervisor") or {}).get("type") or raw.get("hypervisorType")),
        "nodeStatus": opt(raw.get("nodeStatus") or raw.get("state")),
        "numCpuCores": raw.get("numberOfCpuCores"),
        "memoryBytes": raw.get("memorySizeBytes"),
        "bootTimeUsecs": raw.get("bootTimeUsecs"),
    }


def list_hosts(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """[READ] Hosts across clusters, normalised, in a truncation-aware envelope."""
    raw, truncated = fetch_page(conn, _HOSTS, limit)
    return envelope("hosts", [_norm_host(r) for r in raw], limit, truncated)


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
        "name": opt(raw.get("name")),
        "cpuUsagePercent": stats.get("hypervisorCpuUsagePpm"),
        "memoryUsagePercent": stats.get("hypervisorMemoryUsagePpm"),
        "storageUsageBytes": stats.get("storageUsageBytes"),
        "storageCapacityBytes": stats.get("storageCapacityBytes"),
        "iops": stats.get("controllerNumIops"),
    }


def list_cluster_reports(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> list[dict]:
    """[READ] Per-cluster health merged with its utilization, for every cluster.

    The single collection step behind the diagnostics layer: one dict per cluster
    carrying resiliency/upgrade/nodeCount (health) plus storage usage/capacity
    (utilization). Resilient by construction — ``get_cluster_health`` and
    ``get_cluster_utilization`` each degrade to an ``error`` field, and a health
    error is preserved rather than masked by the utilization merge.

    Returns a bare list: it is an internal collection step for the diagnostics
    analyses, which report their own ``clustersAnalyzed`` count. The MCP-facing
    truncation envelope lives on ``list_clusters``.
    """
    reports: list[dict] = []
    for cluster in list_clusters(conn, limit=limit)["clusters"]:
        cid = cluster["extId"]
        health = get_cluster_health(conn, cid)
        if health.get("error"):
            reports.append({**cluster, **health})
            continue
        util = get_cluster_utilization(conn, cid)
        storage = {
            k: util.get(k) for k in ("storageUsageBytes", "storageCapacityBytes")
            if not util.get("error")
        }
        reports.append({**cluster, **health, **storage})
    return reports
