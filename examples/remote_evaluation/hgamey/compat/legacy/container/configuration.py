"""Compatibility entry; maintained implementation ships in the LLM4AD SDK."""
import sys
from importlib import import_module
_impl = import_module('llm4ad.gateway.examples.hgamey.configuration')

def __getattr__(name):
    return getattr(_impl, name)

sys.modules[__name__] = _impl
