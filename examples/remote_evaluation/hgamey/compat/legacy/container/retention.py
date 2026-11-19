"""Legacy evidence collection signature using the SDK collector and device policy."""

from llm4ad.gateway.examples.hgamey.evidence import HGameyEvidencePolicy
from llm4ad.gateway.sdk.retention import collect_finished as _collect


def collect_finished(engine, gateway, **kwargs):
    """Preserve extracted-runtime callers without duplicating acknowledgement logic."""
    return _collect(gateway, policy=HGameyEvidencePolicy(engine, gateway), **kwargs)
