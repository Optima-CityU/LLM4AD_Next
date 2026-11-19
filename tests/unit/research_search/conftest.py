"""Make standalone backend search modules available without booting the API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src/backend"))
