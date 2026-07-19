"""Tasks and capacity-runway insight (read-only).

Two read-only lenses onto a Prism Central estate:

  * ``list_tasks`` — the v4 task feed (running / completed operations), folded
    into a stable shape so an agent can watch async work it (or something else)
    kicked off.
  * ``get_capacity_runway`` — a deterministic storage-runway estimate for one
    cluster. Given the cluster's current usage/capacity and a caller-supplied
    ``daily_growth_bytes``, it projects days-to-full by pure arithmetic — no
    clock, no randomness, so the same inputs always yield the same forecast.
    Without a growth rate it reports ``insufficient-data`` rather than guessing.

All server-returned text passes through ``sanitize`` at the ``_util`` layer.
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

_TASKS = "/api/prism/v4.0/config/tasks"
_CLUSTERS = "/api/clustermgmt/v4.0/config/clusters"


def _norm_task(raw: dict) -> dict:
    """Fold one raw task record into the stable shape."""
    return {
        "extId": ext_id(raw),
        "operation": opt(raw.get("operation") or raw.get("operationType")),
        "status": opt(raw.get("status")),
        "percentageComplete": raw.get("percentageComplete"),
        "createdTime": opt(raw.get("createdTime")),
        "entityExtId": opt((raw.get("entitiesAffected") or [{}])[0].get("extId")
                         if raw.get("entitiesAffected") else raw.get("entityExtId")),
    }


def list_tasks(
    conn: Any, status: str | None = None, limit: int = DEFAULT_LIST_LIMIT
) -> dict:
    """[READ] Tasks, normalised (auto-paginated); optionally filter by status.

    ``status`` (e.g. ``RUNNING`` / ``SUCCEEDED`` / ``FAILED``) becomes a v4
    ``$filter`` so the server does the narrowing rather than the client.
    Returns a ``{"tasks": [...], "returned", "limit", "truncated"}`` envelope.
    """
    params = {"$filter": f"status eq '{status}'"} if status else None
    raw, truncated = fetch_page(conn, _TASKS, limit, params=params)
    return envelope("tasks", [_norm_task(r) for r in raw], limit, truncated)


def get_capacity_runway(
    conn: Any, cluster_ext_id: str, daily_growth_bytes: int | None = None
) -> dict:
    """[READ] Deterministic storage-runway estimate for one cluster.

    Reads the cluster's ``stats`` block for used/total storage. With a positive
    ``daily_growth_bytes`` it projects ``daysToFull = freeBytes // growth`` and
    reports ``forecast="ok"``; without one (or with missing usage/capacity) it
    reports ``forecast="insufficient-data"`` and ``daysToFull=None``. Resilient:
    a failing read yields an ``error`` field rather than raising.
    """
    try:
        raw = as_obj(conn.get(f"{_CLUSTERS}/{_seg(cluster_ext_id)}"))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200), "clusterExtId": s(cluster_ext_id)}

    stats = raw.get("stats") or {}
    usage = stats.get("storageUsageBytes")
    capacity = stats.get("storageCapacityBytes")
    have_both = isinstance(usage, (int, float)) and isinstance(capacity, (int, float))

    free_bytes = capacity - usage if have_both else None
    used_percent = round(usage / capacity * 100, 2) if have_both and capacity else None

    days_to_full: int | None = None
    forecast = "insufficient-data"
    if have_both and daily_growth_bytes and daily_growth_bytes > 0 and free_bytes is not None:
        days_to_full = int(free_bytes // daily_growth_bytes)
        forecast = "ok"

    return {
        "clusterExtId": ext_id(raw) or s(cluster_ext_id),
        "usageBytes": usage,
        "capacityBytes": capacity,
        "freeBytes": free_bytes,
        "usedPercent": used_percent,
        "daysToFull": days_to_full,
        "forecast": forecast,
    }
