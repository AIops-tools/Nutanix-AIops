"""Subnet / network ops + MCP tests for nutanix-aiops.

Proves: list normalises raw subnet payloads, subnet_get surfaces the ETag via
get_with_etag, the write tools carry correct risk tiers, subnet_delete's
dry-run gate never mutates, and subnet_delete records a prior state complete
enough to rebuild the subnet by hand. No real Prism Central is needed — the
connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_list_subnets_normalizes():
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {
            "extId": "sn1",
            "name": "vlan100",
            "subnetType": "VLAN",
            "networkId": 100,
            "clusterReference": {"extId": "cl1"},
            "ipConfig": [
                {
                    "ipv4": {
                        "ipSubnet": {"ip": {"value": "10.0.0.0"}, "prefixLength": 24},
                        "defaultGatewayIp": {"value": "10.0.0.1"},
                    }
                }
            ],
        }
    ]
    rows = ops.list_subnets(conn)["subnets"]
    assert rows == [
        {
            "extId": "sn1",
            "name": "vlan100",
            "subnetType": "VLAN",
            "vlanId": 100,
            "clusterExtId": "cl1",
            "ipConfig": {"cidr": "10.0.0.0/24", "gateway": "10.0.0.1"},
        }
    ]
    assert conn.list_all.call_args[0][0] == "/api/networking/v4.0/config/subnets"


@pytest.mark.unit
def test_get_subnet_returns_etag_via_get_with_etag():
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "sn1", "name": "vlan100", "subnetType": "VLAN"}},
        "etag-77",
    )
    result = ops.get_subnet(conn, "sn1")
    assert result["extId"] == "sn1"
    assert result["_etag"] == "etag-77"
    conn.get_with_etag.assert_called_once_with(
        "/api/networking/v4.0/config/subnets/sn1"
    )


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import network as n

    assert n.subnet_create._risk_level == "medium"
    assert n.subnet_delete._risk_level == "high"


@pytest.mark.unit
def test_mcp_subnet_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import network as n

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "sn1", "name": "vlan100", "subnetType": "VLAN"}},
        "etag-9",
    )
    monkeypatch.setattr(n, "_get_connection", lambda target=None: conn)

    result = n.subnet_delete(ext_id="sn1", dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldDelete"]["name"] == "vlan100"
    conn.delete.assert_not_called()


# ── delete prior state: the delete has no inverse ──────────────────────────
#
# priorState is the ONLY surviving record of a deleted subnet. It once held the
# name alone, which left the VLAN id, cluster and addressing unrecoverable —
# the change could not be reconstructed even by hand from the audit row.

_FULL_SUBNET = {
    "extId": "sn1",
    "name": "vlan100",
    "description": "lab network",
    "subnetType": "VLAN",
    "networkId": 100,
    "clusterReference": {"extId": "cl1"},
    "ipConfig": [
        {
            "ipv4": {
                "ipSubnet": {"ip": {"value": "10.0.0.0"}, "prefixLength": 24},
                "defaultGatewayIp": {"value": "10.0.0.1"},
                "poolList": [
                    {"startIp": {"value": "10.0.0.50"}, "endIp": {"value": "10.0.0.99"}},
                    {"startIp": {"value": "10.0.0.150"}, "endIp": {"value": "10.0.0.199"}},
                ],
            }
        }
    ],
}


@pytest.mark.unit
def test_delete_subnet_captures_full_prior_state():
    """(d) Everything needed to recreate the subnet, not just its name."""
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": _FULL_SUBNET}, "etag-1")
    prior = ops.delete_subnet(conn, "sn1")["priorState"]

    assert prior["name"] == "vlan100"
    assert prior["description"] == "lab network"
    assert prior["subnetType"] == "VLAN"
    assert prior["vlanId"] == 100
    assert prior["clusterExtId"] == "cl1"
    assert prior["ipConfig"] == {"cidr": "10.0.0.0/24", "gateway": "10.0.0.1"}
    assert prior["ipPools"] == [
        {"startIp": "10.0.0.50", "endIp": "10.0.0.99"},
        {"startIp": "10.0.0.150", "endIp": "10.0.0.199"},
    ]
    conn.delete.assert_called_once_with(
        "/api/networking/v4.0/config/subnets/sn1", etag="etag-1"
    )


@pytest.mark.unit
def test_delete_subnet_prior_state_survives_a_sparse_response():
    """(e) Fail open: a sparse subnet record yields nulls, never a crash."""
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "sn2"}}, "etag-2")
    prior = ops.delete_subnet(conn, "sn2")["priorState"]

    # Keys are always present — an absent field must read as null, not as gone.
    assert set(prior) == {"name", "description", "subnetType", "vlanId",
                          "clusterExtId", "ipConfig", "ipPools"}
    assert prior["name"] is None
    assert prior["description"] is None
    assert prior["vlanId"] is None
    assert prior["clusterExtId"] is None
    assert prior["ipConfig"] == {}
    assert prior["ipPools"] == []
    conn.delete.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize(
    "ipconfig",
    [
        "not-a-list",
        [None, "junk"],
        [{"ipv4": {"poolList": "not-a-list"}}],
        [{"ipv4": {"poolList": [None, {}]}}],
        [{"ipv4": None}],
    ],
)
def test_ip_pool_capture_tolerates_malformed_ip_config(ipconfig):
    """A shape the API was not documented to return must not break the delete."""
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "sn3", "name": "odd", "ipConfig": ipconfig}}, "etag-3"
    )
    assert ops.delete_subnet(conn, "sn3")["priorState"]["ipPools"] == []


@pytest.mark.unit
def test_delete_subnet_captures_ipv6_pools():
    """IPv6-only subnets record their pools too — the fallback is not IPv4-only."""
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "sn4", "name": "v6",
                  "ipConfig": [{"ipv6": {"poolList": [
                      {"startIp": {"value": "2001:db8::10"},
                       "endIp": {"value": "2001:db8::20"}}]}}]}},
        "etag-4",
    )
    prior = ops.delete_subnet(conn, "sn4")["priorState"]
    assert prior["ipPools"] == [{"startIp": "2001:db8::10", "endIp": "2001:db8::20"}]
