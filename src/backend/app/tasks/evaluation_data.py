"""Retry only evidence transfer, independently of evolution execution."""

from celery import signals

from app.core.celery import celery_app


@celery_app.task(bind=True, max_retries=None, ignore_result=True)
def recover_uploads(self):
    """Recover durable pending snapshots without executing evaluators again."""
    from app.services.evaluation_data import retry_outboxes

    try:
        complete = retry_outboxes()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60) from exc
    if not complete:
        raise self.retry(countdown=60)


@signals.worker_ready.connect
def recover_on_start(**_kwargs):
    """Schedule one reconciliation when an existing worker becomes ready."""
    recover_uploads.delay()
