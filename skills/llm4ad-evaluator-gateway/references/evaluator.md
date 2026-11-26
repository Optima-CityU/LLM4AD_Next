# Implement a gateway evaluator

Inspect the installed SDK and basic evaluator with `llm4ad evaluator-gateway source`.
Use the paths it reports to read the interfaces and a complete example matching
the installed SDK version.

- Subclass `GatewayEvaluator`, declare an `EvaluationContract` and implement
  `async evaluate(job, reporter)`. Choose a stable `evaluator_id`, version the
  evaluation logic and data, and declare the names and directions of metrics.
- Build and run the candidate from `job.project_root`. Use `job.parameters` for
  task options, and resolve trusted data and tools from the evaluator's own
  configuration. Keep credentials, datasets, and device addresses on the gateway.
- Return a finite `EvaluationResult.score` (higher is better) and metrics. Report
  progress through `reporter.progress` and declare output files under
  `job.artifact_dir` with `reporter.artifact`. Report diagnostic logs with
  `reporter.local_log`. Return only data suitable for the platform as metadata or evidence.
- Use `EvaluationError` with `failure_category: candidate` in its metadata for invalid
  candidate code or output, `EvaluationUnavailableError` for transient
  infrastructure failures, and `EvaluationConfigurationError` for invalid
  configuration. Leave queueing, retries, and local worker cancellation to the SDK.
- Use `before_task` and `after_task` when preparation and cleanup are needed.
  Use the SDK's `TaskCheckpoints` for reusable preparation and override `cancel`
  only when external resources need explicit restoration.

Exercise the actual evaluator with a representative baseline candidate and an
invalid candidate. Confirm scoring and evidence before accepting remote work.
