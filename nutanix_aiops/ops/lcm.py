"""LCM (Life Cycle Manager) inventory + upgrade actions (read + guarded writes).

Reads the LCM inventory of upgradable entities (firmware / software) and drives
the two LCM actions — precheck (read-safe validation) and update (the actual,
destructive firmware/software upgrade). Both actions post to cluster-scoped
``$actions`` endpoints and return the async task extId so an agent can track the
long-running upgrade. All server text passes through ``sanitize`` at the
``_util`` layer.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops._util import as_obj, ext_id, s

_ENTITIES = "/api/lifecycle/v4.0/resources/entities"
_PRECHECK = "/api/lifecycle/v4.0/resources/$actions/perform-precheck"
_UPDATE = "/api/lifecycle/v4.0/resources/$actions/perform-update"


def _norm_entity(raw: dict) -> dict:
    """Fold one raw LCM entity record into the stable inventory shape."""
    current = s(raw.get("currentVersion"))
    available = s(raw.get("availableVersion"))
    return {
        "extId": ext_id(raw),
        "entityClass": s(raw.get("entityClass")),
        "entityModel": s(raw.get("entityModel")),
        "currentVersion": current,
        "availableVersion": available,
        "updateAvailable": bool(available) and available != current,
    }


def list_lcm_inventory(conn: Any) -> list[dict]:
    """[READ] All LCM-managed entities and their upgrade availability (auto-paginated)."""
    return [_norm_entity(r) for r in conn.list_all(_ENTITIES)]


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


def perform_update(conn: Any, cluster_ext_id: str, entity_ext_ids: list[str]) -> dict:
    """[WRITE][high] Perform the LCM firmware/software update for the given entities."""
    body = {
        "clusterExtId": cluster_ext_id,
        "entityUpdateSpecs": _update_specs(entity_ext_ids),
    }
    resp = as_obj(conn.post(_UPDATE, json=body))
    return {
        "action": "lcm_update",
        "clusterExtId": s(cluster_ext_id),
        "entityCount": len(entity_ext_ids),
        "taskExtId": ext_id(resp),
    }
