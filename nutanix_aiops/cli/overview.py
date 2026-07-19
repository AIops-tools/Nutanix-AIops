"""``nutanix-aiops overview`` — one-shot estate health."""

from __future__ import annotations

import json

from nutanix_aiops.cli._common import TargetOption, cli_errors, console, get_connection


@cli_errors
def overview_cmd(target: TargetOption = None) -> None:
    """One-shot estate summary: cluster/host/VM counts + hypervisor & power spread."""
    from nutanix_aiops.ops import overview as ops

    conn, _ = get_connection(target)
    result = ops.fleet_overview(conn)
    console.print_json(json.dumps(result))
    if result.get("truncated"):
        console.print(
            f"[yellow]… {', '.join(result['truncated'])} inventory truncated — "
            f"the counts above are a lower bound; re-run the per-resource list "
            f"command with a higher --limit.[/]"
        )
