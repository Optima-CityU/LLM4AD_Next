# Long-term Memory

LLM4AD uses MindMemOS to provide searchable long-term memory. It is optional: when creating a task, choose no memory, temporary memory, or long-term memory after the service and models are ready.

## Configure and Bind Memory Models

In the Web UI, open **Global Memory** → **Default Strategy**, then select and bind:

1. A **Chat model** to extract structured memories from task activity and manually entered content.
2. An **Embedding model** to convert memories into vectors for retrieval.

Before binding, make sure MindMemOS is enabled in the deployment. The first Embedding binding locks the model and dimension. Contact the deployment administrator if that memory space needs to use a different embedding model.

## Three Memory Scopes

### Global Memory

Global memory belongs to the current user and can be reused across projects. Use it for stable preferences, general algorithm lessons, and long-lived constraints.

### Project Memory

Project memory is available only within the current project and can be reused by its tasks. Use it for project context, domain knowledge, evaluation preferences, and validated conclusions.

### Task Memory

Task memory records the current task's process and results. Select valuable task cards, generate a summary preview, and confirm it to promote the result into project memory; promotion is never automatic.

## Use Memory in a Task

- **No memory**: no memory is injected into the task.
- **Temporary memory**: the default mode; it serves only the current run and is not stored as long-term memory.
- **Long-term memory**: can use global, project, and task scopes after the service and model binding are ready.

Long-term memory supports two ways to obtain shared memory:

- **Automatic retrieval** recalls relevant global and project memory for the current task.
- **Manual selection** injects only the global or project memory cards you select, without automatic retrieval.

## Notes

- Model binding is per user. Each user must bind models before first use.
- Manual selection controls only global and project memory; task memory still follows the task configuration.
- Disabled memory cards are excluded from automatic retrieval, manual selection, and task injection.
- If MindMemOS is unavailable or models are not bound, long-term memory cannot be used normally. Check the service status and binding configuration first.
