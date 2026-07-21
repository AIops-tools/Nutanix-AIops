"""MCP server wrapping nutanix-aiops operations (stdio transport).

Thin adapter layer: each ``@mcp.tool()`` function (in ``mcp_server/tools/``)
delegates to the ``nutanix_aiops`` ops package and is wrapped with the
nutanix-aiops ``@governed_tool`` harness (audit / budget / undo / risk-tier).

Standalone, self-governed Nutanix Prism Central (v4) operations.
Cluster / VM / storage / network / snapshot-DR / alert / LCM management and
read-only diagnostics/RCA, with automatic ETag & pagination handling.

Source: https://github.com/AIops-tools/Nutanix-AIops
License: MIT
"""

import logging
import os

from mcp_server._shared import _safe_error, mcp, tool_errors

# Importing the tool modules registers every @mcp.tool() onto the shared
# `mcp` instance. Order does not matter; each module is self-contained.
from mcp_server.tools import (  # noqa: F401 — side effects
    alerts,
    capacity,
    catalog,
    clusters,
    dataprotection,
    diagnostics,
    lcm,
    network,
    storage,
    undo,
    vms,
)

__all__ = ["mcp", "main", "_safe_error", "tool_errors"]

logger = logging.getLogger(__name__)



def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    # Read-only mode was removed. Warn a deployment that still exports the old
    # switch so it gets one audible signal instead of silently gaining writes.
    if os.environ.get("NUTANIX_READ_ONLY"):
        logger.warning(
            "NUTANIX_READ_ONLY is set but no longer has any effect — "
            "read-only mode was removed. Writes ARE enabled. Restrict them via "
            "the connecting account's permissions instead (a read-only socket / "
            "scope-limited token)."
        )
    mcp.run(transport="stdio")
