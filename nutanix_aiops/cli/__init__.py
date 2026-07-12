"""CLI package for nutanix-aiops.

Re-exports ``app`` so the pyproject entry point
``nutanix-aiops = "nutanix_aiops.cli:app"`` works unchanged.
"""

from nutanix_aiops.cli._root import app

__all__ = ["app"]
