"""Subnet / network inventory (read + guarded writes).

Reads Prism Central's subnet inventory via the networking v4 API and folds it
into stable shapes so downstream analysis never has to special-case raw payload
field names. Every mutating call auto-fetches the subnet's current ETag (via
``conn.get_with_etag``) and sends it back as ``If-Match`` on the mutation — the
Prism v4 footgun handled once, here. The destructive delete captures the
subnet's BEFORE name into ``priorState`` for a faithful audit trail. All server
text passes through ``sanitize`` at the ``_util`` layer.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops._util import _seg, as_obj, ext_id, s

_SUBNETS = "/api/networking/v4.0/config/subnets"


def _norm_subnet(raw: dict) -> dict:
    """Fold one raw subnet record into the stable inventory shape."""
    cluster = raw.get("clusterReference") or raw.get("cluster") or {}
    ipcfg_list = raw.get("ipConfig") or []
    ip_config: dict[str, Any] = {}
    first = ipcfg_list[0] if isinstance(ipcfg_list, list) and ipcfg_list else ipcfg_list
    if isinstance(first, dict):
        ipv4 = first.get("ipv4") or first.get("ipv6") or {}
        ip = ipv4.get("ipSubnet") or {}
        prefix = ip.get("ip") or {}
        cidr = prefix.get("value")
        prefix_len = ip.get("prefixLength")
        if cidr and prefix_len is not None:
            ip_config["cidr"] = s(f"{cidr}/{prefix_len}")
        gateway = (ipv4.get("defaultGatewayIp") or {}).get("value")
        if gateway:
            ip_config["gateway"] = s(gateway)
    return {
        "extId": ext_id(raw),
        "name": s(raw.get("name")),
        "subnetType": s(raw.get("subnetType")),
        "vlanId": raw.get("networkId") if raw.get("networkId") is not None
        else raw.get("vlanId"),
        "clusterExtId": s(cluster.get("extId") or cluster.get("uuid")
                          or raw.get("clusterExtId")),
        "ipConfig": ip_config,
    }


def list_subnets(conn: Any) -> list[dict]:
    """[READ] All subnets, normalised (auto-paginated)."""
    return [_norm_subnet(r) for r in conn.list_all(_SUBNETS)]


def get_subnet(conn: Any, ext_id: str) -> dict:
    """[READ] One subnet by extId, normalised, with its current ETag surfaced.

    The ``_etag`` is what any downstream mutation needs for If-Match; exposing it
    on the read lets an agent chain get→delete without a second round trip.
    """
    raw, etag = conn.get_with_etag(f"{_SUBNETS}/{_seg(ext_id)}")
    obj = as_obj(raw)
    if not obj:
        raise KeyError(f"Subnet '{ext_id}' not found.")
    result = _norm_subnet(obj)
    result["_etag"] = s(etag)
    return result


def _subnet_raw(conn: Any, ext_id: str) -> tuple[dict, str]:
    """Fetch a subnet's raw record + ETag, raising KeyError if absent."""
    raw, etag = conn.get_with_etag(f"{_SUBNETS}/{_seg(ext_id)}")
    obj = as_obj(raw)
    if not obj:
        raise KeyError(f"Subnet '{ext_id}' not found.")
    return obj, etag


def create_subnet(
    conn: Any,
    name: str,
    cluster_ext_id: str,
    vlan_id: int,
    subnet_type: str = "VLAN",
) -> dict:
    """[WRITE] Create a subnet on a cluster."""
    spec = {
        "name": name,
        "subnetType": subnet_type,
        "networkId": vlan_id,
        "clusterReference": {"extId": cluster_ext_id},
    }
    resp = as_obj(conn.post(_SUBNETS, json=spec))
    return {"action": "create_subnet", "name": s(name),
            "clusterExtId": s(cluster_ext_id), "taskExtId": ext_id(resp)}


def delete_subnet(conn: Any, ext_id: str) -> dict:
    """[WRITE][high] Delete a subnet — captures the prior name for the audit trail."""
    obj, etag = _subnet_raw(conn, ext_id)
    conn.delete(f"{_SUBNETS}/{_seg(ext_id)}", etag=etag)
    return {"action": "delete_subnet", "extId": s(ext_id),
            "name": s(obj.get("name")), "priorState": {"name": s(obj.get("name"))}}
