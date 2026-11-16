"""Bounded retries for reads; execution budgets remain in the durable journal."""

from collections.abc import Callable

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from .errors import EvaluationUnavailableError

READ_ERRORS = (OSError, TimeoutError, EvaluationUnavailableError)


def is_retryable(error: BaseException) -> bool:
    """Return whether an error represents the default transient read failures.

    Args:
        error: Observed failure, never an instruction to repeat external work.

    Returns:
        Whether a bounded read retry is appropriate.
    """
    return isinstance(error, READ_ERRORS)


def retry_read[T](fn: Callable[..., T], *args, max_attempts: int = 3, **kwargs) -> T:
    """Retry a read with finite exponential waits and preserve the last exception.

    Args:
        fn: Read operation without execution side effects.
        *args: Positional arguments forwarded to the read.
        max_attempts: Total calls including the first, defaulting to three.
        **kwargs: Keyword arguments forwarded to the read.

    Returns:
        The successful read result.

    Raises:
        ValueError: The attempt limit is not a positive integer.
        Exception: The final read failure or any nonretryable failure.
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    return Retrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=0.05, max=0.5),
        retry=retry_if_exception_type(READ_ERRORS),
        reraise=True,
    )(fn, *args, **kwargs)
