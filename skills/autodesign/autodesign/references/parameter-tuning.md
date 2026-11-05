# Assist with task parameters

Use this workflow when the user enters from task parameter viewing, adjustment
or an already created child. The optimization problem, evaluation contract and
inputs already exist. Read them rather than asking the user to define them again.
Keep the existing project name unless the user asks to rename it.

## Establish the actual adjustment target

Call `mcp__llm4ad_autodesign__get_task_context` and identify the selected task ID,
`parent_id`, status, effective `config` and family. Use explicit IDs when reading
the parent or modifying the selected child; do not substitute the active or latest
version for the user's selected version.

Summarize the relevant current settings and ask about an unresolved tuning goal:
for example, lower model cost, faster valid evaluations, stronger exploration or
better solution quality. When the user has already stated the goal, proceed with
that goal. A parameter explanation request needs an explanation, not a new task.

Read relevant canonical evaluator and candidate files when their meaning affects
the recommendation. Consult the installed Cloud Skill's `references/configuration.md`
and `references/algorithms.md` for supported configuration and methods. Check the
actual selected method's fields before recommending a patch.

## Diagnose before recommending changes

For an unstarted child, use its parent's existing logs, candidate records, scores
and best snapshot when relevant. The installed **llm4ad-run-analysis** Skill maps
these artifacts to actual paths and fields. If there are no previous results,
state that the advice is based on configuration and task characteristics.

Relate each proposal to observed evidence or an explicit hypothesis. For example:

- Repeated invalid candidates: inspect syntax, evaluation errors, prompts and
  context sufficiency before increasing population or iteration counts.
- Empty code responses: inspect final-content length, finish reason and actual
  completion/reasoning token usage. Read the Cloud Skill's context and generation
  sizing guidance. Distinguish input context from an output budget shared with
  reasoning, and check the evolution coder's actual model rather than the chat
  assistant's model. Do not label empty upstream output as an extraction bug or
  assume a larger input window fixes it.
- Slow evaluations: inspect evaluator workload, timeouts, batch size and actual
  resource capacity before increasing concurrency.
- Stagnation: inspect candidate diversity, selection behavior and valid-score
  history before proposing changes to the selected method's exploration settings.
- Model cost or lost context: inspect the actual generation workload and chosen
  model capacity, then explain context/output tradeoffs. Use platform model
  settings for bindings; do not request credentials or invent provider capacity.

These are diagnostic examples, not mandatory parameter choices. Explain current
and proposed values, the reason for each change and the relevant tradeoff. Avoid
unrelated configuration changes, arbitrary run budgets or claims of improvement
without comparable evaluation evidence.

## Apply the requested adjustment

An explanation or recommendation does not authorize a run. When the user asks
for changes, use the existing task operations:

- Selected unstarted root or child: call `patch_task_parameters` with that child's ID
  and the nested configuration patch. Keep using that child for further edits.
- An already executed version: use `create_child_task` when authorized
  changes require an editable child. Explain the returned child ID so the user
  can open its parameters; preserve the original run for comparison.

Inspect the returned `changes`, then refresh that child's context to confirm the
stored values. Editing a draft `config.yaml` alone does not update task parameters.
For code, evaluator or data changes, follow the main Skill's child-file workflow
and real Cloud validation. Parameter-only patches do not need a new project,
ZIP package or another publication.

Finish with a concise account of what changed and why, what was actually checked,
and any remaining setup. The user can return to parameter configuration to inspect
the values and choose whether to start. Do not generate a tuning report file or
start evolution merely to finish the conversation.

The existing task ID remains the conversation default after creating another
version. Pass explicit IDs for mutations and for parent-result reads. Honor
`draft_conflicts` and use the main Skill's reconciliation workflow before
validation/publication. A refreshed context can contain current stored parameters
and deliberately different draft proposals; report which one a claim concerns.
Publication into a selected version keeps that version's platform model bindings
and memory selections. Neither recommendations nor parameter patches start a run.
