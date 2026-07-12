"""MCP server wrapping nutanix-aiops operations (stdio transport).

Thin adapter layer: each ``@mcp.tool()`` function (in ``mcp_server/tools/``)
delegates to the ``nutanix_aiops`` ops package and is wrapped with the
nutanix-aiops ``@governed_tool`` harness (audit / budget / undo / risk-tier).

Standalone, self-governed Nutanix Prism Central (v4) operations (preview).
Cluster / VM / storage / network / snapshot-DR / alert / LCM management with
automatic ETag & pagination handling.

Source: https://github.com/AIops-tools/Nutanix-AIops
License: MIT
"""

import logging

from mcp_server._shared import _safe_error, mcp, tool_errors

# Importing the tool modules registers every @mcp.tool() onto the shared
# `mcp` instance. Order does not matter; each module is self-contained.
from mcp_server.tools import (  # noqa: F401 — side effects
    alerts,
    capacity,
    catalog,
    clusters,
    dataprotection,
    lcm,
    network,
    storage,
    vms,
)

__all__ = ["mcp", "main", "_safe_error", "tool_errors"]


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")
