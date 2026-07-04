"""Base planner interface for LLM4AD."""

import base64 as _base64
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from llm4ad.coder.base import BaseCoder
from llm4ad.config.schema import PlannerConfig
from llm4ad.evaluator.behavior import BehaviorData, BehaviorVisualization
from llm4ad.infra.provider import BaseProvider
from llm4ad.infra.repo_analyzer import AnalyzedRepository
from llm4ad.infra.state import StateTracker
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.infra.version_control import BaseVersionControl, WorktreeInfo
from llm4ad.utils.diff_utils import (
    apply_unified_diff,
    hash_content,
    parse_diff_stats,
)
from llm4ad.utils.registry import Registrable


class InsightType(Enum):
    """Type of algorithm insight."""

    INITIAL = "initial"  # Initial random/seed insight
    MUTATION = "mutation"  # Mutated from parent
    CROSSOVER = "crossover"  # Combined from parents
    REFLECTION = "reflection"  # Derived from error analysis


class CodeArtifact(BaseModel):
    """Represents a code file artifact for an algorithm.

    Supports multi-file algorithms and tracks file metadata.
    In diff mode, content stores the unified diff relative to a base version;
    in full mode, content stores the complete code.
    """

    file_path: str  # Relative path to the file (e.g. "sort/quick_sort.py")
    content: str  # Code content (full) or diff content (diff mode)
    language: str = "python"  # Programming language
    is_entrypoint: bool = False  # Whether this is the entrypoint file
    dependencies: list[str] = []  # Dependencies specific to this file

    # Diff mode support
    content_mode: Literal["full", "diff"] = "diff"  # Storage mode
    base_commit_hash: str | None = None  # Git commit hash if applying to existing repo
    base_file_hash: str | None = None  # Hash of base file content this diff applies to

    model_config = ConfigDict(arbitrary_types_allowed=True)


class GenerationMetadata(BaseModel):
    """Metadata about how an algorithm insight was generated.

    Tracks complete provenance information for reproducibility.
    """

    operator: str  # Evolution operator used: initial/sample/mutate/crossover/reflection
    llm_provider: str  # LLM provider used (e.g. "openai", "anthropic")
    llm_model: str  # LLM model name (e.g. "gpt-4o", "claude-3-5-sonnet")
    memory_entries: list[str] = []  # IDs of memory entries used in generation
    prompt_template: str | None = None  # Prompt template used
    temperature: float = 0.7  # Sampling temperature used
    generation_time_ms: float = 0.0  # Time taken to generate this insight
    tokens_used: int = 0  # Total tokens used in generation

    # Complete provenance tracking
    agent_id: str = ""  # Unique ID of the agent/sampler that generated this
    agent_name: str = ""  # Name of the planner/sampler implementation (e.g., "llm-mutate")
    agent_version: str = "1.0"  # Version of the agent implementation
    operation_params: dict[str, Any] = {}  # Specific parameters used for this evolution operation
    change_description: str = ""  # Natural language description of what was changed
    targeted_files: list[str] = []  # List of files that were targeted for modification
    seed: int = 0  # Random seed used for generation (reproducibility)
    code_generator_version: str = ""  # Version of the coder component used

    model_config = ConfigDict(arbitrary_types_allowed=True)


class EvaluationResult(BaseModel):
    """Result of evaluating an algorithm insight.

    Stores all evaluation related information.
    """

    score: float  # Overall fitness score (higher = better)
    metrics: dict[str, float] = {}  # Detailed performance metrics (latency, accuracy, etc.)
    error: str | None = None  # Error message if evaluation failed
    evaluation_time_ms: float = 0.0  # Time taken to evaluate
    evaluator_version: str = "1.0"  # Version of evaluator used
    hardware_info: dict[str, Any] = {}  # Hardware info where evaluation ran
    timing: ExecutionTiming = Field(default_factory=ExecutionTiming)  # Execution timing breakdown

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Algorithm(BaseModel):
    """Algorithm - the core individual unit in evolution.

    Represents a single algorithm design in the evolution process.
    Supports multi-file algorithms, tracks complete generation provenance,
    evaluation results, and all metadata needed for evolution.

    In diff mode (default), each Algorithm only stores the changes relative to its parents.
    Full code can be reconstructed by traversing the lineage chain.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    insight_type: InsightType
    domain: str = "generic"  # Algorithm domain (sorting, optimization, search, etc.)
    name: str = ""  # Human-readable name for this algorithm

    # Algorithm design
    description: str  # Natural language description of the algorithm
    key_innovations: list[str] = []  # List of key innovations/features
    design_notes: str = ""  # Additional design notes/rationale

    # Code artifacts (supports multi-file algorithms)
    code_artifacts: list[CodeArtifact] = []  # All code files for this algorithm

    # Worktree
    worktree: WorktreeInfo | None = None  # Version control worktree info for this algorithm

    # Evolution lineage
    parent_ids: list[str] = []  # IDs of parent individuals (supports N-parent crossover)
    generation: int = 0  # Generation when this individual was created
    island_id: int | None = None  # ID of island this individual belongs to (for island GA)

    # Generation provenance
    generation_meta: GenerationMetadata | None = None

    # Evaluation results
    evaluation: EvaluationResult | None = None

    # Diff-based workflow metadata
    changed_files: list[str] = []  # List of files modified from parents
    lines_added: int = 0  # Count of lines added
    lines_removed: int = 0  # Count of lines removed
    lines_modified: int = 0  # Count of lines modified

    # Population/run tracking
    population_id: str = ""  # ID of the population this belongs to

    # Additional metadata
    tags: list[str] = []  # Custom tags for filtering/organization
    custom_metadata: dict[str, Any] = {}  # Custom user-defined metadata

    # Timing
    timing: ExecutionTiming = Field(default_factory=ExecutionTiming)

    # Behavior data from evaluation (multimodal support)
    behavior_data: list[BehaviorData] = []  # Behavior data from evaluation instances

    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def score(self) -> float:
        """Get the overall fitness score (shortcut for evaluation.score)."""
        return self.evaluation.score if self.evaluation else 0.0

    @property
    def metrics(self) -> dict[str, float]:
        """Get detailed evaluation metrics."""
        return self.evaluation.metrics if self.evaluation else {}

    def is_evaluated(self) -> bool:
        """Check if this insight has been successfully evaluated."""
        return self.evaluation is not None and self.evaluation.error is None

    def has_behavior(self) -> bool:
        """Check if this algorithm has behavior data with images."""
        return any(bd.has_images() for bd in self.behavior_data)

    def get_best_visualization(self) -> BehaviorVisualization | None:
        """Get the first available renderable visualization.

        Returns:
            First BehaviorVisualization that can produce an image, or None.
        """
        for bd in self.behavior_data:
            for viz in bd.visualizations:
                if viz.is_renderable():
                    return viz
        return None

    def get_observation_text(self) -> str:
        """Get concatenated observation texts from all behavior data.

        Returns:
            Combined observation text from all behavior data entries.
        """
        observations = [bd.observation for bd in self.behavior_data if bd.observation]
        return "\n".join(observations)

    def has_code(self) -> bool:
        """Check if this insight has generated code."""
        return len(self.code_artifacts) > 0

    def get_code(self, file_path: str | None = None) -> str | None:
        """Get stored content (diff or full) for a specific file or the main entrypoint.

        For diff mode, use resolve_full_content() to reconstruct complete code from parents.

        Args:
            file_path: Optional path to specific file, if None returns main entrypoint

        Returns:
            Stored content (diff or full) or None if not found
        """
        if not self.code_artifacts:
            return None

        if file_path:
            for artifact in self.code_artifacts:
                if artifact.file_path == file_path:
                    return artifact.content
        return None

    def add_code_artifact(
        self, file_path: str, content: str, **kwargs
    ) -> None:
        """Add a code artifact to this insight.

        Args:
            file_path: Relative path to the file
            content: Code content (full) or diff content (diff mode)
            is_entrypoint: Whether this is the main entrypoint
            **kwargs: Additional CodeArtifact parameters
        """
        artifact = CodeArtifact(
            file_path=file_path, content=content, **kwargs
        )
        self.code_artifacts.append(artifact)

        self.updated_at = time.time()

    def set_evaluation_result(self, score: float, **kwargs) -> None:
        """Set the evaluation result for this insight.

        Args:
            score: Overall fitness score
            **kwargs: Additional EvaluationResult parameters
        """
        self.evaluation = EvaluationResult(score=score, **kwargs)
        self.updated_at = time.time()

    def update_timing(self, timing: ExecutionTiming) -> None:
        """Merge a timing payload into this algorithm's accumulated timing.

        Args:
            timing: Timing breakdown to add.
        """
        self.timing.add(timing)
        self.timing.recompute_overhead()
        self.updated_at = time.time()

    def calculate_diff_stats(self) -> tuple[int, int, int]:
        """Calculate line change statistics from current diff artifacts.

        Returns:
            Tuple of (lines_added, lines_removed, lines_modified)
        """
        total_added = 0
        total_removed = 0
        total_modified = 0

        for artifact in self.code_artifacts:
            if artifact.content_mode == "diff" and artifact.content:
                added, removed, modified = parse_diff_stats(artifact.content)
                total_added += added
                total_removed += removed
                total_modified += modified

        self.lines_added = total_added
        self.lines_removed = total_removed
        self.lines_modified = total_modified
        self.updated_at = time.time()

        return (total_added, total_removed, total_modified)

    def resolve_full_content(
        self, parent_map: dict[str, "Algorithm"], base_content: dict[str, str] | None = None
    ) -> list[CodeArtifact]:
        """Reconstruct full code content by resolving diffs through the lineage.

        For initial generation with full content, returns code_artifacts directly.
        For diff-only insights, traverses parents and applies diffs to get full code.

        Args:
            parent_map: Dictionary mapping parent IDs to Algorithm objects
            base_content: Optional base content from external repository for initial diffs

        Returns:
            List of CodeArtifact with full content (content_mode = "full")

        Raises:
            ValueError: If parent not found or diff cannot be applied
        """
        # Handle base case: initial generation
        if len(self.parent_ids) == 0 or self.insight_type == InsightType.INITIAL:
            result = []
            for artifact in self.code_artifacts:
                if artifact.content_mode == "full":
                    result.append(artifact.model_copy())
                else:
                    # For initial diff, get base content from external
                    if base_content and artifact.file_path in base_content:
                        base = base_content[artifact.file_path]
                        full_content, success = apply_unified_diff(base, artifact.content)
                        if not success:
                            raise ValueError(f"Failed to apply diff to {artifact.file_path}")
                        result.append(
                            artifact.model_copy(
                                update={
                                    "content": full_content,
                                    "content_mode": "full",
                                    "base_file_hash": hash_content(base),
                                }
                            )
                        )
                    else:
                        raise ValueError(
                            f"Initial diff requires base_content for {artifact.file_path}"
                        )
            return result

        # Get the first parent (for crossover, multiple parents may contribute diffs)
        # For simplicity, we use the first parent as the base and apply our diff on top
        parent_id = self.parent_ids[0]
        if parent_id not in parent_map:
            raise ValueError(f"Parent {parent_id} not found in parent_map")

        parent = parent_map[parent_id]
        parent_full_artifacts = parent.resolve_full_content(parent_map, base_content)

        # Create a map for quick lookup
        parent_full_map = {a.file_path: a for a in parent_full_artifacts}

        result_artifacts: list[CodeArtifact] = []

        for artifact in self.code_artifacts:
            if artifact.content_mode == "full":
                # Already full content, just copy
                result_artifacts.append(artifact.model_copy())
            else:
                # Get base content from parent
                if artifact.file_path in parent_full_map:
                    base_content_full = parent_full_map[artifact.file_path].content
                elif base_content and artifact.file_path in base_content:
                    base_content_full = base_content[artifact.file_path]
                else:
                    raise ValueError(
                        f"Base content for {artifact.file_path} not found in parent or external"
                    )

                # Apply diff
                full_content, success = apply_unified_diff(base_content_full, artifact.content)
                if not success:
                    raise ValueError(f"Failed to apply diff to {artifact.file_path}")

                new_artifact = artifact.model_copy(
                    update={
                        "content": full_content,
                        "content_mode": "full",
                        "base_file_hash": hash_content(base_content_full),
                    }
                )
                result_artifacts.append(new_artifact)

        # Add any files from parent that are not changed in this insight
        for parent_artifact in parent_full_artifacts:
            if not any(a.file_path == parent_artifact.file_path for a in self.code_artifacts):
                result_artifacts.append(parent_artifact.model_copy())

        return result_artifacts

    def get_lineage(self) -> list[str]:
        """Get the lineage of this insight (parent chain).

        Returns:
            List of parent IDs from oldest to newest
        """
        # This would need access to the full population
        # For now, just return immediate parents
        lineage = []
        lineage.extend(self.parent_ids)
        return lineage

    def to_prompt_context(self, include_behavior: bool = False) -> str | list[dict]:
        """Convert to a prompt-friendly representation for LLM.

        Args:
            include_behavior: If True and behavior data with images is
                available, return multimodal content parts (list of dicts)
                instead of plain text.

        Returns:
            Plain text string when include_behavior is False or no images
            are available. List of content parts (OpenAI multimodal format)
            when include_behavior is True and images exist.
        """
        lines = [
            f"Algorithm: {self.description}",
        ]
        if self.score is not None:
            lines.append(f"Score: {self.score:.4f}")
        if self.metrics:
            lines.append("Metrics:")
            for name, value in self.metrics.items():
                lines.append(f"  - {name}: {value}")

        text = "\n".join(lines)

        if not include_behavior or not self.has_behavior():
            return text

        # Build multimodal content parts
        parts: list[dict] = [{"type": "text", "text": text}]

        # Add observation text
        obs = self.get_observation_text()
        if obs:
            parts.append({"type": "text", "text": f"Behavior observation: {obs}"})

        # Add behavior images
        from llm4ad.evaluator.renderer import render_visualization

        for bd in self.behavior_data:
            for viz in bd.visualizations:
                rendered = render_visualization(viz)
                if rendered is not None:
                    data_url = f"data:{viz.media_type};base64,{rendered}"
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    })

        return parts

    def write(
        self,
        output_dir: "Path",
        stage: Literal["insight", "code", "evaluation"],
        island_id: int | None = None,
        generation: int | None = None,
    ) -> None:
        """Write algorithm content to files in the specified directory.

        Exports the algorithm in both markdown and JSON formats.
        The output filename includes island_id, generation, and algorithm_id.

        Behavior images are saved as separate PNG files alongside the JSON
        to avoid bloating the JSON with large base64 strings. The JSON stores
        a ``_saved_image_file`` reference in ``raw_data`` for each extracted
        image, which ``Algorithm.load()`` uses to rehydrate the images.

        Args:
            output_dir: Directory to write the output files (typically the generated_dir)
            stage: Generation stage - "insight" (after plan), "code" (after implement), "evaluation" (after evaluation)
            island_id: ID of the island that generated this algorithm
            generation: Generation number when this algorithm was created
        """
        import json

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create filename with island_id, generation, and algorithm_id
        parts = []
        if island_id is not None:
            parts.append(f"island_{island_id}")
        if generation is not None:
            parts.append(f"gen_{generation}")
        parts.append(self.id)
        filename_prefix = "_".join(parts)

        # Extract behavior images to separate files before JSON serialization
        saved_images: list[tuple[int, int, str, str]] = []
        saved_raws: list[tuple[int, int, dict, str]] = []
        for bd_idx, bd in enumerate(self.behavior_data):
            for viz_idx, viz in enumerate(bd.visualizations):
                if viz.data_base64:
                    img_filename = f"{filename_prefix}_viz_{bd_idx}_{viz_idx}.png"
                    img_path = output_dir / img_filename
                    img_bytes = _base64.b64decode(viz.data_base64)
                    with open(img_path, "wb") as img_f:
                        img_f.write(img_bytes)
                    saved_images.append(
                        (bd_idx, viz_idx, viz.data_base64, img_filename)
                    )
                    # Replace base64 with filename reference for compact JSON
                    viz.data_base64 = None
                    if viz.raw_data is None:
                        viz.raw_data = {}
                    viz.raw_data["_saved_image_file"] = img_filename
                elif viz.raw_data and "_saved_image_file" not in viz.raw_data:
                    # Save large raw_data to companion .json file
                    raw_filename = f"{filename_prefix}_raw_{bd_idx}_{viz_idx}.json"
                    raw_path = output_dir / raw_filename
                    with open(raw_path, "w", encoding="utf-8") as raw_f:
                        json.dump(viz.raw_data, raw_f)
                    saved_raws.append(
                        (bd_idx, viz_idx, viz.raw_data, raw_filename)
                    )
                    viz.raw_data = {"_saved_raw_data_file": raw_filename}

        # Write JSON format (now without large base64 strings)
        json_path = output_dir / f"{filename_prefix}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

        # Restore in-memory base64 data so the object remains usable
        for bd_idx, viz_idx, original_b64, _filename in saved_images:
            viz = self.behavior_data[bd_idx].visualizations[viz_idx]
            viz.data_base64 = original_b64
            if viz.raw_data and "_saved_image_file" in viz.raw_data:
                del viz.raw_data["_saved_image_file"]
                if not viz.raw_data:
                    viz.raw_data = None
        for bd_idx, viz_idx, original_raw, _filename in saved_raws:
            self.behavior_data[bd_idx].visualizations[viz_idx].raw_data = original_raw

        # Write Markdown format
        md_path = output_dir / f"{filename_prefix}.md"
        md_content = self._to_markdown(stage, island_id, generation, filename_prefix)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

    def _to_markdown(
        self,
        stage: Literal["insight", "code", "evaluation"],
        island_id: int | None = None,
        generation: int | None = None,
        filename_prefix: str = "",
    ) -> str:
        """Convert algorithm to markdown format.

        Args:
            stage: Generation stage
            island_id: ID of the island that generated this algorithm
            generation: Generation number when this algorithm was created
            filename_prefix: Prefix used for companion image filenames

        Returns:
            Markdown formatted string
        """
        from datetime import datetime

        # Build context info
        context_parts = []
        if island_id is not None:
            context_parts.append(f"island_{island_id}")
        if generation is not None:
            context_parts.append(f"gen_{generation}")
        context_str = " | ".join(context_parts) if context_parts else "N/A"

        lines = [
            f"# Algorithm: {self.name or self.id}",
            "",
            f"**ID:** `{self.id}`",
            f"**Context:** {context_str}",
            f"**Stage:** {stage}",
            f"**Domain:** {self.domain}",
            f"**Insight Type:** {self.insight_type.value}",
            f"**Generation:** {self.generation}",
            f"**Created:** {datetime.fromtimestamp(self.created_at).isoformat()}",
            "",
            "## Description",
            "",
            self.description,
            "",
        ]

        if self.key_innovations:
            lines.append("## Key Innovations")
            lines.append("")
            for innovation in self.key_innovations:
                lines.append(f"- {innovation}")
            lines.append("")

        if self.design_notes:
            lines.append("## Design Notes")
            lines.append("")
            lines.append(self.design_notes)
            lines.append("")

        # Code artifacts (only show for code and evaluation stages)
        if stage in ("code", "evaluation") and self.code_artifacts:
            lines.append("## Code Artifacts")
            lines.append("")
            for artifact in self.code_artifacts:
                lines.append(f"### `{artifact.file_path}`")
                lines.append("")
                lines.append(f"**Language:** {artifact.language}")
                if artifact.is_entrypoint:
                    lines.append("**Entrypoint:** Yes")
                lines.append("")
                lines.append("```" + artifact.language)
                lines.append(artifact.content)
                lines.append("```")
                lines.append("")

        # Evaluation results (only for evaluation stage)
        if stage == "evaluation" and self.evaluation:
            lines.append("## Evaluation Results")
            lines.append("")
            lines.append(f"**Score:** {self.evaluation.score:.4f}")
            lines.append(f"**Evaluation Time:** {self.evaluation.evaluation_time_ms:.2f}ms")
            if self.evaluation.error:
                lines.append(f"**Error:** {self.evaluation.error}")
            if self.evaluation.metrics:
                lines.append("")
                lines.append("### Metrics")
                for name, value in self.evaluation.metrics.items():
                    lines.append(f"- **{name}:** {value}")
            lines.append("")

        # Generation metadata
        if self.generation_meta:
            lines.append("## Generation Metadata")
            lines.append("")
            lines.append(f"- **Operator:** {self.generation_meta.operator}")
            lines.append(f"- **LLM Provider:** {self.generation_meta.llm_provider}")
            lines.append(f"- **LLM Model:** {self.generation_meta.llm_model}")
            lines.append(f"- **Temperature:** {self.generation_meta.temperature}")
            if self.generation_meta.tokens_used:
                lines.append(f"- **Tokens Used:** {self.generation_meta.tokens_used}")
            if self.generation_meta.generation_time_ms:
                lines.append(f"- **Generation Time:** {self.generation_meta.generation_time_ms:.2f}ms")
            lines.append("")

        # Evolution metadata
        if self.parent_ids:
            lines.append("## Evolution")
            lines.append("")
            lines.append(f"- **Parent IDs:** {', '.join(f'`{pid}`' for pid in self.parent_ids)}")
            lines.append(f"- **Population ID:** `{self.population_id}`")
            if self.lines_added or self.lines_removed:
                lines.append(f"- **Lines Added:** {self.lines_added}")
                lines.append(f"- **Lines Removed:** {self.lines_removed}")
            lines.append("")

        # Behavior data (multimodal)
        if self.behavior_data:
            lines.append("## Behavior Data")
            lines.append("")
            for bd_idx, bd in enumerate(self.behavior_data):
                if bd.observation:
                    instance_label = bd.instance_id or f"#{bd_idx}"
                    lines.append(f"### Observation (instance: {instance_label})")
                    lines.append("")
                    lines.append(bd.observation)
                    lines.append("")
                for viz_idx, viz in enumerate(bd.visualizations):
                    if filename_prefix:
                        img_filename = f"{filename_prefix}_viz_{bd_idx}_{viz_idx}.png"
                        lines.append(f"### {viz.label}")
                        lines.append("")
                        lines.append(f"![{viz.label}]({img_filename})")
                        lines.append("")
                    else:
                        lines.append(f"### {viz.label}")
                        lines.append("")
                        has_img = "Yes" if viz.data_base64 else "No"
                        lines.append(f"- Image available: {has_img}")
                        lines.append("")

        return "\n".join(lines)


    @classmethod
    def load(cls, json_path: Path) -> "Algorithm":
        """Load an Algorithm from a saved JSON file, rehydrating behavior images.

        Companion PNG files (saved by ``write()``) are loaded and their
        contents restored into ``BehaviorVisualization.data_base64``.

        Args:
            json_path: Path to the algorithm JSON file.

        Returns:
            Algorithm instance with behavior images rehydrated.
        """
        import json

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        algorithm = cls.model_validate(data)

        # Rehydrate images from companion PNG files
        parent_dir = json_path.parent
        for bd in algorithm.behavior_data:
            for viz in bd.visualizations:
                saved_file = (viz.raw_data or {}).get("_saved_image_file")
                if saved_file and viz.data_base64 is None:
                    img_path = parent_dir / saved_file
                    if img_path.exists():
                        viz.data_base64 = _base64.b64encode(
                            img_path.read_bytes()
                        ).decode()
                    # Clean up the internal reference
                    if viz.raw_data and "_saved_image_file" in viz.raw_data:
                        del viz.raw_data["_saved_image_file"]
                        if not viz.raw_data:
                            viz.raw_data = None

                # Rehydrate raw_data from companion JSON files
                saved_raw_file = (viz.raw_data or {}).get("_saved_raw_data_file")
                if saved_raw_file and len(viz.raw_data) == 1:
                    raw_path = parent_dir / saved_raw_file
                    if raw_path.exists():
                        with open(raw_path, encoding="utf-8") as rf:
                            viz.raw_data = json.load(rf)

        return algorithm


class ComponentDependency(BaseModel):
    """Describes a dependency between components in a system.

    Defines how algorithms in a multi-algorithm system depend on each other.
    """

    source_component_id: str  # ID of the component that provides the output
    target_component_id: str  # ID of the component that consumes the output
    interface: str = "default"  # Interface/API name for the dependency
    data_format: str = "json"  # Data format passed between components
    is_required: bool = True  # Whether this dependency is required

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SystemConfig(BaseModel):
    """System-level configuration for a multi-algorithm solution.

    Contains global configuration that applies to the entire system,
    not just individual components.
    """

    domain: str = (
        "generic"  # System domain (e.g. "search_engine", "recommendation", "autonomous_driving")
    )
    name: str = ""  # System name
    version: str = "0.1.0"  # System version
    environment: str = "production"  # Target environment (dev/staging/production)
    resource_constraints: dict[str, Any] = (
        {}
    )  # Global resource constraints (latency SLA, memory limit, etc.)
    custom_settings: dict[str, Any] = {}  # Custom system-wide settings

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SystemEvaluationResult(BaseModel):
    """System-level evaluation result for a multi-algorithm solution.

    Contains both overall system performance and per-component metrics.
    """

    overall_score: float  # Overall system fitness score
    component_scores: dict[str, float] = {}  # Per-component individual scores
    system_metrics: dict[str, float] = (
        {}
    )  # System-level metrics (end-to-end latency, throughput, etc.)
    component_metrics: dict[str, dict[str, float]] = {}  # Per-component detailed metrics
    error: str | None = None  # System-level error if evaluation failed
    evaluation_time_ms: float = 0.0  # Total evaluation time
    integration_test_results: dict[str, Any] = {}  # Integration test results

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SystemIndividual(BaseModel):
    """A system-level individual for multi-algorithm co-evolution.

    Represents a complete engineering solution composed of multiple interdependent
    algorithms that evolve together as a single unit. This enables co-evolution
    of entire systems rather than just individual algorithms.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    system_config: SystemConfig
    description: str = ""  # Natural language description of the system
    design_rationale: str = ""  # High-level design rationale

    # Components (algorithm instances) that make up this system
    components: dict[str, Algorithm] = {}  # component_id -> Algorithm
    component_roles: dict[str, str] = (
        {}
    )  # component_id -> role (e.g. "ranker", "retriever", "filter")

    # Dependencies between components
    dependencies: list[ComponentDependency] = []  # All dependencies between components
    dependency_graph: Any | None = None  # Optional pre-built dependency graph

    # Evolution metadata
    parent_system_ids: list[str] = []  # Parent system IDs for crossover
    generation: int = 0  # Generation of this system
    island_id: int | None = None  # Island ID for island GA

    # Generation provenance
    generation_meta: GenerationMetadata | None = None  # How this system was generated

    # Evaluation results
    evaluation: SystemEvaluationResult | None = None  # System-level evaluation result

    # Additional metadata
    tags: list[str] = []
    custom_metadata: dict[str, Any] = {}

    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def score(self) -> float | None:
        """Get the overall system fitness score."""
        return self.evaluation.overall_score if self.evaluation else None

    def is_evaluated(self) -> bool:
        """Check if the entire system has been successfully evaluated."""
        # All components must be evaluated AND system-level evaluation must exist
        if not self.evaluation or self.evaluation.error:
            return False
        return all(component.is_evaluated() for component in self.components.values())

    def has_all_code(self) -> bool:
        """Check if all components have generated code."""
        return all(comp.has_code() for comp in self.components.values())

    def add_component(self, component_id: str, insight: Algorithm, role: str = "") -> None:
        """Add an algorithm component to the system.

        Args:
            component_id: Unique ID for this component in the system
            insight: Algorithm instance
            role: Optional role description for this component
        """
        self.components[component_id] = insight
        if role:
            self.component_roles[component_id] = role
        self.updated_at = time.time()

    def add_dependency(self, source_id: str, target_id: str, **kwargs) -> None:
        """Add a dependency between two components.

        Args:
            source_id: ID of the component providing the output
            target_id: ID of the component consuming the output
            **kwargs: Additional ComponentDependency parameters
        """
        dep = ComponentDependency(
            source_component_id=source_id, target_component_id=target_id, **kwargs
        )
        self.dependencies.append(dep)
        self.updated_at = time.time()

    def set_system_evaluation(self, overall_score: float, **kwargs) -> None:
        """Set the system-level evaluation result.

        Args:
            overall_score: Overall system fitness score
            **kwargs: Additional SystemEvaluationResult parameters
        """
        self.evaluation = SystemEvaluationResult(overall_score=overall_score, **kwargs)
        self.updated_at = time.time()

    def get_downstream_components(self, component_id: str) -> list[str]:
        """Get all components that depend on the given component.

        Args:
            component_id: ID of the component to find dependents for

        Returns:
            List of component IDs that depend on the given component
        """
        return [
            dep.target_component_id
            for dep in self.dependencies
            if dep.source_component_id == component_id
        ]

    def get_upstream_components(self, component_id: str) -> list[str]:
        """Get all components that the given component depends on.

        Args:
            component_id: ID of the component to find dependencies for

        Returns:
            List of component IDs that the given component depends on
        """
        return [
            dep.source_component_id
            for dep in self.dependencies
            if dep.target_component_id == component_id
        ]

    def validate_dependencies(self) -> tuple[bool, list[str]]:
        """Validate the dependency graph for cycles and missing components.

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        component_ids = set(self.components.keys())

        # Check for missing components in dependencies
        for dep in self.dependencies:
            if dep.source_component_id not in component_ids:
                errors.append(f"Source component not found: {dep.source_component_id}")
            if dep.target_component_id not in component_ids:
                errors.append(f"Target component not found: {dep.target_component_id}")

        # Check for cycles in dependency graph
        visited = set()
        recursion_stack = set()

        def has_cycle(node: str) -> bool:
            if node not in component_ids:
                return False
            visited.add(node)
            recursion_stack.add(node)

            for neighbor in self.get_downstream_components(node):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in recursion_stack:
                    errors.append(f"Circular dependency found: {node} -> {neighbor}")
                    return True

            recursion_stack.remove(node)
            return False

        for component_id in component_ids:
            if component_id not in visited:
                has_cycle(component_id)

        return len(errors) == 0, errors


class BasePlanner(ABC, Registrable):
    """Abstract planner interface for LLM4AD.

    The Planner coordinates the evolution process by:
    1. Using a sampler to generate new algorithm insights
    2. Handling mutation and crossover operations
    3. Selecting individuals for the next generation
    4. Maintaining population diversity
    """

    def __init__(
        self,
        provider: BaseProvider,
        coder: BaseCoder,
        memory: Any,
        config: PlannerConfig,
        analyzed_repository: AnalyzedRepository,
        version_control: BaseVersionControl,
        state_tracker: StateTracker,
    ):
        """Initialize planner.

        Args:
            provider: LLM provider for generating insights
            coder: Code generator for creating and modifying code
            memory: Memory system for storing/retrieving insights
            config: Planner configuration dict containing:
                - population_size: Target population size
                - mutation_rate: Probability of mutation vs crossover
                - parent_selection_strategy: Strategy for selecting parents (tournament, roulette, etc.)
                - survival_selection_strategy: Strategy for selecting survivors (truncation, tournament, etc.)
                - elite_ratio: Ratio of elite individuals to preserve
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks
            version_control: Version control system for worktree management
            state_tracker: State tracker for tracking evolution progress and workspace paths
        """
        self.provider = provider
        self.coder = coder
        self.memory = memory
        self.config = config
        self.analyzed_repository = analyzed_repository
        self.version_control = version_control
        self.state_tracker = state_tracker
        self.sampler_map: dict[str, Any] = {}
        self.samplers: list = []

    @abstractmethod
    async def init(
        self,
        island_id: int,
        generation_id: int,
        algorithm_id: str,
        **kwargs,
    ) -> WorktreeInfo:
        """Initialize the planner for a new evolution run.

        This method is called at the start of an evolution run to set up any necessary
        state, such as creating the initial population or loading relevant data.

        Args:
            island_id: ID of the island (for island GA)
            generation_id: ID of the generation
            algorithm_id: ID of the algorithm
            **kwargs: Additional initialization parameters
        """
        pass

    @abstractmethod
    async def plan(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs,
    ) -> Algorithm:
        """Plan the next algorithm individual in the evolution process.

        This is the main entry point that the orchestrator calls to get
        the next algorithm individual. It coordinates sampling, mutation,
        or crossover based on the current evolution state.

        Args:
            population: Current population of algorithms
            generation: Current generation number
            **kwargs: Additional planning parameters

        Returns:
            New algorithm individual with insight description
        """
        pass

    @abstractmethod
    async def implement(self, algorithm: Algorithm, **kwargs) -> Algorithm:
        """Implement the given algorithm insight into code.

        This method takes an Algorithm insight (which may only have a description)
        and generates the corresponding code artifacts. This is typically called
        after planning to turn an insight into executable code.

        Args:
            algorithm: Algorithm insight with description to implement
            worktree: Version control worktree info to apply changes to
            **kwargs: Additional implementation parameters
        """
        pass

    async def build(self, algorithm: Algorithm, worktree: WorktreeInfo) -> bool:
        """Build and verify the generated algorithm code.

        Executes the build script configured in planner config within the
        worktree directory. If no build config is set, this is a no-op.

        When ``script`` is a dict, the key matching the current platform
        (``sys.platform``: ``linux``, ``darwin``, or ``win32`` mapped to
        ``windows``) is used. If no matching key exists the build is skipped.

        On Windows the script is executed via ``cmd /c``, on Unix via ``bash``.

        Args:
            algorithm: Algorithm with code to build.
            worktree: Worktree containing the generated code.

        Returns:
            True if build succeeds or no build is configured.

        Raises:
            RuntimeError: If the build fails (non-zero exit code or timeout).
        """
        import asyncio
        import sys

        build_config = self.config.get("build") if isinstance(self.config, dict) else self.config.build
        if build_config is not None and isinstance(build_config, dict):
            from llm4ad.config.planner import BuildConfig
            build_config = BuildConfig(**build_config)
        if build_config is None:
            return True

        raw_script = build_config.script
        timeout = build_config.timeout

        # Resolve platform-specific script
        if isinstance(raw_script, dict):
            platform_key = "windows" if sys.platform == "win32" else sys.platform
            script = raw_script.get(platform_key)
            if script is None:
                return True
        else:
            script = raw_script

        is_windows = sys.platform == "win32"

        # Write script to a temp file in the worktree and execute
        if is_windows:
            script_path = Path(worktree.path) / ".llm4ad_build.bat"
            script_path.write_text(f"@echo off\r\n{script}\r\n")
        else:
            script_path = Path(worktree.path) / ".llm4ad_build.sh"
            script_path.write_text(f"#!/usr/bin/env bash\nset -e\n{script}\n")
            script_path.chmod(0o755)

        try:
            cmd = ["cmd", "/c", str(script_path)] if is_windows else ["bash", str(script_path)]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=worktree.path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError as e:
                proc.kill()
                await proc.communicate()
                raise RuntimeError(
                    f"Build timed out after {timeout}s for algorithm {algorithm.name}"
                ) from e

            if proc.returncode != 0:
                stderr_text = stderr.decode(errors="replace").strip()
                stdout_text = stdout.decode(errors="replace").strip()
                details = stderr_text or stdout_text
                raise RuntimeError(
                    f"Build failed for algorithm {algorithm.name} "
                    f"(exit code {proc.returncode}): {details}"
                )

            return True
        finally:
            if script_path.exists():
                script_path.unlink()

    def select_parents(
        self,
        population: list[Algorithm],
        n_parents: int = 2,
        strategy: str = "tournament",
    ) -> list[Algorithm]:
        """Select parent(s) from population for reproduction.

        This is a helper method that can be used by concrete implementations.

        Args:
            population: Current population
            n_parents: Number of parents to select
            strategy: Selection strategy (tournament, roulette, random)

        Returns:
            Selected parents
        """
        import random

        if not population:
            return []

        parents = []

        if strategy == "tournament":
            # Tournament selection
            tournament_size = min(3, len(population))
            for _ in range(n_parents):
                tournament = random.sample(population, tournament_size)
                # Select best by score (or random if no scores)
                evaluated = [p for p in tournament if p.score is not None]
                winner = max(evaluated, key=lambda p: p.score) if evaluated else random.choice(tournament)
                parents.append(winner)

        elif strategy == "roulette":
            # Roulette wheel selection (fitness proportionate)
            evaluated = [p for p in population if p.score is not None]
            if not evaluated:
                # Fall back to random if no scores
                return random.sample(population, min(n_parents, len(population)))

            # Use scores as fitness (assume higher is better)
            total_fitness = sum(p.score for p in evaluated)

            for _ in range(n_parents):
                pick = random.uniform(0, total_fitness)
                current = 0.0
                for individual in evaluated:
                    current += individual.score
                    if current >= pick:
                        parents.append(individual)
                        break

        elif strategy == "rank":
            # Rank-based selection
            evaluated = [p for p in population if p.score is not None]
            if not evaluated:
                return random.sample(population, min(n_parents, len(population)))

            # Sort by score (descending)
            sorted_pop = sorted(evaluated, key=lambda p: p.score, reverse=True)

            # Selection probability proportional to rank
            n = len(sorted_pop)
            total_rank = n * (n + 1) / 2  # Sum of 1..n

            for _ in range(n_parents):
                pick = random.uniform(0, total_rank)
                current = 0.0
                for i, individual in enumerate(sorted_pop):
                    # Rank 1 (best) gets n, rank n (worst) gets 1
                    rank_weight = n - i
                    current += rank_weight
                    if current >= pick:
                        parents.append(individual)
                        break

        elif strategy == "random":
            # Random selection
            parents = random.sample(population, min(n_parents, len(population)))

        else:
            raise ValueError(f"Unknown selection strategy: {strategy}")

        return parents

    def select_survivors(
        self,
        population: list[Algorithm],
        n_survivors: int,
        strategy: str = "truncation",
    ) -> list[Algorithm]:
        """Select survivors from a competition pool.

        Used after offspring generation to determine which individuals
        (offspring + non-elite parents) survive to the next generation.

        Args:
            population: Competition pool of individuals
            n_survivors: Number of survivors to select
            strategy: Selection strategy (truncation, tournament, roulette, rank, random)

        Returns:
            Selected survivors
        """
        if not population or n_survivors <= 0:
            return []

        n_survivors = min(n_survivors, len(population))

        if strategy == "truncation":
            # Deterministic: sort by score descending, take top N
            evaluated = sorted(
                [ind for ind in population if ind.is_evaluated()],
                key=lambda x: x.score,
                reverse=True,
            )
            unevaluated = [ind for ind in population if not ind.is_evaluated()]
            ranked = evaluated + unevaluated
            return ranked[:n_survivors]

        # Delegate to select_parents for stochastic strategies
        return self.select_parents(population, n_survivors, strategy)
