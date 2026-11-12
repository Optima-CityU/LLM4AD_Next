"""Optional browser dashboard for the packaged evaluation gateway.

Importing this package does not import NiceGUI or start a gateway service.
"""

from .server import run_dashboard

__all__ = ["run_dashboard"]
