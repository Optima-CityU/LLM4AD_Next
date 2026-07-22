from unittest.mock import Mock

from app.models import TaskStatus
from app.tasks import evolution


def test_duplicate_delivery_is_skipped_before_task_side_effects(monkeypatch):
    """同一业务任务被 Redis 重投时，不得再次准备环境或启动容器。"""
    monkeypatch.setattr(evolution, "acquire_task_execution_lock", lambda *_args: False)
    push_log = Mock()
    prepare = Mock(side_effect=AssertionError("must not prepare"))
    finalize = Mock(side_effect=AssertionError("must not finalize"))
    update_status = Mock(side_effect=AssertionError("must not update status"))
    monkeypatch.setattr(evolution, "push_log_entry", push_log)
    monkeypatch.setattr(
        evolution,
        "_prepare_run_environment",
        prepare,
    )
    monkeypatch.setattr(evolution, "_finalize_task", finalize)
    monkeypatch.setattr(evolution, "_update_task_status", update_status)

    result = evolution.run_evolution.run({"task_id": "task-1"})

    assert result is None
    push_log.assert_called_once()
    prepare.assert_not_called()
    finalize.assert_not_called()
    update_status.assert_not_called()


def test_lock_holder_executes_and_releases_lock(monkeypatch):
    from app.services import evolution_runner

    acquire = Mock(return_value=True)
    release = Mock(return_value=True)
    update_status = Mock()
    prepare = Mock()
    run_container = Mock()
    finalize = Mock()
    monkeypatch.setattr(evolution, "acquire_task_execution_lock", acquire)
    monkeypatch.setattr(evolution, "release_task_execution_lock", release)
    monkeypatch.setattr(evolution, "_update_task_status", update_status)
    monkeypatch.setattr(evolution, "_prepare_run_environment", prepare)
    monkeypatch.setattr(evolution_runner, "run_evolution_container", run_container)
    monkeypatch.setattr(evolution, "_finalize_task", finalize)

    evolution.run_evolution.push_request(id="celery-primary")
    try:
        evolution.run_evolution.run({"task_id": "task-1"})
    finally:
        evolution.run_evolution.pop_request()

    update_status.assert_called_once_with("celery-primary", TaskStatus.RUNNING)
    prepare.assert_called_once_with({"task_id": "task-1"})
    run_container.assert_called_once_with(
        {"task_id": "task-1"}, check_cancelled=evolution.run_evolution.is_aborted
    )
    finalize.assert_called_once_with("celery-primary", TaskStatus.COMPLETED)
    assert acquire.call_args.args[0] == "task-1"
    assert release.call_args.args[0] == "task-1"
    assert release.call_args.args[1] == acquire.call_args.args[1]


def test_terminal_task_is_not_revived_by_a_delayed_delivery(monkeypatch):
    acquire = Mock(return_value=True)
    release = Mock(return_value=True)
    update_status = Mock(return_value=None)
    prepare = Mock(side_effect=AssertionError("must not prepare"))
    finalize = Mock(side_effect=AssertionError("must not finalize"))
    monkeypatch.setattr(evolution, "acquire_task_execution_lock", acquire)
    monkeypatch.setattr(evolution, "release_task_execution_lock", release)
    monkeypatch.setattr(evolution, "_update_task_status", update_status)
    monkeypatch.setattr(evolution, "_prepare_run_environment", prepare)
    monkeypatch.setattr(evolution, "_finalize_task", finalize)

    evolution.run_evolution.push_request(id="celery-delayed")
    try:
        evolution.run_evolution.run({"task_id": "task-1"})
    finally:
        evolution.run_evolution.pop_request()

    update_status.assert_called_once_with("celery-delayed", TaskStatus.RUNNING)
    prepare.assert_not_called()
    finalize.assert_not_called()
    release.assert_called_once()
