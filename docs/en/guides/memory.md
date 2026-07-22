# Long-term Memory

LLM4AD uses MindMemOS to provide searchable long-term memory. Its purpose is not merely to store conversation: when a new algorithm-design task starts, it brings verified experience, domain knowledge, and constraints back into the prompt. This reduces repeated trial and error and lets tasks in the same project build on one another.

Long-term memory is optional. When creating a task, choose no memory, temporary memory for the current run only, or long-term memory once the service and models are ready.

## Bind Memory Models First

In the Web UI, open **Global Memory** → **Default Strategy**, then select and bind:

1. A **Chat model** to turn manually entered content and task activity into structured memory cards, such as successful algorithm lessons, domain knowledge, and error reflections.
2. An **Embedding model** to turn memories into vectors that can be retrieved for a new task.

Binding is per user. Long-term memory becomes available in task creation only after MindMemOS is enabled in the deployment and the current user has bound both models. The first Embedding binding locks its model and dimension; contact the deployment administrator if that memory space must use a different embedding model.

## How the Three Memory Layers Work Together

Long-term memory is layered as **global → project → task**. Earlier layers have broader reuse, while later layers stay closer to the current problem.

### Global Memory: Seed New Tasks with Reusable Experience

Global memory belongs to the current user and can be reused across projects. Use it to proactively add or curate:

- proven algorithm-design lessons, heuristics, and implementation patterns;
- terminology, constraints, objectives, and evaluation principles for a problem domain;
- error reflections, recurring pitfalls, and ways to avoid them;
- long-lived preferences for code style, resource budgets, or result interpretation.

Place stable, general knowledge that is worth reusing in global memory. New project tasks can recall it automatically, or you can pin specific cards for a controlled prompt.

### Project Memory: Preserve Shared Context for One Project

Project memory belongs only to the current project and never enters another project. It complements global experience with project-specific background, data characteristics, evaluation criteria, validated conclusions, and local constraints.

It remains under user control: add, edit, or disable cards directly, or select valuable task-memory cards to generate a project-memory preview and confirm it before saving. This lets later tasks benefit from a task's results without automatically spreading unreviewed intermediate work across the project.

### Task Memory: Support the Current Run and Produce Curated Candidates

Task memory records experience and outcomes from the current task. It is the closest layer to the active problem, making it appropriate for useful approaches, failure causes, and leads that still need validation.

During a run, task-scoped memories are retrieved first and injected according to the task configuration. After the run, review task-memory cards, select genuinely reusable lessons, generate a project-memory summary preview, and confirm it. Promotion does not change the original task cards.

## Recommended Workflow

1. **Prepare global experience**: add cross-project algorithm lessons, domain knowledge, and error reflections in **Global Memory**.
2. **Set project context**: add project-specific goals, constraints, and validated conclusions in project memory.
3. **Create a long-term-memory task**: select **Long-term Memory** in task configuration.
4. **Choose shared-memory access**: use automatic retrieval or manual pinning based on how controlled the context needs to be.
5. **Configure task-memory injection**: choose how memories produced during the task should affect later prompts.
6. **Review and promote after the run**: turn selected high-value task cards into project memory; edit or disable global/project cards that are outdated.

## Use Memory in a Task

### Three Task Modes

- **No memory**: injects no memory into the task; useful for an independent baseline.
- **Temporary memory**: the default mode; used only during the current run and not stored as long-term memory.
- **Long-term memory**: uses global, project, and task scopes; it requires a ready service and bound models.

### Shared Memory: Automatic Retrieval or Manual Pinning

Global and project memory are shared scopes and have two access modes:

- **Automatic retrieval**: recalls enabled global and project memories by relevance to the current task. You can independently include each scope and set the maximum number of cards injected from each scope per prompt. This is appropriate for most exploratory tasks.
- **Manual pinning**: choose specific global and project cards to inject, without automatically searching shared memory at runtime. Use it for reproducible experiments, tightly controlled context, or testing a particular lesson.

Manual pinning controls only global and project memory. Task memory is still retrieved and injected using the task-memory strategy below.

### Task Memory: Injection Strategies

Task memory is produced during a run and always starts by retrieving candidate cards. Choose one of the following:

- **TopK**: inject the N most relevant cards by retrieval relevance; suitable for most tasks.
- **Weight mode**: order cards by the weight set in memory management. You can balance similarity and recency: a higher similarity weight favors lessons closest to the current problem, while a lower one favors newer lessons.
- **Random mode**: randomly inject from the candidates to increase exploration diversity and reduce premature fixation on one line of thought.

The task-memory limit controls how many task lessons are injected into each prompt. A larger limit gives richer context but consumes more prompt space.

## Advanced Retrieval and Reliability Settings

Defaults suit most tasks. Use advanced settings only when you need to trade off quality, cost, or reliability:

- **Search strategy**: `fast` has lower latency for routine retrieval; `agentic` plans retrieval more deeply and may be more accurate, but is slower and costs more.
- **Reranking and score threshold (not currently supported)**: the UI keeps these configuration fields for future use, but the current deployment does not provide reranking and the related settings have no effect.
- **Request/write timeouts and extraction language**: useful for slower networks, larger extraction jobs, or when extracted results must consistently use Chinese or English.
- **Fail-open**: when enabled, a temporary MindMemOS outage does not stop the task, but remote memory is skipped for that run. When disabled, a memory-service failure also fails the task, which is useful for strict memory-effect experiments.

## Notes

- A card's enabled state controls retrieval: disabled cards are excluded from automatic retrieval, manual pinning, and task injection.
- Keep stable, general knowledge in global memory and project-specific conclusions in project memory. Do not put short-lived experimental noise directly into the global scope.
- Promotion from task to project memory always needs user confirmation, so an accidental result does not become an assumption for later tasks.
- If MindMemOS is unavailable or models are not bound, long-term memory cannot be selected; use temporary memory or no memory instead.
