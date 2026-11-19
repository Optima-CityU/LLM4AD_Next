"""Bounded read retries that share the caller's original transport budget."""

import subprocess
import time

from tenacity import Retrying, retry_if_exception, stop_after_attempt, stop_before_delay, wait_exponential


def transient_read(error):
    """Recognize transport outages without retrying identity or semantic failures."""
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired, ConnectionError)):
        return True
    text = str(error).lower()
    return any(value in text for value in (
        "device offline", "device not ready", "connection reset", "connection refused",
        "connection closed", "error: closed", "timed out", "read timeout", "no devices/emulators found",
    )) or ("device" in text and "not found" in text)


def retry_read(operation, timeout, *, sleep=time.sleep, clock=time.monotonic):
    """Retry a read at most three times within its original remaining deadline.

    Args:
        operation: Read callable accepting the remaining timeout in seconds.
        timeout: Original remaining read budget in seconds.
        sleep: Optional deterministic sleeper.
        clock: Optional deterministic monotonic clock.

    Returns:
        The actual successful read, without fallback values.
    """
    if timeout <= 0:
        raise TimeoutError("Original HGamey read deadline elapsed")
    deadline = clock() + timeout
    for attempt in Retrying(
        stop=stop_after_attempt(3) | stop_before_delay(timeout),
        wait=wait_exponential(multiplier=1, max=2), retry=retry_if_exception(transient_read),
        sleep=sleep, reraise=True,
    ):
        with attempt:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("Original HGamey read deadline elapsed")
            return operation(remaining)
