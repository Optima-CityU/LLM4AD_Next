"""Compatibility import for the direct SDK gateway CLI."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("llm4ad.gateway.cli")
