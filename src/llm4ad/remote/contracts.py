"""Compatibility import for the gateway SDK's contracts module."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("llm4ad.gateway.sdk.contracts")
