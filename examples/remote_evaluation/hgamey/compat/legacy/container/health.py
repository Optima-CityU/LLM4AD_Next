"""Legacy container probe delegating to the common SDK health contract."""

import json
import os
from pathlib import Path

from llm4ad.gateway.sdk.health import probe


def main():
    """Read the endpoint registered by the shared service."""
    value = probe(Path(os.environ.get("HGAMEY_RUNTIME_DIR", "/data/gateway")))
    print(json.dumps(value))
    return 0 if value.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
