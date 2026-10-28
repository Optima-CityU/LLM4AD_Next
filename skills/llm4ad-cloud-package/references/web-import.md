# Delivering and importing the cloud package

## Web ZIP import

1. Open or create a project, click **New Task**, and choose **Import ZIP** as
   the build mode.
2. Select the ZIP produced by `validate_and_pack.py`; no manual extraction is
   needed. Optionally override the task name; otherwise it uses `project_name`
   from `config.yaml`.
3. Leave **Enable memory (recommended)** checked to inherit the current account
   and project's memory settings, even if an older package disables memory.
   A valid long-term binding uses MindMemOS; otherwise task-local memory is
   enabled. Use **Configure long-term memory models** when available to bind
   a chat model and an embedding model without losing the selected ZIP. Uncheck
   memory only when explicitly choosing a run without it. See [memory.md](memory.md).
4. Click **Import and create**. The platform checks the single-root archive,
   file paths, task configuration, existing storage quotas, and the file hashes
   in `validation.json` when present. It creates and selects a new task in the
   current project.
5. In the task configuration, select the platform model for planning/code
   generation. Review the imported method, evaluator, context/output settings
   against the selected model, and any required embedding service.
6. Start evolution when ready and inspect logs and resulting candidates.

The import saves source/data in RustFS under a single task folder and writes
the parsed configuration into `Task.input_args`. Model bindings are reset for the
current account; importing never starts evolution or runs uploaded Python. The
effective task memory settings come from the import choice and platform defaults;
the uploaded `config.yaml` and validation hashes remain unchanged. Package-specific
extraction and static-card settings are preserved, while remote bindings are reset.
The runtime downloads fresh files and strips the single top-level directory, so
candidate/evaluator/dataset paths remain relative to the package root.

The local report is preserved as a file. Matching hashes confirm that its input
files have not changed, not that the report was issued by a trusted server.
Local baseline verification does not certify generated candidates, server
dependency compatibility, or a real LLM evolution run. A server rerun uses the
normal task-container execution path, not the import API process.

After import, edit configuration in the task settings and source/data in the
task inputs. Uploading or editing `config.yaml` alone does not replace stored
`input_args`, and changes made only in a past run's IDE are not canonical inputs.
If changing a package before import, regenerate its ZIP and report together.

## Older deployment fallback

If **Import ZIP** is unavailable on an older deployment, extract the archive,
create a project and a manual task, upload the extracted task directory in data
preparation, and import its `config.yaml` in parameter confirmation. Preserve
one top-level folder, then bind a platform model. The ordinary task file uploader
does not extract a ZIP automatically.
