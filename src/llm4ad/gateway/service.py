"""Compatibility exports for the common SDK gateway lifecycle."""

from .sdk.service import GatewayService, create_service, run_service, serve

__all__ = ["GatewayService", "create_service", "run_service", "serve"]
