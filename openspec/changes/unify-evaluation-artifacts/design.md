# Design

Each dispatcher invocation owns an evaluation UUID, candidate/source fingerprint, fidelity and instance identity. Local evaluators write under EvalContext.artifact_dir and return explicit EvaluationArtifact declarations. Immutable snapshots and atomic records live outside the disposable run directory in cloud deployments; standalone CLI runs retain them in run/evaluations.

A worker uploader commits files, a RustFS manifest and a small discoverable index before acknowledging and deleting local bulk. Existing Celery workers recover unacknowledged transfers on restart. Remote reporter.artifact transport is preserved; terminal reports normalize into the same manifests before ACK, without duplicating remote objects. No new database table, service or dependency is required.

Task-authorized APIs merge stored, pending and live remote records. Files are served only through their manifest, with checksum verification and a bounded safe preview. Existing task deletion removes both namespaces and pending outboxes. The workbench replaces the remote-only tab with Evaluation details, preserving gateway progress and adding candidate/profile filters, details and previews. Generated API clients come from openapi-ts.


Transfer/deletion coordination uses an OS-released SQLite lease outside the evaluator-mounted outbox, rather than holding a task-status database lock during network IO. Lock contention waits cooperatively for gevent workers. Final task completion queues transfer reconciliation instead of waiting on object storage.

During object-store outages, the API can read task-owned local records and compact remote progress. The panel retries access and refreshes attachment availability after ACK while preserving the selected detail tab. Full details stay in RustFS; generated candidate logs retain compact summaries and record references. Old candidate associations are recovered read-only from saved generated-node evidence when unambiguous.

The existing read-only file tree, Markdown components and code editor are reused. The editor is loaded on demand; collapsed metadata does not instantiate it. Layout uses the panel's container width, because workbench sidebars make viewport breakpoints unsuitable.
