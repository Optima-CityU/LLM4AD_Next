"""Paper revision evaluator package."""

from llm4ad.evaluator.paper_revision.evaluator import PaperRevisionEvaluator
from llm4ad.evaluator.paper_revision.schemas import (
    CandidateRevision,
    CSPaperFinding,
    DebateBallot,
    JudgeReport,
    PaperRevisionTask,
)

__all__ = [
    "CandidateRevision",
    "CSPaperFinding",
    "DebateBallot",
    "JudgeReport",
    "PaperRevisionEvaluator",
    "PaperRevisionTask",
]
