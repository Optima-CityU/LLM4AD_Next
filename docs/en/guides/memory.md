# Long-term Memory

Long-term memory is optional. When creating a task, you can choose no memory, temporary memory, or long-term memory after the service is ready.

## Before You Start: Bind Memory Models

Open **Global Memory**, then bind the following in **Default Strategy**:

1. A Chat model, which extracts structured memories from content and task activity.
2. An Embedding model, which converts memories into searchable vectors.

The first Embedding binding locks its model and dimension. If MindMemOS is unavailable, check the deployment environment first; model binding cannot work until the service is ready.

## Three Memory Scopes

### Global Memory

Global memory belongs to the current user and can be reused across projects. Use it for stable preferences, general algorithm lessons, and long-lived constraints.

### Project Memory

Project memory belongs only to the current project and can be reused by its tasks. Use it for project context, domain knowledge, evaluation preferences, and validated conclusions.

### Task Memory

Task memory serves the current task's process and results. Temporary memory exists only for the current run; with long-term memory enabled, task-scoped memory can participate in later injection according to task configuration.

Valuable task conclusions are not automatically made project memory. Select task cards, generate a summary preview, and confirm it to promote the result into project memory.

## Choose a Task Mode

- **No memory**: no memory is injected into the task.
- **Temporary memory**: the default mode; keeps only this run's task memory and does not persist it long term.
- **Long-term memory**: can use global, project, and task memory scopes. It is available only after the service and model binding are ready.

Long-term memory has two ways to obtain shared memory:

- **Automatic retrieval** recalls relevant global and project memory according to the task configuration.
- **Manual selection** injects only the global/project memory cards you check, without automatically retrieving shared memory.

Pinned memory controls only global and project scopes. Task memory still follows its task injection strategy. Disabled memory cards never participate in injection or manual selection.
