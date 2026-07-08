"""LLM4AD: LLM for Algorithm Design.

A platform that uses Large Language Models with evolutionary algorithms
to automatically design and improve algorithms.
"""

__version__ = "1.12.0"

from llm4ad.llm4ad import LLM4AD
from llm4ad.utils.registry import Registrable, Registry

__all__ = [
    "__version__",
    "Registry",
    "Registrable",
    "LLM4AD",
]
