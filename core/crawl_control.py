import threading
import time
from collections.abc import Callable
from contextlib import contextmanager


class CrawlStoppedError(RuntimeError):
    pass


_THREAD_STATE = threading.local()


def _get_stop_checker() -> Callable[[], bool] | None:
    return getattr(_THREAD_STATE, "stop_checker", None)


@contextmanager
def crawl_stop_context(stop_checker: Callable[[], bool] | None):
    previous = _get_stop_checker()
    _THREAD_STATE.stop_checker = stop_checker
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_THREAD_STATE, "stop_checker")
            except AttributeError:
                pass
        else:
            _THREAD_STATE.stop_checker = previous


def is_stop_requested() -> bool:
    stop_checker = _get_stop_checker()
    return bool(stop_checker and stop_checker())


def raise_if_stop_requested(message: str = "Crawler da dung theo yeu cau nguoi dung.") -> None:
    if is_stop_requested():
        raise CrawlStoppedError(message)


def sleep_with_stop(seconds: float, *, interval: float = 0.25) -> None:
    deadline = time.monotonic() + max(seconds, 0.0)
    while True:
        raise_if_stop_requested()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(interval, remaining))
