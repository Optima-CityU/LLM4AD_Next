"""Legacy extracted-container launcher for the common SDK service."""

import asyncio
import os

from llm4ad.gateway.examples.hgamey.runtime import HGameyRuntime
from llm4ad.gateway.sdk.service import GatewayService, run_service


def Service(environment, executor_module=None, gateway_factory=None):
    """Preserve the legacy constructor without another service implementation."""
    runtime = HGameyRuntime(environment, "llm4ad.gateway.examples.hgamey.evaluator:HGameyShaderEvaluator", executor_module)
    return GatewayService(runtime, gateway_factory)


async def main():
    """Delegate the extracted-container startup to the SDK lifecycle."""
    return await run_service(Service(dict(os.environ)))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
