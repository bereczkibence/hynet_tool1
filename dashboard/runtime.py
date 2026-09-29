from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RunStatus:
    """Current state of one dashboard background run."""

    state: str
    message: str = ""


class DashboardRunManager(Generic[T]):
    """Single-worker background runner for local dashboard sessions."""

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._future: Future[T] | None = None

    @property
    def future(self) -> Future[T] | None:
        return self._future

    def submit(self, fn: Callable[..., T], *args, **kwargs) -> Future[T]:
        if self._future is not None and not self._future.done():
            raise RuntimeError("A dashboard run is already in progress.")
        self._future = self._executor.submit(fn, *args, **kwargs)
        return self._future

    def status(self) -> RunStatus:
        if self._future is None:
            return RunStatus("idle", "No run has been submitted.")
        if self._future.running():
            return RunStatus("running", "PF/OPF calculation is running.")
        if not self._future.done():
            return RunStatus("queued", "PF/OPF calculation is queued.")
        exc = self._future.exception()
        if exc is not None:
            return RunStatus("failed", f"{type(exc).__name__}: {exc}")
        return RunStatus("succeeded", "PF/OPF calculation finished.")

    def result(self) -> T | None:
        if self._future is None or not self._future.done():
            return None
        return self._future.result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
