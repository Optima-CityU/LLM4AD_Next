"""Controller-owned durable deliveries backed by SQLiteAckQueue and public loky pools."""

from __future__ import annotations

import importlib
import json
import shutil
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from loky import ProcessPoolExecutor
from persistqueue import Empty, SQLiteAckQueue
from persistqueue.serializers import json as json_serializer

from .journal import gateway_lease
from .processes import (
    ProcessOwnershipError,
    cleanup_owner,
    create_owner,
    initialize_worker,
    owner_environment,
    worker_identity,
)


def _resolve(target: str):
    module, separator, name = target.partition(":")
    if not separator or module in {"", "__main__", "__mp_main__"} or not name or "<locals>" in name:
        raise ValueError("Targets must be importable module:qualname callables")
    value = importlib.import_module(module)
    for part in name.split("."):
        value = getattr(value, part)
    if not callable(value):
        raise ValueError("The target is not callable")
    return value


def _execute_packet(packet: dict):
    return _resolve(packet["target"])(*packet["args"], **packet["kwargs"])


class _DeliveryFuture(Future):
    def __init__(self, executor: DurableExecutor, key: str):
        super().__init__()
        self._executor = executor
        self._key = key

    def cancel(self) -> bool:
        return self._executor.cancel(self._key)


@dataclass
class _Slot:
    key: str
    index: int
    owner: Path
    pool: ProcessPoolExecutor | None = None
    inner: Future | None = None
    error: BaseException | None = None
    cleaned: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


class DurableExecutor:
    """Reserve each slot until its caller explicitly acknowledges durable completion.

    One controller owns each directory and all SQLite queue operations. Workers
    receive JSON packets and ownership markers, never queue objects. Completion
    cleans the slot generation before publishing its Future outcome; acknowledgement
    is deliberately separate from execution, retries and network result delivery.
    """

    def __init__(self, directory: str | Path, concurrency: int = 1):
        """Reconcile old process owners before resuming unacknowledged deliveries.

        Args:
            directory: Persistent controller directory, exclusive to this instance.
            concurrency: Maximum independent single-worker slots.

        Raises:
            ValueError: Capacity or persisted packets are invalid.
            ProcessOwnershipError: An old generation cannot be safely reconciled.
        """
        if type(concurrency) is not int or concurrency < 1:
            raise ValueError("concurrency must be a positive integer")
        self.directory = Path(directory).absolute()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.concurrency = concurrency
        self._lock = threading.RLock()
        self._manual_paused = False
        self._holds: set[str] = set()
        self._entries: dict[str, dict] = {}
        self._receipts: dict[str, int] = {}
        self._futures: dict[str, Future] = {}
        self._slots: dict[str, _Slot] = {}
        self._owners: dict[str, set[Path]] = {}
        self._cancelling: set[str] = set()
        self._closed = False
        self._queue = None
        self._completions = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="llm4ad-slot-cleanup")
        self._lease = gateway_lease(self.directory / "controller")
        self._lease.__enter__()
        try:
            self._queue = SQLiteAckQueue(
                str(self.directory / "queue"),
                auto_resume=False,
                multithreading=True,
                serializer=json_serializer,
            )
            for owner in sorted((self.directory / "owners").glob("*/owner.json")):
                cleanup_owner(owner.parent)
                key = json.loads(owner.read_text(encoding="utf-8"))["key"]
                self._owners.setdefault(key, set()).add(owner.parent)
            self._queue.resume_unack_tasks()
            acknowledged = set()
            for item in self._queue.queue():
                if str(item["status"]) not in {"0", "1", "2"}:
                    if str(item["status"]) in {"5", "9"} and isinstance(item["data"], dict):
                        acknowledged.add(item["data"].get("key"))
                    continue
                packet = self._validate_packet(item["data"])
                key = packet["key"]
                if key in self._entries:
                    if self._entries[key] != packet:
                        raise ValueError(f"Conflicting durable delivery key: {key}")
                    self._queue.ack(id=item["id"])
                    continue
                self._entries[key] = packet
                self._receipts[key] = item["id"]
                self._futures[key] = _DeliveryFuture(self, key)
            for key in acknowledged - self._entries.keys():
                self._prune_owners(key)
        except BaseException:
            self._queue = None
            self._completions.shutdown(wait=True)
            self._lease.__exit__(None, None, None)
            raise

    @property
    def paused(self) -> bool:
        """Return whether manual admission or any independent hold blocks dispatch."""
        with self._lock:
            return self._manual_paused or bool(self._holds)

    @paused.setter
    def paused(self, value: bool) -> None:
        with self._lock:
            self._manual_paused = bool(value)

    def hold(self, reason: str) -> None:
        """Add an independent, idempotent admission fence without stopping workers.

        Args:
            reason: Stable nonempty name for the owner of this fence.

        Raises:
            ValueError: The fence name is not a nonempty string.
        """
        if not isinstance(reason, str) or not reason:
            raise ValueError("An admission hold requires a nonempty reason")
        with self._lock:
            self._holds.add(reason)

    def release(self, reason: str) -> None:
        """Release only the named admission fence, preserving every other fence.

        Args:
            reason: Hold name previously supplied by its owner.
        """
        with self._lock:
            self._holds.discard(reason)

    def active_keys(self) -> tuple[str, ...]:
        """Return reserved slots, including completed deliveries awaiting ACK.

        Returns:
            A snapshot of retained slot keys; callers can inspect their Futures.
        """
        with self._lock:
            return tuple(self._slots)

    @staticmethod
    def _validate_packet(packet: dict) -> dict:
        if (
            not isinstance(packet, dict)
            or not isinstance(packet.get("key"), str)
            or not packet["key"]
            or not isinstance(packet.get("target"), str)
            or not isinstance(packet.get("args"), list)
            or not isinstance(packet.get("kwargs"), dict)
        ):
            raise ValueError("Invalid durable execution packet")
        return json.loads(json.dumps(packet, allow_nan=False))

    def submit(self, key: str, target, args: tuple = (), kwargs: dict | None = None) -> Future:
        """Persist or deduplicate a delivery without launching a worker.

        Args:
            key: Stable delivery identity, retained until ack or discard.
            target: Importable module-level callable, including class static methods.
            args: JSON-compatible positional arguments.
            kwargs: JSON-compatible keyword arguments.

        Returns:
            The delivery's local Future, shared by duplicate submissions.

        Raises:
            ValueError: The packet is invalid or conflicts with a retained key.
            RuntimeError: The controller has closed.
        """
        module, name = getattr(target, "__module__", ""), getattr(target, "__qualname__", "")
        reference = f"{module}:{name}"
        resolved = _resolve(reference)
        if resolved is not target and resolved != target:
            raise ValueError("Targets must resolve to the supplied importable callable")
        packet = self._validate_packet(
            {
                "key": key,
                "target": reference,
                "args": list(args),
                "kwargs": {} if kwargs is None else kwargs,
            }
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("DurableExecutor is closed")
            if key in self._entries:
                if self._entries[key] != packet:
                    raise ValueError(f"Conflicting durable delivery key: {key}")
                return self._futures[key]
            receipt = self._queue.put(packet)
            self._entries[key] = packet
            self._receipts[key] = receipt
            self._futures[key] = _DeliveryFuture(self, key)
            return self._futures[key]

    def poll(self) -> None:
        """Launch queued deliveries only into unreserved slots when admission is open.

        Finished and cancelled deliveries keep their slots until ack. Recovered
        deliveries can be discarded by the caller before its first poll.
        """
        with self._lock:
            if self._closed or self.paused or len(self._slots) >= self.concurrency:
                return
            occupied = {slot.index for slot in self._slots.values()}
            for index in range(self.concurrency):
                if len(self._slots) >= self.concurrency:
                    break
                if index in occupied:
                    continue
                try:
                    item = self._queue.get(block=False, raw=True)
                except Empty:
                    break
                packet = self._validate_packet(item["data"])
                key = packet["key"]
                future = self._futures[key]
                self._receipts[key] = item["pqid"]
                owner = self.directory / "owners" / uuid.uuid4().hex
                create_owner(owner, key=key, slot=index)
                self._owners.setdefault(key, set()).add(owner)
                slot = _Slot(key, index, owner)
                self._slots[key] = slot
                try:
                    slot.pool = ProcessPoolExecutor(
                        max_workers=1,
                        initializer=initialize_worker,
                        initargs=(str(owner),),
                        env=owner_environment(owner),
                    )
                    slot.inner = slot.pool.submit(_execute_packet, packet)
                    slot.inner.add_done_callback(lambda completed, current=slot: self._completed(current, completed))
                except BaseException as failure:
                    error = failure
                    try:
                        self._stop_slot(slot)
                    except BaseException as cleanup_error:
                        error = cleanup_error
                    future.set_exception(error)

    def _completed(self, slot: _Slot, inner: Future) -> None:
        # close() owns synchronous cleanup once its completion pool stops.
        with suppress(RuntimeError):
            self._completions.submit(self._finish, slot, inner)

    def _stop_slot(self, slot: _Slot) -> None:
        with slot.lock:
            if slot.cleaned:
                return
            try:
                if slot.pool is not None:
                    slot.pool.shutdown(wait=True, kill_workers=True)
                    slot.pool = None
                cleanup_owner(slot.owner)
                slot.error = None
                slot.cleaned = True
            except BaseException as error:
                slot.error = error
                raise

    def _finish(self, slot: _Slot, inner: Future) -> None:
        try:
            try:
                result, error = inner.result(), None
            except BaseException as failure:
                result, error = None, failure
            self._stop_slot(slot)
        except BaseException as failure:
            result, error = None, failure
        with self._lock:
            future = self._futures.get(slot.key)
            if future is not None and not future.done():
                if slot.key in self._cancelling and slot.error is None:
                    Future.cancel(future)
                elif error is None:
                    future.set_result(result)
                else:
                    future.set_exception(error)

    def cancel(self, key: str) -> bool:
        """Stop only the selected slot and retain its unacknowledged receipt.

        Args:
            key: Delivery to stop, including one not yet launched.

        Returns:
            Whether an existing delivery was stopped or already cancelled.

        Raises:
            ProcessOwnershipError: A stopped generation cannot be confirmed clean.
        """
        with self._lock:
            future = self._futures.get(key)
            if future is None:
                return False
            slot = self._slots.get(key)
            if not future.done():
                self._cancelling.add(key)
            if slot is None and not future.done():
                self._queue.get(id=self._receipts[key], block=False, raw=True)
        if slot is not None:
            self._stop_slot(slot)
        with self._lock:
            return Future.cancel(future)

    def ack(self, key: str) -> None:
        """Acknowledge a durable caller outcome and release the retained slot.

        Capture worker_identity before acknowledgement. Confirmed-clean process
        receipts are pruned only after the queue acknowledgement commits.

        Args:
            key: Completed, cancelled or never-launched recovered delivery.

        Raises:
            RuntimeError: Active work must be stopped before acknowledgement.
            ProcessOwnershipError: Cleanup remains unconfirmed.
        """
        with self._lock:
            if key not in self._entries:
                self._prune_owners(key)
                return
            slot = self._slots.get(key)
            future = self._futures[key]
            if slot is not None:
                if slot.error is not None:
                    raise ProcessOwnershipError(f"Slot cleanup remains unconfirmed: {key}") from slot.error
                if not future.done():
                    raise RuntimeError("Cancel active work before acknowledging its delivery")
            self._queue.ack(id=self._receipts[key])
            Future.cancel(future)
            self._slots.pop(key, None)
            self._entries.pop(key)
            self._receipts.pop(key)
            self._futures.pop(key)
            self._cancelling.discard(key)
            self._prune_owners(key)

    def _prune_owners(self, key: str) -> None:
        for directory in tuple(self._owners.get(key, ())):
            try:
                shutil.rmtree(directory)
            except FileNotFoundError:
                pass
            except OSError:
                # A confirmed, acknowledged generation is safe to retry at startup.
                continue
            self._owners[key].discard(directory)
        if not self._owners.get(key):
            self._owners.pop(key, None)

    def discard(self, key: str) -> None:
        """Discard a stale recovered delivery after the caller's durable decision.

        Args:
            key: Delivery no longer belonging to the current journal generation.
        """
        self.ack(key)

    def entries(self) -> dict[str, dict]:
        """Return independent JSON packets for every retained unacknowledged key.

        Returns:
            Delivery keys mapped to target, args, kwargs and key packets.
        """
        with self._lock:
            return json.loads(json.dumps(self._entries))

    def future(self, key: str) -> Future | None:
        """Look up a local Future without implicitly admitting or executing work.

        Args:
            key: Delivery identity.

        Returns:
            Its Future, or None after acknowledgement or for an unknown key.
        """
        with self._lock:
            return self._futures.get(key)

    def worker_identity(self, key: str) -> dict | None:
        """Read a slot's PID/create-time receipt using public ownership records.

        Args:
            key: Retained delivery identity.

        Returns:
            The worker record, or None before launch or worker initialization.
        """
        with self._lock:
            slot = self._slots.get(key)
            if slot:
                return worker_identity(slot.owner)
            identities = [worker_identity(directory) for directory in self._owners.get(key, ())]
            return max((identity for identity in identities if identity), key=lambda item: item["create_time"], default=None)

    def close(self) -> None:
        """Stop pools and reconcile descendants while preserving unacked deliveries."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            slots = list(self._slots.values())
        failures = []
        for slot in slots:
            try:
                self._stop_slot(slot)
            except BaseException as error:
                failures.append(error)
        self._completions.shutdown(wait=True)
        with self._lock:
            for future in self._futures.values():
                Future.cancel(future)
            self._queue = None
            self._lease.__exit__(None, None, None)
        if failures:
            raise ProcessOwnershipError("One or more slots could not be reconciled") from failures[0]
