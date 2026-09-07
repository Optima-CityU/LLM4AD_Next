"""Container runtime user-isolation contracts."""

from app.services.container_runtime import ContainerJob, ContainerJobSpec


def test_container_job_passes_explicit_runtime_user_to_docker() -> None:
    job = ContainerJob(
        ContainerJobSpec(image="task-runner:test", user="65534:65534"),
        client=object(),
    )

    assert job._build_run_kwargs()["user"] == "65534:65534"


def test_container_job_keeps_existing_default_when_user_is_unspecified() -> None:
    job = ContainerJob(ContainerJobSpec(image="task-runner:test"), client=object())

    assert "user" not in job._build_run_kwargs()
