"""Main LLM4AD entry point class.

This class provides a unified entry point for the LLM4AD platform that:
- Loads and validates configuration
- Initializes all components via the registry system
- Sets up the workspace directory structure
- Orchestrates the full evolution pipeline
- Handles checkpoint saving and loading
- Exports evolution state for visualization
"""

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.config.schema import AppConfig, CustomEvaluatorConfig
from llm4ad.evaluator import BaseEvaluator, EvaluationDispatcher
from llm4ad.infra.provider import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository, BaseRepositoryAnalyzer
from llm4ad.infra.state import StateTracker
from llm4ad.infra.version_control.base import BaseVersionControl
from llm4ad.orchestrator import (
    BaseOrchestrator,
    EvolutionResult,
)
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.planner.base import BasePlanner


class LLM4AD:
    """Main entry point for LLM4AD algorithm design platform.

    This class handles the complete lifecycle of an algorithm design run:
    1. Load and validate configuration
    2. Initialize all components (providers, planner, coder, evaluator, orchestrator)
    3. Set up workspace directory structure
    4. Run the evolution pipeline
    5. Export results and evolution state for visualization

    Example:
        ```python
        from llm4ad import LLM4AD

        # Run from config file
        llm4ad = LLM4AD("config.yaml")
        result = await llm4ad.run()
        print(f"Best score: {result.best_individual.score}")

        # Save state for visualization
        llm4ad.save_state("output/evolution_state.json")
        ```
    """

    def __init__(self, config: AppConfig | str):
        """Initialize LLM4AD from configuration.

        Args:
            config: Either an AppConfig instance or a path to a config file
                (supports .yaml, .yml, or .json).
        """
        # Load configuration
        self._config_dir: str | None = None
        if isinstance(config, str):
            config_path = config
            config_file_dir = str(Path(config_path).expanduser().resolve().parent)
            if config_path.endswith((".yaml", ".yml")):
                self.config = AppConfig.from_yaml(config_path)
            elif config_path.endswith(".json"):
                self.config = AppConfig.from_json(config_path)
            else:
                raise ValueError(
                    f"Unsupported config file format: {config_path}. "
                    "Supported formats: .yaml, .yml, .json"
                )
            # Determine effective path resolution base
            if self.config.path_resolution == "config_dir":
                self._config_dir = config_file_dir

            # Resolve base_dir relative to resolution base
            base_dir = Path(self.config.base_dir).expanduser()
            if not base_dir.is_absolute() and self._config_dir:
                base_dir = (Path(self._config_dir) / base_dir).resolve()
            self.config = self.config.model_copy(update={"base_dir": str(base_dir)})
        else:
            self.config = config

        # Initialize components
        self._providers: dict[str, BaseProvider] = {}
        self._planner: BasePlanner | None = None
        self._coder: BaseCoder | None = None
        self._embedding_client: EmbeddingClient | None = None
        self._evaluator: BaseEvaluator | None = None
        self._dispatcher: EvaluationDispatcher | None = None
        self._version_control: BaseVersionControl | None = None
        self._repo_analyzer: BaseRepositoryAnalyzer | None = None
        self._analyzed_repository: AnalyzedRepository | None = None
        self._state_tracker: StateTracker | None = None
        self._orchestrator: BaseOrchestrator | None = None

        # Setup logging
        self._setup_workspace()
        self._setup_logging()

        # Create all components
        self._initialize_components()

    @property
    def state_tracker(self) -> StateTracker:
        """Get the current evolution state tracker.

        Returns:
            The StateTracker instance containing all evolution data.
        """
        if self._state_tracker is None:
            raise RuntimeError("StateTracker not initialized yet")
        return self._state_tracker

    def get_state(self) -> StateTracker:
        """Get the current evolution state tracker.

        Returns:
            The StateTracker instance with all evolution data.
        """
        return self.state_tracker

    @property
    def analyzed_repository(self) -> AnalyzedRepository | None:
        """Get the analyzed repository result if repository analysis was performed.

        Returns:
            The AnalyzedRepository containing all discovered EVOLVE blocks,
            or None if repository analysis was not configured or performed.
        """
        return self._analyzed_repository

    def export_state(self) -> dict[str, Any]:
        """Export complete evolution state for visualization.

        Returns:
            JSON-serializable dictionary containing all evolution state data
            including historical metrics, timing, and resource usage.
        """
        return self.state_tracker.export_for_visualization()

    def save_state(self, path: str) -> None:
        """Save complete evolution state to a JSON file for visualization.

        Args:
            path: Path where to save the JSON file.
        """
        with open(path, "w", encoding='UTF-8') as f:
            json.dump(self.export_state(), f, indent=2)

    async def run(self, resume_from_checkpoint: str | None = None) -> EvolutionResult:
        """Run the full algorithm design pipeline.

        Args:
            resume_from_checkpoint: Optional path to a checkpoint file
                to resume evolution from.

        Returns:
            EvolutionResult containing the final best algorithm and metadata.
        """
        if self._orchestrator is None:
            raise RuntimeError("Orchestrator not initialized")

        # Resume from checkpoint if provided
        if resume_from_checkpoint is not None:
            await self._orchestrator.load_checkpoint(resume_from_checkpoint)

        # Run the full evolution
        result = await self._orchestrator.run()

        # Save the final state if workspace is enabled
        if self.config.workspace.auto_create:
            run_dir = self.get_run_directory()
            state_path = run_dir / "state" / "evolution_state.json"
            self.save_state(str(state_path))

            # Snapshot the best (and Pareto archive, if MEoH) into a stable
            # ``best/`` directory so frontends can find code + metadata
            # without having to parse checkpoints or read git worktrees.
            from llm4ad.infra.best_exporter import export_best

            best_subdir = self.config.workspace.subdirs.get("best", "best")
            export_best(result, run_dir / best_subdir)

        return result

    def _initialize_components(self) -> None:
        """Initialize all components from configuration."""
        # Initialize providers
        for provider_config in self.config.providers:
            provider = BaseProvider.create(
                provider_config.type,
                config=provider_config.model_dump(),
            )
            self._providers[provider_config.name] = provider

        # Get planner provider
        planner_provider_name = self.config.planner.provider
        if planner_provider_name not in self._providers:
            raise ValueError(
                f"Planner provider '{planner_provider_name}' not found in providers configuration"
            )
        planner_provider = self._providers[planner_provider_name]

        # Initialize planner
        from llm4ad.planner.memory import create_memory, create_memory_extractor

        # Get coder provider
        coder_provider_name = self.config.coder.provider
        if coder_provider_name not in self._providers:
            raise ValueError(
                f"Coder provider '{coder_provider_name}' not found in providers configuration"
            )
        coder_provider = self._providers[coder_provider_name]
        coder_provider_config = next(
            p for p in self.config.providers if p.name == coder_provider_name
        )

        # Initialize coder
        coder_type = self.config.coder.type
        coder_kwargs: dict[str, Any] = {"config": self.config.coder}
        if coder_type == "custom":
            coder_kwargs["provider"] = coder_provider
        elif coder_type in ("claude_code", "opencode"):
            coder_kwargs["provider_config"] = coder_provider_config
        self._coder = BaseCoder.create(
            coder_type,
            **coder_kwargs,
        )

        # Initialize evaluator
        evaluator_config = self.config.evaluator

        # Resolve evaluator provider for custom evaluators
        eval_provider_config = None
        if isinstance(evaluator_config, CustomEvaluatorConfig):
            eval_provider_name = evaluator_config.provider
            if eval_provider_name not in self._providers:
                raise ValueError(
                    f"Evaluator provider '{eval_provider_name}' not found in providers configuration"
                )
            eval_provider_config = next(
                p for p in self.config.providers if p.name == eval_provider_name
            )

        # Initilize dispatcher
        self._dispatcher = EvaluationDispatcher(
            config=evaluator_config,
            behavior_storage=self.config.multimodal.behavior_storage,
            config_dir=self._config_dir,
            provider_config=eval_provider_config,
        )

        # Initialize version control if enabled
        if self.config.version_control.enabled:
            # Resolve local_path relative to config directory
            vc_model = self.config.version_control
            if (
                self._config_dir
                and hasattr(vc_model, "local_path")
                and vc_model.local_path
            ):
                lp = Path(vc_model.local_path).expanduser()
                if not lp.is_absolute():
                    resolved_lp = str((Path(self._config_dir) / lp).resolve())
                    vc_model = vc_model.model_copy(update={"local_path": resolved_lp})

            # Convert pydantic model to dict
            vc_config_dict = (
                vc_model
                if isinstance(vc_model, dict)
                else vc_model.model_dump()
            )
            self._version_control = BaseVersionControl.create(
                self.config.version_control.type,
                config=vc_config_dict,
                base_dir=self.get_run_directory(),
            )

        # Initialize repository analyzer if configured
        if self.config.repo_analyzer is not None:
            # Convert pydantic model to dict
            ra_config_dict = (
                self.config.repo_analyzer
                if isinstance(self.config.repo_analyzer, dict)
                else self.config.repo_analyzer.model_dump()
            )
            ra_type = ra_config_dict.get("type", "evolve_detector")
            self._repo_analyzer = BaseRepositoryAnalyzer.create(
                ra_type,
                config=ra_config_dict,
            )

            # Determine repository path to analyze
            repo_path: Path | None = None
            if (
                self.config.version_control.enabled
                and hasattr(self.config.version_control, "local_path")
                and self.config.version_control.local_path
            ):
                lp = Path(self.config.version_control.local_path).expanduser()
                repo_path = (
                    (Path(self._config_dir) / lp).resolve()
                    if not lp.is_absolute() and self._config_dir
                    else lp.resolve()
                )

            # Run analysis if we have a path
            if repo_path is not None and repo_path.exists():
                self._analyzed_repository = self._repo_analyzer.analyze(repo_path)

        # Initialize StateTracker and set workspace directories
        self._state_tracker = StateTracker()
        run_dir = self.get_run_directory()
        self._state_tracker.set_workspace_dirs(
            base_dir=run_dir,
            subdirs=self.config.workspace.subdirs,
        )

        memory = create_memory(self.config.memory)
        memory.set_query_provider(planner_provider)

        # Set up memory persistence directory
        if self._state_tracker.memory_dir:
            memory.set_memory_dir(self._state_tracker.memory_dir)

        # Load static memory cards from config
        memory.load_static_cards(
            inline_cards=self.config.memory.static_cards,
        )

        # Create memory extractor for auto-extraction during evolution
        if self.config.memory.auto_extraction.enabled:
            auto_extraction_config = self.config.memory.auto_extraction
            if self.config.memory.type == "mindmemos_cloud":
                auto_extraction_config = auto_extraction_config.model_copy(
                    update={"type": "mindmemos_raw_extractor"}
                )
            memory.extractor = create_memory_extractor(
                provider=planner_provider,
                config=auto_extraction_config,
            )

        # Use planner_type from evolution config if available (e.g. MEoH),
        # otherwise fall back to planner config's type.
        planner_type = getattr(self.config.evolution, "planner_type", None) or self.config.planner.type
        # MEoH planner expects a plain dict config; keep PlannerConfig for others.
        planner_config: Any = self.config.planner
        if planner_type == "meoh_evolution":
            planner_config = self.config.planner.model_dump()
        else:
            # Inject multimodal config into each sampler for MLES support
            if self.config.multimodal.enabled and planner_config:
                multimodal_dict = self.config.multimodal.model_dump()
                for sampler_cfg in planner_config.samplers:
                    sampler_cfg.config.setdefault("multimodal", multimodal_dict)
        self._planner = BasePlanner.create(
            planner_type,
            provider=planner_provider,
            coder=self._coder,
            memory=memory,
            config=planner_config,
            analyzed_repository=self.analyzed_repository,
            version_control=self._version_control,
            state_tracker=self._state_tracker,
        )


        # Get monitor - currently using default console monitor
        from llm4ad.infra.monitor.console import ConsoleMonitor

        monitor = ConsoleMonitor(config={})

        if hasattr(self.config.evolution, "background") and self.config.background:
            self.config.evolution.background = self.config.background

        self._initialize_embedding_client()

        self._orchestrator = BaseOrchestrator.create(
            self.config.evolution.type,
            planner=self._planner,
            coder=self._coder,
            dispatcher=self._dispatcher,
            monitor=monitor,
            version_control=self._version_control,
            config=self.config.evolution,
            state_tracker=self._state_tracker,
            background=self.config.background,
            embedding_client=self._embedding_client
        )

    def _initialize_embedding_client(self) -> None:
        """Initialize the optional embedding client and log the effective routing."""
        if not self.config.embedding or not self.config.embedding.type:
            logger.info("Embedding client not configured; evaluation trace embeddings are disabled")
            self._embedding_client = None
            return

        text_model = None
        code_model = None
        if self.config.embedding.text_config:
            text_model = self.config.embedding.text_config.model
        if self.config.embedding.code_config:
            code_model = self.config.embedding.code_config.model

        logger.info(
            "Initializing embedding client: type={}, model={}, text_model={}, code_model={}",
            self.config.embedding.type,
            self.config.embedding.model or "",
            text_model or "",
            code_model or "",
        )
        self._embedding_client = EmbeddingClient(self.config.embedding)

    def _setup_workspace(self) -> None:
        """Create the workspace directory structure according to configuration."""
        if not self.config.workspace.auto_create:
            return

        run_dir = self.get_run_directory()
        os.makedirs(run_dir, exist_ok=True)

        # Create all subdirectories
        for subdir_name in self.config.workspace.subdirs.values():
            subdir_path = run_dir / subdir_name
            os.makedirs(subdir_path, exist_ok=True)

    def _setup_logging(self) -> None:
        """Configure logging based on logging config."""
        import sys

        from loguru import logger

        # Clear default handlers
        logger.remove()

        # Get logging config
        log_config = self.config.logging

        # Determine log level (default INFO)
        level = log_config.level if log_config.level else "INFO"

        # Build log format
        log_format = log_config.format or (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )

        # Add console handler if enabled
        if log_config.console:
            logger.add(
                sys.stderr,
                format=log_format,
                level=level,
                colorize=True,
                serialize=log_config.json_format,
            )

        # Determine log file path: use config value if set, otherwise auto-generate
        if log_config.file:
            log_file = Path(log_config.file)
            log_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            run_dir = self._get_run_directory()
            logs_dir = run_dir / "logs"
            os.makedirs(logs_dir, exist_ok=True)
            log_file = logs_dir / "llm4ad.log"

        # Add file handler
        logger.add(
            log_file,
            format=log_format,
            level="TRACE",  # Log everything to file
            rotation="100 MB",
            retention="10 days",
            compression="zip",
            serialize=log_config.json_format,
        )

    def _get_run_directory(self) -> Path:
        """Get the full path to the run directory.

        Returns:
            Path object for the run directory: {base_dir}/{project_name}/{run_id}/
        """
        return Path(self.config.base_dir) / self.config.project_name / self.config.run_id

    def get_checkpoint_directory(self) -> Path:
        """Get the checkpoint directory within the run workspace.

        Returns:
            Path object for the checkpoints directory.
        """
        run_dir = self.get_run_directory()
        subdir_name = self.config.workspace.subdirs.get("checkpoints", "checkpoints")
        return run_dir / subdir_name

    def print_run_summary(self) -> None:
        """Print a summary of the current run configuration using rich for colored output."""
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table, box
        from rich.text import Text

        console = Console()

        # Run directory info
        run_dir = self.get_run_directory()

        # Build main title
        title = Text("LLM4AD Run Summary", style="bold cyan")

        # Create a table for basic info
        basic_table = Table(show_header=False, box=None, padding=(0, 2))
        basic_table.add_column("key", style="bold white")
        basic_table.add_column("value", style="cyan")

        basic_table.add_row("Project:", self.config.project_name)
        basic_table.add_row("Run ID:", self.config.run_id)
        basic_table.add_row("Working Directory:", os.path.abspath(run_dir))

        # Create main panel
        console.print(Panel(basic_table, title=title, border_style="cyan", expand=False))

        # Repository info
        if self._analyzed_repository is not None:
            repo_table = Table(show_header=False, box=None, padding=(0, 2))
            repo_table.add_column("key", style="bold white")
            repo_table.add_column("value", style="green")

            repo_table.add_row("Repository:", str(self._analyzed_repository.repo_path))
            repo_table.add_row("Files Analyzed:", str(self._analyzed_repository.files_analyzed))
            repo_table.add_row("Files with EVOLVE blocks:", str(self._analyzed_repository.files_with_blocks))
            repo_table.add_row("Total EVOLVE blocks:", str(len(self._analyzed_repository.evolvable_blocks)))

            console.print(Panel(repo_table, title="Repository", border_style="green", expand=False))

            # List evolvable blocks
            if self._analyzed_repository.evolvable_blocks:
                blocks_table = Table(title="Evolving Modules", box=box.SIMPLE)
                blocks_table.add_column("File", style="yellow")
                # blocks_table.add_column("Block Name", style="cyan")
                blocks_table.add_column("Language", style="magenta")

                for block in self._analyzed_repository.evolvable_blocks:
                    blocks_table.add_row(
                        block.file_path,
                        # block.block_name or "unnamed",
                        block.language
                    )
                console.print(blocks_table)
        else:
            console.print(Panel("Not configured", title="Repository", border_style="green", expand=False))

        # Evolution config
        evolution_table = self.config.evolution.to_table()
        evolution_table.add_row("Planner:", self.config.planner.type)
        evolution_table.add_row("Planner Provider:", self.config.planner.provider)
        evolution_table.add_row("Coder:", self.config.coder.type)
        evolution_table.add_row("Coder Provider:", self.config.coder.provider)

        console.print(Panel(evolution_table, title="Evolution Config", border_style="yellow", expand=False))

        # Providers
        providers_table = Table(title="LLM Providers", box=box.SIMPLE)
        providers_table.add_column("Name", style="cyan")
        providers_table.add_column("Type", style="green")
        providers_table.add_column("Model", style="magenta")

        for provider_config in self.config.providers:
            provider = self._providers.get(provider_config.name)
            model = provider.model if provider else "N/A"
            providers_table.add_row(provider_config.name, provider_config.type, model)

        console.print(providers_table)

    def get_run_directory(self) -> Path:
        """Get the full path to the run directory.

        Returns:
            Path object for the run directory: {base_dir}/{project_name}/{run_id}/
        """
        return Path(self.config.base_dir) / self.config.project_name / self.config.run_id
