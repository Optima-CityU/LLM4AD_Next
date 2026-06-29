import asyncio
import hashlib
import random
from typing import Any, Literal

from loguru import logger
from openai import AsyncOpenAI

from llm4ad.config.app import EmbeddingConfig


class EmbeddingClient:
    """Client for generating embeddings supporting per-task routing in 'local' mode."""

    def __init__(self, config: EmbeddingConfig) -> None:
        """Initialize the embedding client with per‑task routing capabilities.

        The client supports three operational modes:
        - **mock** – No real API calls; deterministic pseudo‑random vectors.
        - **local** – Uses separate OpenAI‑compatible clients for `text` and `code` tasks.
        - **standard** (openai, jina, openai_compatible) – Single client shared by both task types.

        For `local` mode, both `config.text_config` and `config.code_config` must be provided.
        For `jina` mode, the configured base URL is used as-is so deployments can choose
        the official or domestic endpoint.

        Args:
            config: Embedding configuration containing provider type, model names,
                    API keys, base URLs, and concurrency limits.

        Raises:
            ValueError: If `config.type == 'local'` but either `text_config` or
                        `code_config` is `None`.

        Notes:
            - The client’s own retry mechanism is used (`max_retries=0` disables OpenAI's native retries).
            - A semaphore limits concurrent embedding requests to `config.embedding_func_max_async`.
            - Task‑specific clients are stored internally in `self._task_clients` as
              `{task_type: (AsyncOpenAI client, model_name)}`.
        """
        self._type = config.type
        self._dim = config.dim
        self._semaphore = asyncio.Semaphore(config.embedding_func_max_async)

        # We store clients in a mapping: {task_type: (client, model_name, task_hint, provider_type)}
        self._task_clients: dict[str, tuple[AsyncOpenAI | None, str, str | None, str | None]] = {}

        if self._type == 'mock':
            logger.info("Initialized EmbeddingClient in MOCK mode.")
            return

        if self._type == 'local':
            # Initialize specialized clients for 'text' and 'code'
            if not config.text_config or not config.code_config:
                raise ValueError("text_config and code_config must be provided for 'local' type")

            self._task_clients['text'] = (
                AsyncOpenAI(
                    api_key=config.text_config.api_key,
                    base_url=config.text_config.base_url,
                    timeout=config.text_config.timeout or config.timeout,
                    max_retries=0,
                ),
                config.text_config.model,
                config.text_config.task,
                config.text_config.type,
            )
            self._task_clients['code'] = (
                AsyncOpenAI(
                    api_key=config.code_config.api_key,
                    base_url=config.code_config.base_url,
                    timeout=config.code_config.timeout or config.timeout,
                    max_retries=0,
                ),
                config.code_config.model,
                config.code_config.task,
                config.code_config.type,
            )
        else:
            # Standard single-client initialization
            base_url = config.base_url

            client = AsyncOpenAI(api_key=config.api_key, base_url=base_url, timeout=config.timeout, max_retries=0)
            # Both task types use the same client/model
            self._task_clients['text'] = (client, config.model, config.text_task, config.type)
            self._task_clients['code'] = (client, config.model, config.code_task, config.type)

    async def _embed_batch_chunk(
            self,
            texts: list[str],
            task_type: Literal["text", "code"] | None = None,
            retries: int = 3,
            delay: float = 1.0
    ) -> None | list[list[float]] | list[Any]:
        # Handle default task type
        task_key = task_type if task_type in ('text', 'code') else 'text'

        # ==========================================
        # MOCK Mode
        # ==========================================
        if self._type == 'mock':
            await asyncio.sleep(random.uniform(0.01, 0.05))
            results = []
            for text in texts:
                seed = int(hashlib.md5(text.encode('utf-8')).hexdigest(), 16)
                rng = random.Random(seed)
                vec = [rng.uniform(-1.0, 1.0) for _ in range(self._dim)]
                norm = sum(x ** 2 for x in vec) ** 0.5
                results.append([x / norm for x in vec] if norm > 0 else vec)
            return results

        # Get relevant client and model for the current task
        client, model_name, configured_task, route_provider_type = self._task_clients.get(task_key, (None, "", None, None))
        if not client:
            raise RuntimeError(f"No client configured for task type: {task_key}")

        params = {
            "input": texts,
            "model": model_name
        }

        # Handle provider task hints. For local vLLM Jina, task is usually handled by
        # the endpoint/model itself and comes through configured_task.
        if route_provider_type == 'jina':
            task_hint = configured_task
            if not task_hint:
                task_hint = 'text-matching' if task_type == 'text' else 'code.passage' if task_type == 'code' else None
            if task_hint:
                params["extra_body"] = {"task": task_hint, "late_chunking": False}
        elif configured_task:
            params["extra_body"] = {"task": configured_task}

        async with self._semaphore:
            for attempt in range(retries):
                try:
                    response = await client.embeddings.create(**params)
                    sorted_data = sorted(response.data, key=lambda x: x.index)
                    return [item.embedding for item in sorted_data]
                except Exception as e:
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(f"Batch failed. Provider: {self._type} | Task: {task_key} | Error: {e}")
                        return [[0.0] * self._dim] * len(texts)
            return None

    async def run_single(self, text: str, task_type: Literal["text", "code"] | None = None) -> list[float]:
        """Generate an embedding vector for a single text or code string.

        This is a convenience wrapper around `run_batch` for a single input.

        Args:
            text: The input string to embed.
            task_type: Optional hint indicating whether the input is natural language
                       (`"text"`) or source code (`"code"`). Used by some providers
                       (e.g., Jina) to apply task‑specific optimisations. Defaults to
                       `None`, which falls back to `"text"` internally.

        Returns:
            A list of floats representing the embedding vector. Its length equals
            the configured embedding dimension (`self._dim`).

        Notes:
            - In `mock` mode, the returned vector is deterministic (based on MD5 of the input)
              and L2‑normalised.
            - In `local` mode, the request is routed to the task‑specific client/model
              defined in the configuration.
        """
        results = await self._embed_batch_chunk([text], task_type=task_type)
        return results[0]

    async def run_batch(
            self,
            texts: list[str],
            task_type: Literal["text", "code"] | None = None,
            batch_size: int = 100,
            show_progress: bool = False,
    ) -> list[list[float]]:
        """Generate embedding vectors for a batch of texts or code snippets.

        The input list is split into chunks of size `batch_size`. Each chunk is
        processed concurrently, and results are returned in the original order.

        Args:
            texts: List of input strings to embed.
            task_type: Optional hint indicating the type of the inputs.
                       Affects request parameters for providers like Jina.
                       Defaults to `None` (treated as `"text"`).
            batch_size: Maximum number of texts per API request. Larger batches
                        reduce HTTP overhead but may hit provider limits.
            show_progress: If `True`, displays a progress bar using `tqdm.asyncio`.
                           Silently falls back to no progress bar if `tqdm` is not installed.

        Returns:
            A list of embedding vectors, one per input text, preserving the original order.
            Each vector is a list of floats with length `self._dim`.

        Raises:
            RuntimeError: If no client is configured for the given `task_type` (should not
                          happen under normal operation).

        Notes:
            - The method respects the concurrency semaphore (`embedding_func_max_async`).
            - Failed batches (after retries) return zero vectors for that chunk to avoid
              crashing the entire process.
            - In `mock` mode, vectors are deterministic and L2‑normalised.
            - For `local` mode, each chunk is routed to the client/model corresponding
              to `task_type` (or `"text"` if `task_type` is `None`).
        """
        if not texts:
            return []
        chunks = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        tasks = [self._embed_batch_chunk(chunk, task_type) for chunk in chunks]

        if show_progress:
            try:
                from tqdm.asyncio import tqdm
                chunk_results = await tqdm.gather(*tasks, desc=f"Generating {task_type or 'text'} Embeddings")
            except ImportError:
                chunk_results = await asyncio.gather(*tasks)
        else:
            chunk_results = await asyncio.gather(*tasks)

        return [embedding for chunk in chunk_results for embedding in chunk]

    async def shutdown(self) -> None:
        """Close all unique OpenAI clients."""
        # Use set to avoid closing the same client multiple times
        unique_clients = {c for c, _m, _task, _provider_type in self._task_clients.values() if c is not None}
        for client in unique_clients:
            await client.close()
