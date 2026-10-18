# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Live preview and unqualified marker trials using the existing Keyence API."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module, util
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Protocol

import cv2

from rdt_monitor.domain import MarkerState, Observation, SyncResult, SyncState
from rdt_monitor.replay import Image, ReplayRecipe
from rdt_monitor.synchronization import CycleSynchronizer

logger = logging.getLogger(__name__)


class SynchronizationFault(RuntimeError):
    """Latched marker-processing failure, independent of camera capture health."""


class CameraDevice(Protocol):
    """Single-worker camera lifecycle supplied by the existing API."""

    def open(self) -> bool:
        """Open and verify a readable frame."""
        ...

    def read(self) -> Image:
        """Return a BGR image or raise a camera-read error."""
        ...

    def get_properties(self) -> Mapping[str, int | float | None]:
        """Return generic index, selected backend, dimensions, and reported FPS."""
        ...

    def close(self) -> None:
        """Release the device in its owning worker."""
        ...


def create_camera(index: int) -> CameraDevice:
    """Load the shared Keyence API from the installed wheel or repository.

    Args:
        index (int): Generic OpenCV camera index, not a verified model identity.

    Returns:
        CameraDevice: Instance of the existing KeyenceNRCM01Camera implementation.

    Raises:
        OSError: If the API cannot be located in the installation or checkout.
    """
    module_name = "rdt_monitor.keyence_nr_cm01_api"
    if util.find_spec(module_name) is not None:
        module = import_module(module_name)
    else:
        source = Path(__file__).resolve().parents[4] / "tools" / "keyence_nr_cm01_api.py"
        spec = util.spec_from_file_location(module_name, source)
        if not source.is_file() or spec is None or spec.loader is None:
            raise OSError("Existing Keyence API is missing; reinstall the monitor package")
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.KeyenceNRCM01Camera(index)


@dataclass(frozen=True)
class CameraFrame:
    """Owned live pixels with host-read completion time, not a sensor timestamp.

    Args:
        frame_id (int): Sequential frame identity within this connection.
        received_at (float): Absolute host monotonic read-completion time.
        image (Image): Read-only BGR pixel buffer owned by this record.
    """

    frame_id: int
    received_at: float
    image: Image


@dataclass(frozen=True)
class CameraStatus:
    """Connection state independent of specimen and synchronization state.

    Args:
        state (str): Opening, Streaming, Stopped, or Fault.
        reason (str): Operator-readable condition or latched failure.
        index (int): Generic OpenCV device index.
        backend (int | None): Selected OpenCV backend when known.
        width (int): Last actual frame width.
        height (int): Last actual frame height.
        reported_fps (float): Driver-reported value; zero means unavailable.
        last_received_at (float | None): Last host-read completion time.
    """

    state: str
    reason: str
    index: int
    backend: int | None = None
    width: int = 0
    height: int = 0
    reported_fps: float = 0
    last_received_at: float | None = None


class MarkerTrial:
    """Unqualified ordered marker trial, independent of preview replacement.

    Args:
        recipe (ReplayRecipe): Explicit experimental regions and timing limits.
        event_capacity (int): Maximum pending cycle/position events.

    Raises:
        ValueError: If event capacity is not a positive integer.
    """

    def __init__(self, recipe: ReplayRecipe, event_capacity: int = 128) -> None:
        if isinstance(event_capacity, bool) or not isinstance(event_capacity, int) or event_capacity < 1:
            raise ValueError("Event capacity must be a positive integer")
        self._recipe = recipe
        self.events: Queue[SyncResult] = Queue(maxsize=event_capacity)
        self._synchronizer = CycleSynchronizer(recipe.synchronization)
        self._result: SyncResult | None = None
        self._shape: tuple[int, ...] | None = None
        self._lock = Lock()
        self._cycle_capture: Callable[[Image, SyncResult], None] | None = None
        self._stop_requested = Event()
        self._stopped = Event()

    @property
    def stopped(self) -> bool:
        """Return whether Stop has barred new observations and callbacks have finished."""
        return self._stopped.is_set()

    def request_stop(self) -> None:
        """Stop admission without waiting on recognition or an in-flight callback."""
        self._stop_requested.set()
        if self._lock.acquire(blocking=False):
            try:
                self._cycle_capture = None
                self._stopped.set()
            finally:
                self._lock.release()

    def set_cycle_capture(self, capture: Callable[[Image, SyncResult], None] | None) -> None:
        """Attach a nonblocking completed-cycle snapshot consumer under the trial lock.

        Args:
            capture (Callable[[Image, SyncResult], None] | None): Consumer of exact source pixels and result.
        """
        with self._lock:
            if self._stop_requested.is_set() and capture is not None:
                raise RuntimeError("Cannot attach capture to a stopped trial")
            self._cycle_capture = capture

    @property
    def recipe(self) -> ReplayRecipe:
        """Return the immutable experimental settings locked for this trial."""
        return self._recipe

    @property
    def result(self) -> SyncResult | None:
        """Return the last immutable observation without waiting on capture callbacks."""
        return self._result

    def observe(self, frame: CameraFrame) -> None:
        """Process one captured frame before any preview replacement.

        Args:
            frame (CameraFrame): Next worker-owned frame with host completion time.

        Raises:
            SynchronizationFault: If recognition, coverage, geometry, or event capacity is lost.
        """
        with self._lock:
            if self._stop_requested.is_set():
                self._stopped.set()
                return
            try:
                self._observe_locked(frame)
            finally:
                if self._stop_requested.is_set():
                    self._cycle_capture = None
                    self._stopped.set()

    def _observe_locked(self, frame: CameraFrame) -> None:
        if self._result is not None and self._result.state == SyncState.LOST:
            raise SynchronizationFault(self._result.reason)
        if self._shape is None:
            self._shape = frame.image.shape
        elif frame.image.shape != self._shape:
            observation = Observation(frame.frame_id, frame.received_at, MarkerState.AMBIGUOUS, False)
            self._result = self._synchronizer.invalidate(
                observation, "Camera geometry changed; confirm new regions and restart"
            )
            raise SynchronizationFault(self._result.reason)
        try:
            position = self.recipe.classify(frame.image, self.recipe.position)
            observation = Observation(
                frame.frame_id,
                frame.received_at,
                self.recipe.classify(frame.image, self.recipe.marker),
                position == MarkerState.PRESENT,
                position_marker=position,
            )
        except (ValueError, cv2.error) as error:
            observation = Observation(frame.frame_id, frame.received_at, MarkerState.AMBIGUOUS, False)
            self._result = self._synchronizer.invalidate(observation, f"Marker recognition failed: {error}")
            raise SynchronizationFault(self._result.reason) from error
        result = self._synchronizer.observe(observation)
        if result.cycle_started or result.inspection_ready:
            try:
                self.events.put_nowait(result)
            except Full:
                result = self._synchronizer.invalidate(observation, "Marker event backlog overflow; coverage unknown")
        self._result = result
        if result.state == SyncState.LOST:
            raise SynchronizationFault(result.reason)
        if result.inspection_ready and self._cycle_capture is not None:
            self._cycle_capture(frame.image, result)


class CameraPreview:
    """Single-owner capture worker with bounded preview and latched timeout faults.

    Marker recognition is enabled only by an explicit experimental trial command.
    No inference, evidence recording, or automatic reconnect is performed.
    Open/read/close and trial observations all run in the owning worker.

    Args:
        index (int): Operator-selected generic device index.
        factory (Callable[[int], CameraDevice]): Existing API factory or test double.
        clock (Callable[[], float]): Monotonic host clock.
        stale_seconds (float): Maximum host interval without a completed read.
        opening_seconds (float): Maximum opening time before status becomes a fault.

    Raises:
        ValueError: If the index or timeout limits are invalid.
    """

    def __init__(
        self,
        index: int,
        factory: Callable[[int], CameraDevice] = create_camera,
        *,
        clock: Callable[[], float] = monotonic,
        stale_seconds: float = 2,
        opening_seconds: float = 10,
    ) -> None:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("Camera index must be a nonnegative integer")
        if not all(isfinite(value) and value > 0 for value in (stale_seconds, opening_seconds)):
            raise ValueError("Camera timeouts must be finite and positive")
        self.index = index
        self.frames: Queue[CameraFrame] = Queue(maxsize=1)
        self._factory = factory
        self._clock = clock
        self._stale_seconds = stale_seconds
        self._opening_seconds = opening_seconds
        self._started_at = clock()
        self._stop = Event()
        self._lock = Lock()
        self._status = CameraStatus("Stopped", "Camera preview stopped", index)
        self._trial: MarkerTrial | None = None
        self._stopping_trial: MarkerTrial | None = None
        self._thread = Thread(target=self._run, name="rdt-camera", daemon=True)

    @property
    def trial(self) -> MarkerTrial | None:
        """Return the explicitly started trial, retained after faults or disconnect."""
        with self._lock:
            return self._trial

    def start_trial(self, recipe: ReplayRecipe, capture: Callable[[Image, SyncResult], None] | None = None) -> None:
        """Start synchronization explicitly, replacing only an already-faulted session.

        Args:
            recipe (ReplayRecipe): Confirmed unqualified marker/timing settings.
            capture (Callable[[Image, SyncResult], None] | None): Consumer attached before the first observation.

        Raises:
            RuntimeError: If capture is not healthy or synchronization is already active.
        """
        status = self.status
        with self._lock:
            if status.state != "Streaming" or self._stop.is_set():
                raise RuntimeError("A healthy streaming camera is required; reconnect after camera faults")
            if self._stopping_trial is not None and not self._stopping_trial.stopped:
                raise RuntimeError("Wait for the previous synchronization trial to finish stopping")
            previous_result = self._trial.result if self._trial is not None else None
            if self._trial is not None and (previous_result is None or previous_result.state != SyncState.LOST):
                raise RuntimeError(
                    "Synchronization is already active; stop or reconnect before starting another session"
                )
            self._trial = MarkerTrial(recipe)
            self._trial.set_cycle_capture(capture)

    def stop_trial(self) -> MarkerTrial | None:
        """Request synchronization Stop without waiting or stopping camera capture.

        Returns:
            MarkerTrial | None: Detached trial; check its ``stopped`` barrier before
                declaring callbacks finished or discarding pending events.
        """
        with self._lock:
            trial = self._trial
            self._trial = None
            if trial is not None:
                self._stopping_trial = trial
                trial.request_stop()
            return trial

    @property
    def alive(self) -> bool:
        """Return whether the camera worker still owns or is releasing the device."""
        return self._thread.is_alive()

    @property
    def status(self) -> CameraStatus:
        """Return health, detecting stalled open/read operations without UI blocking."""
        with self._lock:
            now = self._clock()
            if self._status.state == "Opening" and now - self._started_at > self._opening_seconds:
                self._fault_locked("Camera opening timed out; driver may be blocked")
            elif self._status.state == "Streaming" and self._status.last_received_at is not None:
                if now - self._status.last_received_at > self._stale_seconds:
                    self._fault_locked("Camera frame stale; coverage unknown; reconnect explicitly")
            return self._status

    def start(self) -> None:
        """Start a single-use connection after an explicit operator command."""
        if self._thread.ident is not None:
            raise RuntimeError("Create a new connection to reconnect the camera")
        self._started_at = self._clock()
        with self._lock:
            self._status = CameraStatus("Opening", "Opening generic camera index", self.index)
        self._thread.start()

    def stop(self) -> None:
        """Request release without touching the worker-owned device from the UI."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait outside the UI thread for release.

        Args:
            timeout (float | None): Maximum wait in seconds.
        """
        self._thread.join(timeout)

    def _fault_locked(self, reason: str) -> None:
        status = self._status
        self._status = CameraStatus(
            "Fault",
            reason,
            self.index,
            status.backend,
            status.width,
            status.height,
            status.reported_fps,
            status.last_received_at,
        )
        self._stop.set()

    def _fault(self, reason: str) -> None:
        with self._lock:
            self._fault_locked(reason)

    def _publish(self, frame: CameraFrame, backend: int | None, fps: float) -> None:
        with self._lock:
            if self._status.state == "Fault" or self._stop.is_set():
                return
            height, width = frame.image.shape[:2]
            self._status = CameraStatus(
                "Streaming",
                "Live preview only; no inspection or recording",
                self.index,
                backend,
                width,
                height,
                fps,
                frame.received_at,
            )
            try:
                self.frames.get_nowait()
            except Empty:
                pass
            self.frames.put_nowait(frame)

    def _run(self) -> None:
        camera: CameraDevice | None = None
        try:
            camera = self._factory(self.index)
            if not camera.open():
                raise OSError("Camera cannot open; check USB, driver, index, and other camera applications")
            properties = camera.get_properties()
            backend_value = properties.get("backend")
            backend = int(backend_value) if backend_value is not None else None
            fps = float(properties.get("fps") or 0)
            frame_id = 0
            while not self._stop.is_set():
                image = camera.read()
                received_at = self._clock()
                status = self.status
                if status.state == "Fault" or self._stop.is_set():
                    break
                if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
                    raise ValueError("Camera returned an invalid BGR image")
                owned = image.copy()
                owned.flags.writeable = False
                frame = CameraFrame(frame_id, received_at, owned)
                trial = self.trial
                if trial is not None:
                    result = trial.result
                    if result is None or result.state != SyncState.LOST:
                        try:
                            trial.observe(frame)
                        except SynchronizationFault as error:
                            logger.warning("Synchronization stopped; camera preview continues: %s", error)
                self._publish(frame, backend, fps if isfinite(fps) else 0)
                frame_id += 1
        except (OSError, RuntimeError, ValueError, TypeError, cv2.error) as error:
            self._fault(f"Camera unavailable: {error}")
        finally:
            if camera is not None:
                try:
                    camera.close()
                except (OSError, RuntimeError, cv2.error) as error:
                    self._fault(f"Camera release failed: {error}")
            with self._lock:
                if self._status.state != "Fault":
                    self._status = CameraStatus("Stopped", "Camera disconnected; last frame is not live", self.index)
