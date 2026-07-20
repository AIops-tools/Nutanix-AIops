"""LCM (Life Cycle Manager) inventory + upgrade actions (read + guarded writes).

Reads the LCM inventory of upgradable entities (firmware / software) and drives
the two LCM actions — precheck (read-safe validation) and update (the actual,
destructive firmware/software upgrade). Both actions post to cluster-scoped
``$actions`` endpoints and return the async task extId so an agent can track the
long-running upgrade. All server text passes through ``sanitize`` at the
``_util`` layer.

The precheck is **enforced**, not advised: :func:`perform_update` refuses unless
the caller hands back the extId of a precheck task that reached SUCCEEDED. See
:func:`require_passed_precheck`.
"""

from __future__ import annotations

import logging
from typing import Any

from nutanix_aiops.connection import NutanixApiError
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

_log = logging.getLogger("nutanix-aiops.lcm")

_ENTITIES = "/api/lifecycle/v4.0/resources/entities"
_PRECHECK = "/api/lifecycle/v4.0/resources/$actions/perform-precheck"
_UPDATE = "/api/lifecycle/v4.0/resources/$actions/perform-update"
_TASKS = "/api/prism/v4.0/config/tasks"

#: Task statuses that mean the precheck finished and passed. ``SUCCESS`` is
#: carried alongside the v4 ``SUCCEEDED`` only so a spelling difference on some
#: Prism Central build cannot wedge every update behind a false refusal.
_PRECHECK_PASSED = frozenset({"SUCCEEDED", "SUCCESS"})


class LcmPrecheckRequired(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: an LCM update was attempted without a precheck that passed."""


def _norm_entity(raw: dict) -> dict:
    """Fold one raw LCM entity record into the stable inventory shape."""
    current = opt(raw.get("currentVersion"))
    available = opt(raw.get("availableVersion"))
    return {
        "extId": ext_id(raw),
        "entityClass": opt(raw.get("entityClass")),
        "entityModel": opt(raw.get("entityModel")),
        "currentVersion": current,
        "availableVersion": available,
        "updateAvailable": bool(available) and available != current,
    }


def list_lcm_inventory(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """[READ] LCM-managed entities + upgrade availability, in a truncation envelope."""
    raw, truncated = fetch_page(conn, _ENTITIES, limit)
    return envelope("entities", [_norm_entity(r) for r in raw], limit, truncated)


def _update_specs(entity_ext_ids: list[str]) -> list[dict]:
    """Build the ``entityUpdateSpecs`` body shared by precheck and update."""
    return [{"entityExtId": s(e)} for e in entity_ext_ids]


def run_precheck(conn: Any, cluster_ext_id: str, entity_ext_ids: list[str]) -> dict:
    """[WRITE] Run LCM prechecks for the given entities (read-safe validation action)."""
    body = {
        "clusterExtId": cluster_ext_id,
        "entityUpdateSpecs": _update_specs(entity_ext_ids),
    }
    resp = as_obj(conn.post(_PRECHECK, json=body))
    return {
        "action": "lcm_precheck",
        "clusterExtId": s(cluster_ext_id),
        "entityCount": len(entity_ext_ids),
        "taskExtId": ext_id(resp),
    }


#: Remedy sentence shared by every precheck refusal, so the guard teaches the
#: same next step whichever way it fired.
_RUN_PRECHECK_FIRST = (
    "Run lcm_precheck with the same cluster_ext_id and entity_ext_ids, wait for the task "
    "it returns to reach SUCCEEDED (task_list reports task status), then call lcm_update "
    "again passing that task's taskExtId as precheck_task_ext_id."
)


def require_passed_precheck(conn: Any, precheck_task_ext_id: str) -> dict:
    """Raise :class:`LcmPrecheckRequired` unless a precheck task exists and passed.

    An LCM update reflashes firmware and reboots hosts. It has no inverse — no
    undo descriptor can be recorded for it — so the one validation LCM offers
    is made mandatory here rather than left to the caller's discretion, and the
    same check runs on the dry-run preview so a preview can never promise an
    update the real call would refuse.

    Refuses when no task extId is supplied, when the extId does not name a task
    (404 / empty record), and when the task reports any status other than
    SUCCEEDED. It FAILS OPEN in exactly one place: a task record that comes back
    carrying **no status field at all** is permitted, with a WARNING, because
    that is the API declining to answer rather than an answer of "did not pass".
    Any other transport or API failure propagates untouched — an update must not
    proceed on the strength of a lookup that never completed.

    Returns the verification result for the caller's audit payload.
    """
    task_id = str(precheck_task_ext_id or "").strip()
    if not task_id:
        raise LcmPrecheckRequired(
            "Refusing to run an LCM update: no precheck task was supplied, so nothing "
            f"shows this upgrade is safe to start. {_RUN_PRECHECK_FIRST}"
        )
    try:
        raw = conn.get(f"{_TASKS}/{_seg(task_id)}")
    except NutanixApiError as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        raise LcmPrecheckRequired(
            f"Refusing to run an LCM update: precheck task '{s(task_id)}' does not exist on "
            f"this Prism Central. Pass the taskExtId lcm_precheck returned, not an invented "
            f"or remembered id. {_RUN_PRECHECK_FIRST}"
        ) from exc
    obj = as_obj(raw)
    if not obj:
        raise LcmPrecheckRequired(
            f"Refusing to run an LCM update: precheck task '{s(task_id)}' returned an empty "
            f"record, so its outcome is unknown. {_RUN_PRECHECK_FIRST}"
        )
    status = str(obj.get("status") or "").strip().upper()
    if not status:
        _log.warning(
            "LCM precheck guard: task '%s' reported no status; proceeding WITHOUT "
            "confirmation that the precheck passed.", task_id,
        )
        return {"taskExtId": s(task_id), "status": None, "verified": False}
    if status not in _PRECHECK_PASSED:
        raise LcmPrecheckRequired(
            f"Refusing to run an LCM update: precheck task '{s(task_id)}' is {s(status)}, not "
            f"SUCCEEDED. Wait for it to finish, or — if it failed — fix what it reported and "
            f"run a fresh precheck; do not reuse this task's id. {_RUN_PRECHECK_FIRST}"
        )
    return {"taskExtId": s(task_id), "status": opt(obj.get("status")), "verified": True}


def preview_update(
    conn: Any, cluster_ext_id: str, entity_ext_ids: list[str], precheck_task_ext_id: str
) -> dict:
    """Guarded dry-run preview for :func:`perform_update` — reads only.

    Runs the SAME precheck requirement as the real update, so preview and write
    always agree on whether the upgrade is permitted to start.
    """
    precheck = require_passed_precheck(conn, precheck_task_ext_id)
    return {
        "clusterExtId": s(cluster_ext_id),
        "entities": [s(e) for e in entity_ext_ids],
        "entityCount": len(entity_ext_ids),
        "precheck": precheck,
    }


def perform_update(
    conn: Any, cluster_ext_id: str, entity_ext_ids: list[str], precheck_task_ext_id: str
) -> dict:
    """[WRITE][high] Perform the LCM firmware/software update for the given entities.

    Refuses unless ``precheck_task_ext_id`` names a precheck task that reached
    SUCCEEDED — see :func:`require_passed_precheck` for the exact condition and
    the single case it fails open on.
    """
    precheck = require_passed_precheck(conn, precheck_task_ext_id)
    body = {
        "clusterExtId": cluster_ext_id,
        "entityUpdateSpecs": _update_specs(entity_ext_ids),
    }
    resp = as_obj(conn.post(_UPDATE, json=body))
    return {
        "action": "lcm_update",
        "clusterExtId": s(cluster_ext_id),
        "entityCount": len(entity_ext_ids),
        "precheck": precheck,
        "taskExtId": ext_id(resp),
    }
