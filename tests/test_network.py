"""Subnet / network ops + MCP tests for nutanix-aiops.

Proves: list normalises raw subnet payloads, subnet_get surfaces the ETag via
get_with_etag, the write tools carry correct risk tiers, and subnet_delete's
dry-run gate never mutates. No real Prism Central is needed — the connection is
a MagicMock.
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
    rows = ops.list_subnets(conn)
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
    conn.list_all.assert_called_once_with("/api/networking/v4.0/config/subnets")


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
