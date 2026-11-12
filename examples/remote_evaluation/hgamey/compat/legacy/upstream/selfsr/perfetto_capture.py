"""Compatibility import of the packaged HGamey protocol."""
import sys
from importlib import import_module
_impl = import_module("llm4ad.gateway.examples.hgamey.protocol.upstream.selfsr.perfetto_capture")
globals().update({name: value for name, value in vars(_impl).items() if name not in {"__name__", "__file__", "__spec__"}})
sys.modules[__name__] = _impl

if __name__ == "__main__":
    import runpy
    runpy.run_module("llm4ad.gateway.examples.hgamey.protocol.upstream.selfsr.perfetto_capture", run_name="__main__")
