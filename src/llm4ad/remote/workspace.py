"""Compatibility import for optional gateway scaffolding."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("llm4ad.gateway.scaffold")
