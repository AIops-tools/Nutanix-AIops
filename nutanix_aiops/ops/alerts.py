"""Alerts, events, audits, and alert root-cause analysis (read + guarded writes).

Reads Prism Central's serviceability alerts/events and the config audit trail via
the v4 monitoring/prism APIs and folds them into stable shapes so downstream
analysis never has to special-case raw payload field names. The two writes
(acknowledge / resolve) auto-fetch the alert's current ETag (via
``conn.get_with_etag``) and send it back as ``If-Match`` — the Prism v4 footgun
handled once, here. ``analyze_alert`` is the flagship value-add: it correlates an
alert with the recent events on the same affected entity and emits a small,
deterministic root-cause heuristic (probable cause + suggested actions). All
server text passes through ``sanitize`` at the ``_util`` layer.
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

_ALERTS = "/api/monitoring/v4.0/serviceability/alerts"
_EVENTS = "/api/monitoring/v4.0/serviceability/events"
_AUDITS = "/api/prism/v4.0/config/audits"


def _norm_alert(raw: dict) -> dict:
    """Fold one raw alert record into the stable shape."""
    return {
        "extId": ext_id(raw),
        "title": opt(raw.get("title")),
        "severity": opt(raw.get("severity")),
        "impactType": opt(raw.get("impactType")),
        "creationTime": opt(raw.get("creationTime")),
        "acknowledged": raw.get("acknowledged"),
        "resolved": raw.get("resolved"),
        "affectedEntityExtId": opt(raw.get("affectedEntityExtId")),
    }


def _norm_event(raw: dict) -> dict:
    """Fold one raw event record into the stable shape."""
    return {
        "extId": ext_id(raw),
        "title": opt(raw.get("title")),
        "creationTime": opt(raw.get("creationTime")),
        "sourceEntityExtId": opt(raw.get("sourceEntityExtId")),
    }


def _norm_audit(raw: dict) -> dict:
    """Fold one raw audit record into the stable shape."""
    return {
        "extId": ext_id(raw),
        "operationType": opt(raw.get("operationType")),
        "user": opt(raw.get("user")),
        "creationTime": opt(raw.get("creationTime")),
    }


def list_alerts(
    conn: Any, severity: str | None = None, limit: int = DEFAULT_LIST_LIMIT
) -> dict:
    """[READ] Alerts, normalised (auto-paginated); optional severity filter.

    Returns a ``{"alerts": [...], "returned": N, "limit": L, "truncated": bool}``
    envelope so a capped read announces itself.
    """
    params = {"$filter": f"severity eq '{severity}'"} if severity else None
    raw, truncated = fetch_page(conn, _ALERTS, limit, params=params)
    return envelope("alerts", [_norm_alert(r) for r in raw], limit, truncated)


def list_events(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """[READ] Events, normalised (auto-paginated), in a truncation-aware envelope."""
    raw, truncated = fetch_page(conn, _EVENTS, limit)
    return envelope("events", [_norm_event(r) for r in raw], limit, truncated)


def list_audits(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """[READ] Config audit records, normalised, in a truncation-aware envelope."""
    raw, truncated = fetch_page(conn, _AUDITS, limit)
    return envelope("audits", [_norm_audit(r) for r in raw], limit, truncated)


def acknowledge_alert(conn: Any, alert_ext_id: str) -> dict:
    """[WRITE] Acknowledge an alert, capturing its prior acknowledged state."""
    raw, etag = conn.get_with_etag(f"{_ALERTS}/{_seg(alert_ext_id)}")
    obj = as_obj(raw)
    prior = obj.get("acknowledged")
    conn.post(f"{_ALERTS}/{_seg(alert_ext_id)}/$actions/acknowledge", etag=etag, json={})
    return {"action": "acknowledge_alert", "extId": s(alert_ext_id),
            "priorState": {"acknowledged": prior}}


def resolve_alert(conn: Any, alert_ext_id: str) -> dict:
    """[WRITE] Resolve an alert, capturing its prior resolved state."""
    raw, etag = conn.get_with_etag(f"{_ALERTS}/{_seg(alert_ext_id)}")
    obj = as_obj(raw)
    prior = obj.get("resolved")
    conn.post(f"{_ALERTS}/{_seg(alert_ext_id)}/$actions/resolve", etag=etag, json={})
    return {"action": "resolve_alert", "extId": s(alert_ext_id),
            "priorState": {"resolved": prior}}


def _probable_cause(severity: str | None, impact_type: str | None) -> str:
    """Deterministic root-cause heuristic from severity + impactType (no clock/random)."""
    impact = (impact_type or "").lower()
    sev = (severity or "").lower()
    if "capacity" in impact:
        return ("A capacity threshold was crossed — a pool, container, or datastore "
                "is filling up and is the likely root cause.")
    if "performance" in impact:
        return ("A performance impact was reported — contention on CPU, memory, or "
                "storage latency on the affected entity is the likely root cause.")
    if "availability" in impact:
        return ("An availability impact was reported — a service, host, or VM on the "
                "affected entity likely went down or became unreachable.")
    if "configuration" in impact:
        return ("A configuration issue was flagged — a recent config change on the "
                "affected entity is the likely root cause.")
    if sev == "critical":
        return ("A critical alert with no specific impact type — inspect the affected "
                "entity directly; a hardware or service fault is the likely cause.")
    return ("No specific impact type was reported — review the related events below to "
            "localise the root cause on the affected entity.")


def _suggested_actions(severity: str | None, impact_type: str | None) -> list[str]:
    """Deterministic remediation hints from severity + impactType."""
    impact = (impact_type or "").lower()
    actions = ["Review the related events below for the earliest matching event."]
    if "capacity" in impact:
        actions.append("Free space or expand the affected pool/container/datastore.")
    elif "performance" in impact:
        actions.append("Check CPU/memory/storage-latency utilization on the entity.")
    elif "availability" in impact:
        actions.append("Verify the affected host/VM/service is up and reachable.")
    elif "configuration" in impact:
        actions.append("Audit recent configuration changes on the affected entity.")
    if (severity or "").lower() == "critical":
        actions.append("Escalate: this is a critical-severity alert.")
    actions.append("Acknowledge the alert once triaged, then resolve when fixed.")
    return actions


def analyze_alert(conn: Any, alert_ext_id: str) -> dict:
    """[READ] Root-cause summary: correlate an alert with recent same-entity events.

    Resilient: any failure yields an ``error`` field rather than a raised
    traceback (an RCA probe must survive the thing it probes). Deterministic — no
    clock or randomness — so the same inputs always produce the same summary.
    """
    try:
        raw = as_obj(conn.get(f"{_ALERTS}/{_seg(alert_ext_id)}"))
        alert = _norm_alert(raw)
        affected = alert["affectedEntityExtId"]
        related = [
            e for e in list_events(conn)["events"]
            if affected and e["sourceEntityExtId"] == affected
        ][:10]
        return {
            "alert": {"title": alert["title"], "severity": alert["severity"],
                      "affectedEntityExtId": affected},
            "relatedEvents": related,
            "probableCause": _probable_cause(alert["severity"], alert["impactType"]),
            "suggestedActions": _suggested_actions(alert["severity"], alert["impactType"]),
        }
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200), "alertExtId": s(alert_ext_id)}
