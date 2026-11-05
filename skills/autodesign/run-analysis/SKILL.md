---
name: llm4ad-run-analysis
description: Analyze actual LLM4AD run files, best code, raw metrics, internal scores, logs, candidates and checkpoints through the AutoDesign task runtime. Use for progress, result explanation, failures and comparisons between task-family runs.
---

# LLM4AD Run Analysis

Read [references/run-artifacts.md](references/run-artifacts.md) before interpreting
files. It maps actual library and backend producers to paths and fields.

1. Call `mcp__llm4ad_autodesign__get_task_context(task_id=...)` to identify the
   selected run, status, effective configuration and family. Reuse existing task
   context; do not ask again for a paper or original task brief.
2. Call `mcp__llm4ad_autodesign__list_task_files(area="run", task_id=...)`, then
   read relevant returned paths with `mcp__llm4ad_autodesign__read_task_file`.
   Reads default to the full file. For a large file, request explicit line ranges,
   track which lines were inspected, and distinguish partial from complete evidence.
   Discover paths instead of assuming every run wrote every artifact.
3. Inspect actual candidate/evaluation records and evaluator definitions as well
   as summaries. Use `area="input"` for canonical evaluator, data and source;
   runtime copies can differ. Report raw metric names, values, units, direction,
   aggregation and failures separately from internal fitness. Check candidate
   identity and generation before associating code with a score.
4. For a finished run, inspect `best/code/`, `best/metadata.json` and
   `best/summary.txt` at their listed location. Best is exported at run end, never
   promised live. While running or after an interrupted run, inspect logs,
   `generated/` records, visualization caches and any actual checkpoints instead.
   Empty/missing best output does not establish that no candidate improved.
5. State findings with task ID, exact file paths and JSON fields (or line ranges).
   Cite evidence for progress, best candidate, validity and limitations. Acknowledge
   missing artifacts, failed instances or incomparable data/evaluators. Do not
   invent scores, confidence, benchmark results or artifacts. Explain findings
   in the conversation; create a separate analysis document only when the user
   explicitly requests one.

Analysis requests authorize reading and explanation. A task version has its own
conversation and run files; creating or reading a child does not move the
conversation default. An unstarted child has no results of its own. Read the
parent explicitly, and distinguish the parent's measured parameters from the
child's proposed settings. Do not create, modify or restart a task merely to
produce an analysis. If the user asks to change or rerun, use the installed **autodesign** Skill to materialize adjustments in a
child and execute the requested action without a blanket confirmation flow.
Recommendations alone do not authorize child creation, running or stopping.

Use the installed **llm4ad-task-memory** Skill to consult relevant prior cards or
save a finding when requested. Existing cards can suggest a diagnosis; verify it
against this run's real artifacts before treating it as an observed result.
