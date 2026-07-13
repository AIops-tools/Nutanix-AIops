"""Storage container inventory and lifecycle (read + guarded writes).

Reads Prism Central's storage containers via the clustermgmt v4 API and folds
each into a stable shape so downstream analysis never special-cases raw payload
field names. Every mutating call auto-fetches the container's current ETag (via
``conn.get_with_etag``) and sends it back as ``If-Match`` on the mutation — the
Prism v4 footgun handled once, here. Reversible writes capture the container's
BEFORE state into ``priorState`` so the harness can record a faithful undo.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops._util import _seg, as_obj, ext_id, s

_CONTAINERS = "/api/clustermgmt/v4.0/config/storage-containers"


def _norm_container(raw: dict) -> dict:
    """Fold one raw storage-container record into the stable inventory shape."""
    return {
        "extId": ext_id(raw),
        "name": s(raw.get("name")),
        "clusterExtId": s(raw.get("clusterExtId") or (raw.get("cluster") or {}).get("extId")),
        "maxCapacityBytes": raw.get("maxCapacityBytes"),
        "logicalUsageBytes": raw.get("logicalUsageBytes"),
        "replicationFactor": raw.get("replicationFactor"),
    }


def list_storage_containers(conn: Any) -> list[dict]:
    """[READ] All storage containers, normalised (auto-paginated)."""
    return [_norm_container(r) for r in conn.list_all(_CONTAINERS)]


def _container_raw(conn: Any, ext_id_: str) -> tuple[dict, str]:
    """Fetch a container's raw record + ETag, raising KeyError if absent."""
    raw, etag = conn.get_with_etag(f"{_CONTAINERS}/{_seg(ext_id_)}")
    obj = as_obj(raw)
    if not obj:
        raise KeyError(f"Storage container '{ext_id_}' not found.")
    return obj, etag


def create_storage_container(
    conn: Any,
    name: str,
    cluster_ext_id: str,
    replication_factor: int = 2,
) -> dict:
    """[WRITE] Create a storage container on a cluster (reversible → delete)."""
    spec = {
        "name": name,
        "clusterExtId": cluster_ext_id,
        "replicationFactor": replication_factor,
    }
    resp = as_obj(conn.post(_CONTAINERS, json=spec))
    return {
        "action": "create_storage_container",
        "name": s(name),
        "clusterExtId": s(cluster_ext_id),
        "taskExtId": ext_id(resp),
    }


def update_storage_container(
    conn: Any,
    ext_id: str,
    max_capacity_bytes: int | None = None,
    replication_factor: int | None = None,
) -> dict:
    """[WRITE] Resize a container's capacity / replication (reversible → prior values)."""
    obj, etag = _container_raw(conn, ext_id)
    prior = {
        "maxCapacityBytes": obj.get("maxCapacityBytes"),
        "replicationFactor": obj.get("replicationFactor"),
    }
    body = dict(obj)
    if max_capacity_bytes is not None:
        body["maxCapacityBytes"] = max_capacity_bytes
    if replication_factor is not None:
        body["replicationFactor"] = replication_factor
    conn.put(f"{_CONTAINERS}/{_seg(ext_id)}", etag=etag, json=body)
    return {
        "action": "update_storage_container",
        "extId": s(ext_id),
        "name": s(obj.get("name")),
        "priorState": prior,
    }


def delete_storage_container(conn: Any, ext_id: str) -> dict:
    """[WRITE][high] Delete a storage container — captures prior state for the audit trail."""
    obj, etag = _container_raw(conn, ext_id)
    conn.delete(f"{_CONTAINERS}/{_seg(ext_id)}", etag=etag)
    return {
        "action": "delete_storage_container",
        "extId": s(ext_id),
        "name": s(obj.get("name")),
        "priorState": {
            "maxCapacityBytes": obj.get("maxCapacityBytes"),
            "replicationFactor": obj.get("replicationFactor"),
        },
    }
