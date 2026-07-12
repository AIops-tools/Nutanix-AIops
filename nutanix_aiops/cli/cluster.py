"""``nutanix-aiops cluster`` — cluster / host / utilization reads."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from nutanix_aiops.cli._common import TargetOption, cli_errors, console, get_connection

cluster_app = typer.Typer(
    name="cluster",
    help="Clusters: list, health, hosts, utilization.",
    no_args_is_help=True,
)

ExtIdArg = Annotated[str, typer.Argument(help="Cluster extId (from 'cluster list')")]


@cluster_app.command("list")
@cli_errors
def cluster_list(target: TargetOption = None) -> None:
    """List registered clusters."""
    from nutanix_aiops.ops import clusters as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_clusters(conn)))


@cluster_app.command("health")
@cli_errors
def cluster_health(cluster_ext_id: ExtIdArg, target: TargetOption = None) -> None:
    """Show one cluster's health summary."""
    from nutanix_aiops.ops import clusters as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.get_cluster_health(conn, cluster_ext_id)))


@cluster_app.command("hosts")
@cli_errors
def cluster_hosts(target: TargetOption = None) -> None:
    """List hosts across all clusters."""
    from nutanix_aiops.ops import clusters as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_hosts(conn)))


@cluster_app.command("util")
@cli_errors
def cluster_util(cluster_ext_id: ExtIdArg, target: TargetOption = None) -> None:
    """Show a cluster's CPU/memory/storage/IOPS utilization."""
    from nutanix_aiops.ops import clusters as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.get_cluster_utilization(conn, cluster_ext_id)))
