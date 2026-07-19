"""Flagship signature analyses over Prism Central telemetry (pure analysis).

Nutanix-AIops already ships a *per-alert* correlator (``ops.alerts.analyze_alert``).
These two analyses work one level up — across the whole registered estate — and
share the line's differentiator: a **transparent** RCA, where every finding
carries the measured number or state that tripped it, so an operator sees *why*
something was flagged rather than a black-box verdict.

  1. ``cluster_health_findings`` — cluster resiliency / fault-tolerance state,
     storage pool + storage-container usage over threshold, and nodes that are
     down or missing from host inventory.
  2. ``alert_triage_findings`` — active (unresolved) Prism alerts grouped by
     severity with a per-level count, unacknowledged criticals, and the oldest
     unresolved alert surfaced with its age.

Both are pure functions (no I/O, no clock, no randomness): pass them the
normalised rows produced by ``ops.clusters`` / ``ops.storage`` / ``ops.alerts``
and they return the analysis. The MCP and CLI layers do the collection; keeping
the heuristics pure makes them trivially unit-testable without a live Prism
Central, and makes the same input always yield the same output.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Thresholds that flip a signal on. Each is surfaced in the finding text next to
# the measured value so the ranking is auditable, not opaque.
STORAGE_WARN_PCT = 80.0
STORAGE_CRIT_PCT = 90.0

# An unresolved alert older than this (relative to the newest alert observed in
# the same feed) is called out as stale triage.
STALE_ALERT_DAYS = 7.0

# Cluster fault-tolerance / node states that count as healthy. Anything else is
# reported with the raw state string so the operator sees Prism's own wording.
_HEALTHY_STATES = {"NORMAL", "OK", "HEALTHY", "kNormal", "kOk"}

# Severity ordering used to rank findings most-urgent first.
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

# Prism alert severities, mapped onto this module's finding severities.
_ALERT_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "WARNING": "warning",
    "INFO": "info",
    "INFORMATIONAL": "info",
}


def _finding(
    severity: str, resource: str, signal: str, detail: str, cause: str, action: str
) -> dict:
    """Build one cited finding (a fresh dict — callers never mutate inputs)."""
    return {
        "severity": severity,
        "resource": resource,
        "signal": signal,
        "detail": detail,
        "cause": cause,
        "action": action,
    }


def _rank(findings: list[dict]) -> list[dict]:
    """Return findings most-urgent first, each carrying its explicit 1-based rank.

    The priority is stated in the payload rather than left implicit in list
    order: a consumer — notably a smaller local model summarising the result —
    should never have to infer urgency from position. Returns new dicts; the
    inputs are not mutated.
    """
    ordered = sorted(findings, key=lambda f: _SEVERITY_RANK.get(f["severity"], 9))
    return [{**finding, "rank": i} for i, finding in enumerate(ordered, 1)]


def _pct(used: Any, total: Any) -> float | None:
    """Percentage used, or None when either side is missing / non-numeric / zero."""
    try:
        u = float(used)
        t = float(total)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return round(u / t * 100.0, 1)


def _is_healthy_state(state: Any) -> bool:
    """True when a resiliency / node state string reads as healthy (or is absent)."""
    if state is None or state == "":
        return True  # absent telemetry is not evidence of a fault
    return str(state).strip().upper() in {s.upper() for s in _HEALTHY_STATES}


def _usage_finding(resource: str, kind: str, pct: float, hint: str) -> dict:
    """Build the warn/critical finding for a storage object over threshold."""
    critical = pct >= STORAGE_CRIT_PCT
    threshold = STORAGE_CRIT_PCT if critical else STORAGE_WARN_PCT
    return _finding(
        "critical" if critical else "warning",
        resource,
        f"{kind} near full",
        f"{pct}% used >= {threshold}% threshold",
        "Nutanix reserves headroom for rebuild after a node/disk failure; a full "
        "container blocks new writes and can break self-healing.",
        hint,
    )


def _cluster_findings(report: dict) -> list[dict]:
    """Resiliency, probe-failure, and pool-usage findings for one cluster report."""
    name = str(report.get("name") or report.get("clusterExtId") or report.get("extId") or "?")
    out: list[dict] = []
    if report.get("error"):
        out.append(_finding(
            "warning", name, "health probe failed",
            f"cluster read returned: {report['error']}",
            "Prism Central could not return this cluster's detail record.",
            "Run 'nutanix-aiops doctor', then retry; check PC-to-cluster connectivity.",
        ))
        return out
    resiliency = report.get("resiliencyState")
    if not _is_healthy_state(resiliency):
        out.append(_finding(
            "critical", name, "cluster resiliency degraded",
            f"faultToleranceState = {resiliency}",
            "The cluster cannot currently tolerate its configured failure domain — "
            "a further node or disk loss risks data unavailability.",
            "Check host/disk health and let curator rebuild before any maintenance.",
        ))
    if report.get("upgradeStatus") and not _is_healthy_state(report.get("upgradeStatus")):
        out.append(_finding(
            "info", name, "upgrade in progress",
            f"upgradeStatus = {report['upgradeStatus']}",
            "An AOS/LCM upgrade is mid-flight; resiliency and performance dip during it.",
            "Let the upgrade finish before starting writes; watch 'lcm_inventory'.",
        ))
    pool_pct = _pct(report.get("storageUsageBytes"), report.get("storageCapacityBytes"))
    if pool_pct is not None and pool_pct >= STORAGE_WARN_PCT:
        out.append(_usage_finding(
            name, "cluster storage pool", pool_pct,
            "Free space (delete stale snapshots/recovery points) or add capacity nodes.",
        ))
    return out


def _node_findings(report: dict, host_rows: list[dict]) -> list[dict]:
    """Node-down and missing-host findings for one cluster's hosts."""
    name = str(report.get("name") or report.get("clusterExtId") or report.get("extId") or "?")
    cluster_id = str(report.get("extId") or report.get("clusterExtId") or "")
    mine = [h for h in host_rows if str(h.get("clusterExtId") or "") == cluster_id]
    out: list[dict] = []
    for host in mine:
        state = host.get("nodeStatus")
        if not _is_healthy_state(state):
            out.append(_finding(
                "critical", str(host.get("name") or host.get("extId") or "?"),
                "node not healthy",
                f"nodeStatus = {state} (cluster {name})",
                "A node is down, in maintenance, or detached from the cluster.",
                "Check the node's power/CVM state; VMs on it need HA restart elsewhere.",
            ))
    expected = report.get("nodeCount")
    if isinstance(expected, int) and cluster_id and mine and len(mine) < expected:
        out.append(_finding(
            "critical", name, "node missing from inventory",
            f"{len(mine)} host(s) visible, cluster reports nodeCount={expected}",
            "A node stopped reporting to Prism Central — likely down or partitioned.",
            "Reconcile against Prism Element; investigate the unreported node.",
        ))
    return out


def cluster_health_findings(
    cluster_reports: list[dict],
    host_rows: list[dict],
    container_rows: list[dict],
) -> dict:
    """[ANALYSIS] Estate health: resiliency state, storage headroom, nodes down.

    Args:
        cluster_reports: one dict per cluster, merging ``get_cluster_health``
            (``name``/``extId``/``resiliencyState``/``upgradeStatus``/``nodeCount``)
            with ``get_cluster_utilization`` (``storageUsageBytes`` /
            ``storageCapacityBytes``). A report carrying ``error`` is reported as
            a probe failure rather than skipped silently.
        host_rows: normalised rows from ``ops.clusters.list_hosts``.
        container_rows: normalised rows from ``ops.storage.list_storage_containers``.

    Returns the worst-first ``findings`` list plus a ``summary`` of every measured
    percentage, so the numbers behind the verdict are always visible.
    """
    findings: list[dict] = []
    summary: list[dict] = []
    for report in cluster_reports:
        findings.extend(_cluster_findings(report))
        findings.extend(_node_findings(report, host_rows))
        summary.append({
            "cluster": str(report.get("name") or report.get("clusterExtId") or "?"),
            "resiliencyState": report.get("resiliencyState"),
            "storagePoolPct": _pct(
                report.get("storageUsageBytes"), report.get("storageCapacityBytes")
            ),
        })
    for container in container_rows:
        cname = str(container.get("name") or container.get("extId") or "?")
        pct = _pct(container.get("logicalUsageBytes"), container.get("maxCapacityBytes"))
        summary.append({"container": cname, "usedPct": pct})
        if pct is not None and pct >= STORAGE_WARN_PCT:
            findings.append(_usage_finding(
                cname, "storage container", pct,
                "Reclaim space in this container or raise its maxCapacityBytes "
                "(storage_container_update) if the pool has headroom.",
            ))
    return {
        "findings": _rank(findings),
        "summary": summary,
        "clustersAnalyzed": len(cluster_reports),
        "hostsAnalyzed": len(host_rows),
        "containersAnalyzed": len(container_rows),
    }


def _parse_time(value: Any) -> datetime | None:
    """Parse a Prism ISO-8601 timestamp, or None when absent / unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_days(then: datetime | None, reference: datetime | None) -> float | None:
    """Whole-ish days between two timestamps, or None when either is missing."""
    if then is None or reference is None:
        return None
    try:
        return round((reference - then).total_seconds() / 86400.0, 1)
    except (TypeError, OverflowError):
        return None


def _severity_findings(counts: dict[str, int], unacked_critical: int) -> list[dict]:
    """One finding per populated severity bucket, plus the unacknowledged-critical gap."""
    out: list[dict] = []
    for raw_sev, count in counts.items():
        mapped = _ALERT_SEVERITY_MAP.get(raw_sev.upper(), "info")
        out.append(_finding(
            mapped, "estate", f"{count} active {raw_sev} alert(s)",
            f"{count} unresolved alert(s) at severity {raw_sev}",
            "These alerts are still open in Prism Central and nothing has cleared them.",
            f"Triage the {raw_sev} bucket with 'analyze_alert <extId>', then "
            "acknowledge and resolve each once handled.",
        ))
    if unacked_critical:
        out.append(_finding(
            "critical", "estate", "unacknowledged critical alerts",
            f"{unacked_critical} critical alert(s) not yet acknowledged",
            "No operator has claimed these — they are at risk of going unowned.",
            "Acknowledge them (alert_acknowledge) so ownership is recorded, then fix.",
        ))
    return out


def alert_triage_findings(alert_rows: list[dict], now_iso: str | None = None) -> dict:
    """[ANALYSIS] Group active Prism alerts by severity and surface the oldest.

    Args:
        alert_rows: normalised rows from ``ops.alerts.list_alerts`` (``severity``,
            ``title``, ``creationTime``, ``acknowledged``, ``resolved``).
        now_iso: reference timestamp for age arithmetic. Omit it and the **newest
            alert observed in this same feed** is used as the reference — keeping
            the analysis clock-free and therefore deterministic and reproducible.

    Returns the worst-first ``findings`` list, the per-severity ``severityCounts``,
    and ``oldestUnresolved`` (title + severity + age in days).
    """
    active = [a for a in alert_rows if not a.get("resolved")]
    counts: dict[str, int] = {}
    for alert in active:
        sev = str(alert.get("severity") or "UNKNOWN")
        counts[sev] = counts.get(sev, 0) + 1
    unacked_critical = sum(
        1 for a in active
        if str(a.get("severity") or "").upper() == "CRITICAL" and not a.get("acknowledged")
    )
    findings = _severity_findings(counts, unacked_critical)

    timed = [(a, _parse_time(a.get("creationTime"))) for a in active]
    timed = [(a, t) for a, t in timed if t is not None]
    reference = _parse_time(now_iso) or (max(t for _, t in timed) if timed else None)
    oldest: dict | None = None
    if timed:
        alert, created = min(timed, key=lambda pair: pair[1])
        age = _age_days(created, reference)
        oldest = {
            "extId": alert.get("extId"),
            "title": alert.get("title"),
            "severity": alert.get("severity"),
            "creationTime": alert.get("creationTime"),
            "ageDays": age,
        }
        if age is not None and age >= STALE_ALERT_DAYS:
            findings.append(_finding(
                "warning", str(alert.get("title") or alert.get("extId") or "?"),
                "stale unresolved alert",
                f"open for {age} day(s) >= {STALE_ALERT_DAYS} day threshold "
                f"(severity {alert.get('severity')})",
                "An alert this old is either unowned or a chronic condition nobody "
                "has cleared; either way it is eroding signal quality.",
                "Resolve it (alert_resolve) or fix the underlying condition — stale "
                "alerts train operators to ignore the feed.",
            ))
    return {
        "findings": _rank(findings),
        "severityCounts": counts,
        "oldestUnresolved": oldest,
        "activeAlerts": len(active),
        "alertsAnalyzed": len(alert_rows),
    }
