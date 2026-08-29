"""Evaluator layer for LLM4AD.

Responsible for measuring algorithm quality through evaluation tasks and metrics.
"""

from llm4ad.evaluator.base import (
    BaseBatchEvaluator,
    BaseEvaluator,
    BenchmarkEvaluator,
    EvaluationResult,
    ExecutableEvaluator,
    Metric,
    MetricType,
    PythonEvaluator,
)
from llm4ad.evaluator.behavior import BehaviorData, BehaviorVisualization
from llm4ad.evaluator.dispatcher import EvaluationDispatcher
from llm4ad.evaluator.llm_judge import LLMJudgeEvaluator
from llm4ad.evaluator.paper_revision import PaperRevisionEvaluator
from llm4ad.evaluator.task import Task, TaskConfig, TaskType

__all__ = [
    # Base types
    "BaseEvaluator",
    "BaseBatchEvaluator",
    "EvaluationResult",
    "EvaluationDispatcher",
    # Behavior data
    "BehaviorData",
    "BehaviorVisualization",
    # Concrete base classes
    "PythonEvaluator",
    "ExecutableEvaluator",
    "BenchmarkEvaluator",
    "LLMJudgeEvaluator",
    "PaperRevisionEvaluator",
    # Task management
    "Task",
    "TaskConfig",
    "TaskType",
    # Metric types
    "Metric",
    "MetricType",
]
