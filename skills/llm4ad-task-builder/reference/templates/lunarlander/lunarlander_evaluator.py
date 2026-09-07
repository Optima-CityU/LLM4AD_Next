"""LunarLander policy evaluator for the LLM4AD platform.

This evaluator demonstrates the RL rollout pattern:
- The policy is a function that maps state to action (called many times per episode)
- The evaluator spawns ITSELF as a subprocess to run gymnasium episodes in isolation
- The __main__ block is the subprocess entry point that loads policy and runs episodes
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)


@BaseEvaluator.register("lunarlander_policy_evaluator")
class LunarLanderPolicyEvaluator(BaseEvaluator):
    """Evaluator for LunarLander control policies.

    Spawns itself as a subprocess to run gymnasium episodes in isolation,
    preventing crashes or state corruption from affecting other evaluations.
    """

    def __init__(self):
        """Initialize with LunarLander-specific metrics."""
        self._metrics = [
            Metric(
                name="episode_reward",
                type=MetricType.MAXIMIZE,
                weight=1.0,
                description="Total reward accumulated in the episode",
            ),
            Metric(
                name="execution_time_ms",
                type=MetricType.MINIMIZE,
                weight=0.1,
                description="Policy execution time in milliseconds",
            ),
            Metric(
                name="fuel_consumed",
                type=MetricType.MINIMIZE,
                weight=0.2,
                description="Total fuel consumed (engine usage)",
            ),
            Metric(
                name="success",
                type=MetricType.MAXIMIZE,
                weight=5.0,
                description="Whether landing was successful (1.0) or not (0.0)",
            ),
        ]

    @property
    def name(self) -> str:
        """Return evaluator name."""
        return "lunarlander_policy_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Return supported metrics."""
        return self._metrics

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Evaluate a LunarLander policy on one seed configuration.

        Args:
            cfg: Evaluation context with project_root, data_path, and timeout.

        Returns:
            EvaluationResult with score (episode reward) and metrics.
        """
        start_time = time.time()

        try:
            # 1. Locate the policy file (the worktree contains local_path's
            #    contents directly, so choose_action.py sits at the worktree root).
            project_root = Path(cfg.project_root)
            policy_dir = project_root

            if not (policy_dir / "choose_action.py").exists():
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Policy file not found: {policy_dir / 'choose_action.py'}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # 2. Load episode configuration
            with open(cfg.data_path, encoding="utf-8") as f:
                episode_config = json.load(f)

            seed = episode_config.get("seed", 42)
            max_steps = episode_config.get("max_steps", 200)

            # 3. Prepare input JSON for subprocess
            input_json = json.dumps({
                "policy_dir": str(policy_dir),
                "seed": seed,
                "max_steps": max_steps,
            })

            # 4. Spawn THIS FILE as subprocess to run episode in isolation
            evaluator_script = str(Path(__file__).resolve())
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                evaluator_script,
                input_json,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=cfg.timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Episode timed out after {cfg.timeout}s",
                    duration_ms=cfg.timeout * 1000,
                )

            execution_time_ms = (time.time() - start_time) * 1000
            stdout_text = stdout_bytes.decode(errors="replace")
            stderr_text = stderr_bytes.decode(errors="replace")

            if proc.returncode != 0:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Episode crashed (exit code {proc.returncode}): {stderr_text.strip()}",
                    duration_ms=execution_time_ms,
                )

            # 5. Parse episode results from subprocess output
            try:
                result = json.loads(stdout_text.strip())
            except json.JSONDecodeError as e:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Invalid JSON output: {e}",
                    duration_ms=execution_time_ms,
                )

            episode_reward = result.get("episode_reward", 0.0)
            fuel_consumed = result.get("fuel_consumed", 0.0)
            landing_success = result.get("success", 0.0)

            return EvaluationResult(
                score=episode_reward,
                metrics={
                    "episode_reward": episode_reward,
                    "execution_time_ms": execution_time_ms,
                    "fuel_consumed": fuel_consumed,
                    "success": landing_success,
                },
                success=True,
                duration_ms=execution_time_ms,
                metadata={"dataset": cfg.data_path, "seed": seed},
            )

        except Exception as e:
            return EvaluationResult(
                score=0.0,
                metrics={},
                success=False,
                error_message=f"Evaluation error: {e}",
                duration_ms=(time.time() - start_time) * 1000,
            )

    def _load_policy_module(self, policy_dir: Path):
        """Dynamically load the policy module from the policy directory."""
        import importlib.util

        choose_action_file = policy_dir / "choose_action.py"
        if not choose_action_file.exists():
            raise FileNotFoundError(f"Policy file not found: {choose_action_file}")

        spec = importlib.util.spec_from_file_location("policy_module", choose_action_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load policy from {choose_action_file}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run_episode(self, policy_func, seed: int, max_steps: int = 200) -> dict:
        """Run one gymnasium episode with the policy.

        Args:
            policy_func: The choose_action function from the policy module.
            seed: Random seed for episode initialization.
            max_steps: Maximum steps per episode.

        Returns:
            Dictionary with episode_reward, fuel_consumed, and success.
        """
        import gymnasium as gym

        env = gym.make("LunarLander-v3", render_mode=None)
        observation, _ = env.reset(seed=seed)

        episode_reward = 0.0
        fuel_consumed = 0.0
        action = 0
        pre_observation = observation

        for _step in range(max_steps + 1):
            # Call policy to get action
            action = policy_func(observation.tolist(), action, pre_observation.tolist())

            # Validate action
            if not isinstance(action, int) or action < 0 or action > 3:
                action = 0

            # Track fuel consumption
            if action == 2:  # Main engine
                fuel_consumed += 0.3
            elif action in [1, 3]:  # Side engines
                fuel_consumed += 0.03

            # Step environment
            pre_observation = observation
            observation, reward, done, truncated, _info = env.step(action)
            episode_reward += reward

            if done or truncated:
                break

        env.close()

        # Landing is successful if final reward is positive and high
        landing_success = 1.0 if episode_reward > 100 else 0.0

        return {
            "episode_reward": episode_reward,
            "fuel_consumed": fuel_consumed,
            "success": landing_success,
        }


def _subprocess_main():
    """Subprocess entry point: load policy, run episode, print results."""
    input_data = json.loads(sys.argv[1])
    policy_dir = Path(input_data["policy_dir"])
    seed = input_data["seed"]
    max_steps = input_data["max_steps"]

    evaluator = LunarLanderPolicyEvaluator()
    module = evaluator._load_policy_module(policy_dir)
    result = evaluator._run_episode(module.choose_action, seed, max_steps)

    print(json.dumps(result))


if __name__ == "__main__":
    _subprocess_main()
