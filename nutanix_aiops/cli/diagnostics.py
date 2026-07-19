"""``nutanix-aiops diagnose ...`` — read-only diagnostics / RCA over the estate."""

from __future__ import annotations

import typer
from rich.table import Table

from nutanix_aiops.cli._common import TargetOption, cli_errors, console, get_connection

diagnose_app = typer.Typer(
    name="diagnose",
    help="Read-only diagnostics / RCA over the Prism Central estate.",
    no_args_is_help=True,
)

_SEVERITY_STYLE = {"critical": "red", "warning": "yellow", "info": "cyan"}


def _print_findings(findings: list[dict]) -> None:
    """Render worst-first findings as a table, or a green all-clear line."""
    if not findings:
        console.print("[green]No findings — every measured value is under threshold.[/]")
        return
    table = Table(title="Findings (worst first)")
    for col in ("severity", "resource", "signal", "detail", "action"):
        table.add_column(col, overflow="fold")
    for f in findings:
        style = _SEVERITY_STYLE.get(f["severity"], "white")
        table.add_row(
            f"[{style}]{f['severity']}[/]", f.get("resource", ""),
            f["signal"], f["detail"], f["action"],
        )
    console.print(table)


@diagnose_app.command("cluster-health")
@cli_errors
def diagnose_cluster_health(target: TargetOption = None) -> None:
    """Estate health: resiliency state, storage headroom, nodes down (worst first)."""
    from nutanix_aiops.ops import clusters as cl
    from nutanix_aiops.ops import diagnostics as diag
    from nutanix_aiops.ops import storage as st

    conn, _ = get_connection(target)
    result = diag.cluster_health_findings(
        cl.list_cluster_reports(conn),
        cl.list_hosts(conn)["hosts"],
        st.list_storage_containers(conn)["containers"],
    )
    console.print(
        f"[bold]Analyzed {result['clustersAnalyzed']} cluster(s), "
        f"{result['hostsAnalyzed']} host(s), "
        f"{result['containersAnalyzed']} storage container(s).[/]"
    )
    _print_findings(result["findings"])


@diagnose_app.command("alert-triage")
@cli_errors
def diagnose_alert_triage(target: TargetOption = None) -> None:
    """Triage active alerts: per-severity counts and the oldest unresolved one."""
    from nutanix_aiops.ops import alerts as al
    from nutanix_aiops.ops import diagnostics as diag

    conn, _ = get_connection(target)
    result = diag.alert_triage_findings(al.list_alerts(conn)["alerts"])
    console.print(
        f"[bold]Analyzed {result['alertsAnalyzed']} alert(s); "
        f"{result['activeAlerts']} still active.[/]"
    )
    _print_findings(result["findings"])
    oldest = result["oldestUnresolved"]
    if oldest:
        console.print(
            f"[dim]Oldest unresolved: {oldest['title']} "
            f"({oldest['severity']}, {oldest['ageDays']} day(s) old)[/]"
        )
