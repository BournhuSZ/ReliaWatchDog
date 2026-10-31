# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Bounded replay transport and visual-marker experiments; never defect assessment."""

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from rdt_monitor.domain import MarkerState, Observation, SyncConfig, SyncResult, SyncState
from rdt_monitor.synchronization import CycleSynchronizer

Image = NDArray[np.uint8]


@dataclass(frozen=True)
class Region:
    """Normalized image rectangle used only for unqualified marker experiments.

    Args:
        left (float): Left coordinate relative to image width.
        top (float): Top coordinate relative to image height.
        width (float): Fraction of image width.
        height (float): Fraction of image height.

    Raises:
        ValueError: If the rectangle is empty, nonfinite, or outside the image.
    """

    left: float
    top: float
    width: float
    height: float

    def __post_init__(self) -> None:
        values = (self.left, self.top, self.width, self.height)
        if not all(isfinite(value) for value in values):
            raise ValueError("Region coordinates must be finite")
        if min(self.left, self.top) < 0 or min(self.width, self.height) <= 0:
            raise ValueError("Region must have positive size and nonnegative origin")
        if self.left + self.width > 1.000001 or self.top + self.height > 1.000001:
            raise ValueError("Region must be inside the image")

    def pixels(self, image: Image) -> tuple[int, int, int, int]:
        """Return nonempty pixel bounds for an image.

        Args:
            image (Image): BGR source image.

        Returns:
            tuple[int, int, int, int]: Left, top, right, and bottom exclusive bounds.

        Raises:
            ValueError: If the selected region is smaller than a source pixel.
        """
        height, width = image.shape[:2]
        left, top = int(self.left * width), int(self.top * height)
        right = min(width, int((self.left + self.width) * width))
        bottom = min(height, int((self.top + self.height) * height))
        if right <= left or bottom <= top:
            raise ValueError("Selected region is smaller than one source pixel")
        return left, top, right, bottom


@dataclass(frozen=True)
class MarkerAppearance:
    """Immutable normalized grayscale reference captured from a visible mark.

    Args:
        pixels (tuple[int, ...]): Row-major pixels of a 32x32 reference patch.

    Raises:
        ValueError: If pixels are invalid or the reference lacks usable contrast.
    """

    pixels: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.pixels) != 1024 or any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255 for value in self.pixels
        ):
            raise ValueError("Mark reference must contain 1024 grayscale pixels")
        if float(np.std(self.pixels)) < 2:
            raise ValueError("Mark reference has insufficient contrast; select the visible mark more tightly")

    @classmethod
    def capture(cls, image: Image, region: Region) -> "MarkerAppearance":
        """Capture the visible appearance inside a source-aligned region.

        Args:
            image (Image): Frozen BGR camera image.
            region (Region): Region containing the visible mark and its immediate surround.

        Returns:
            MarkerAppearance: Immutable reference independent of the source buffer.

        Raises:
            ValueError: If the region is empty or its reference has insufficient contrast.
        """
        left, top, right, bottom = region.pixels(image)
        gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        patch = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        return cls(tuple(int(value) for value in patch.flat))

    def score(self, image: Image, region: Region) -> float:
        """Match a mark within its region with bounded translation and pose tolerance.

        A 24x24 reference core locates candidates in the 32x32 observation;
        correlation is then validated over the full overlapping reference.
        Translation is limited to four normalized pixels, rotations to +/-15
        degrees, and scales to 0.9, 1.0, and 1.1. Observations below 40 percent
        of reference contrast are rejected. These limits are unqualified.

        Args:
            image (Image): Current BGR source image.
            region (Region): Region in the same camera geometry as the reference.

        Returns:
            float: Correlation in [0, 1]; not a defect score or probability.

        Raises:
            ValueError: If the selected region has no source pixels.
        """
        left, top, right, bottom = region.pixels(image)
        gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        observed = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        reference = np.asarray(self.pixels, dtype=np.uint8).reshape(32, 32)
        if float(observed.std()) < max(1, float(reference.std()) * 0.4):
            return 0
        score = float(cv2.matchTemplate(observed, reference, cv2.TM_CCOEFF_NORMED)[0, 0])
        if isfinite(score) and score >= 0.8:
            return min(1, score)
        for angle in (-15, 0, 15):
            for scale in (0.9, 1.0, 1.1):
                transform = cv2.getRotationMatrix2D((15.5, 15.5), angle, scale)
                posed = cv2.warpAffine(reference, transform, (32, 32), borderMode=cv2.BORDER_REFLECT_101)
                core = posed[4:28, 4:28]
                if float(core.std()) < 2:
                    continue
                matches = cv2.matchTemplate(observed, core, cv2.TM_CCOEFF_NORMED)
                location = cv2.minMaxLoc(matches)[3]
                shift_x, shift_y = location[0] - 4, location[1] - 4
                observed_overlap = observed[
                    max(0, shift_y) : min(32, 32 + shift_y), max(0, shift_x) : min(32, 32 + shift_x)
                ]
                reference_overlap = posed[
                    max(0, -shift_y) : min(32, 32 - shift_y), max(0, -shift_x) : min(32, 32 - shift_x)
                ]
                candidate = float(cv2.matchTemplate(observed_overlap, reference_overlap, cv2.TM_CCOEFF_NORMED)[0, 0])
                if isfinite(candidate):
                    score = max(score, candidate)
        return max(0, min(1, score)) if isfinite(score) else 0


@dataclass(frozen=True)
class ReplayRecipe:
    """Unqualified bright-marker experiment settings, not a production recipe.

    Args:
        synchronization (SyncConfig): Explicit cycle timing/debounce limits.
        marker (Region): Cycle-start marker region.
        position (Region): Independent inspection-position marker region.
        brightness (int): Minimum grayscale value for a bright pixel.
        absent_fraction (float): Maximum bright fraction to classify absent.
        present_fraction (float): Minimum bright fraction to classify present.
        marker_appearance (MarkerAppearance | None): Captured start-mark reference;
            None uses diagnostic brightness mode.
        position_appearance (MarkerAppearance | None): Captured inspection-position mark reference.

    Raises:
        ValueError: If brightness or bright-fraction limits are invalid.
    """

    synchronization: SyncConfig
    marker: Region
    position: Region
    brightness: int = 180
    absent_fraction: float = 0.2
    present_fraction: float = 0.8
    marker_appearance: MarkerAppearance | None = None
    position_appearance: MarkerAppearance | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.brightness <= 255:
            raise ValueError("Brightness must be in [0, 255]")
        if not 0 <= self.absent_fraction < self.present_fraction <= 1:
            raise ValueError("Bright fractions must satisfy 0 <= absent < present <= 1")
        if (self.marker_appearance is None) != (self.position_appearance is None):
            raise ValueError("Capture both start and position mark references")

    def classify(self, image: Image, region: Region) -> MarkerState:
        """Classify captured appearance or legacy brightness; uncertainty is not absence.

        Args:
            image (Image): BGR source frame.
            region (Region): Rectangle to classify.

        Returns:
            MarkerState: Present, absent, or ambiguous recognition result.
        """
        appearance = self.marker_appearance if region == self.marker else self.position_appearance
        if appearance is not None:
            score = appearance.score(image, region)
            if score >= 0.8:
                return MarkerState.PRESENT
            if score <= 0.4:
                return MarkerState.ABSENT
            return MarkerState.AMBIGUOUS
        left, top, right, bottom = region.pixels(image)
        gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        fraction = float(np.count_nonzero(gray >= self.brightness)) / gray.size
        if fraction >= self.present_fraction:
            return MarkerState.PRESENT
        if fraction <= self.absent_fraction:
            return MarkerState.ABSENT
        return MarkerState.AMBIGUOUS


def fixture_recipe() -> ReplayRecipe:
    """Return explicit synthetic-fixture settings, never production defaults.

    Returns:
        ReplayRecipe: Unqualified replay settings for the supplied fixture.
    """
    return ReplayRecipe(SyncConfig(2, 0.2, 4), Region(0.05, 0.1, 0.1, 0.1), Region(0.8, 0.1, 0.1, 0.1))


@dataclass(frozen=True)
class SourceFrame:
    """Owned source pixels with sequential frame identity and source time.

    Args:
        frame_id (int): Zero-based frame identity.
        timestamp (float): Source time in seconds, not a host acquisition timestamp.
        image (Image): BGR pixel buffer whose ownership transfers to the pipeline.
    """

    frame_id: int
    timestamp: float
    image: Image


class FrameSource(Protocol):
    """Minimal sequential replay-source contract."""

    def read(self) -> SourceFrame | None:
        """Return the next owned frame, or None at the source boundary."""
        ...

    def close(self) -> None:
        """Release the source resources."""
        ...


class VideoSource:
    """Sequential local-video decoder; frame-index time is unqualified metadata.

    Args:
        path (str): Selected local recording path.

    Raises:
        OSError: If the recording cannot open or has invalid frame-rate metadata.
    """

    def __init__(self, path: str) -> None:
        if not Path(path).is_file():
            raise OSError("Select an existing local recording file")
        self._capture = cv2.VideoCapture(path)
        self._fps = self._capture.get(cv2.CAP_PROP_FPS)
        self._count = self._capture.get(cv2.CAP_PROP_FRAME_COUNT)
        self._index = 0
        if not self._capture.isOpened() or not isfinite(self._fps) or self._fps <= 0:
            self.close()
            raise OSError("Recording cannot open or has invalid FPS metadata")

    def read(self) -> SourceFrame | None:
        """Decode in order; report an early advertised-frame boundary as a fault.

        Returns:
            SourceFrame | None: Next frame, or the EOF/decode boundary.

        Raises:
            OSError: If decoding stops before the advertised frame count.
        """
        success, image = self._capture.read()
        if not success or image is None:
            if isfinite(self._count) and self._count > 0 and self._index < int(self._count):
                raise OSError("Decode ended before the advertised frame count; coverage is unknown")
            return None
        frame = SourceFrame(self._index, self._index / self._fps, image)
        self._index += 1
        return frame

    def close(self) -> None:
        """Release the OpenCV decoder."""
        self._capture.release()


class SyntheticSource:
    """Finite, explicitly synthetic marker fixture, not an industrial dataset.

    Args:
        frame_count (int): Number of frames to generate at 30 source FPS.
    """

    def __init__(self, frame_count: int = 360) -> None:
        self._index = 0
        self._frame_count = frame_count

    def read(self) -> SourceFrame | None:
        """Return a labeled synthetic frame with independent start/position markers.

        Returns:
            SourceFrame | None: Fixture frame, or None when complete.
        """
        if self._index >= self._frame_count:
            return None
        image = np.full((540, 960, 3), (21, 17, 15), dtype=np.uint8)
        phase = self._index % 90
        cv2.rectangle(image, (180, 175), (780, 380), (55, 59, 64), -1)
        cv2.rectangle(image, (220, 218), (740, 338), (105, 130, 110), -1)
        for offset in range(8):
            row = 230 + offset * 13
            cv2.line(image, (240, row), (720, row), (175, 190, 175), 2)
        for horizontal in (190, 770):
            cv2.circle(image, (horizontal, 190), 5, (145, 145, 145), -1)
            cv2.circle(image, (horizontal, 365), 5, (145, 145, 145), -1)
        cv2.rectangle(image, (48, 54), (143, 107), (245, 245, 245) if phase < 15 or phase >= 45 else (0, 0, 0), -1)
        cv2.rectangle(image, (768, 54), (863, 107), (245, 245, 245) if phase < 15 or phase >= 60 else (0, 0, 0), -1)
        cv2.putText(
            image,
            "SYNTHETIC REPLAY / NOT CAMERA FOOTAGE",
            (35, 475),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (190, 195, 200),
            1,
            cv2.LINE_AA,
        )
        frame = SourceFrame(self._index, self._index / 30, image)
        self._index += 1
        return frame

    def close(self) -> None:
        """Close the in-memory fixture (no external resources)."""


@dataclass(frozen=True)
class Preview:
    """Source-aligned preview and synchronization state, never a defect result.

    Args:
        frame (SourceFrame): Immutable-by-ownership source frame.
        result (SyncResult): Synchronization result for that exact frame.
    """

    frame: SourceFrame
    result: SyncResult


@dataclass(frozen=True)
class ReplayStatus:
    """Thread-safe lifecycle snapshot with durable-in-memory terminal reason.

    Args:
        state (str): Replay lifecycle state.
        reason (str): End/fault detail, independent of preview drops.
    """

    state: str
    reason: str


class ReplayPipeline:
    """Sequential synchronization worker with independently bounded UI transport.

    This development pipeline performs no inference and saves no evidence. Event
    overflow latches a fault; preview replacement cannot lose synchronization work.

    Args:
        factory (Callable[[], FrameSource]): Source factory executed in the worker.
        recipe (ReplayRecipe): Frozen unqualified replay settings.
        event_capacity (int): Maximum pending cycle/position events.
        realtime (bool): Pace playback against source timestamps when True.
    """

    def __init__(
        self,
        factory: Callable[[], FrameSource],
        recipe: ReplayRecipe,
        event_capacity: int = 128,
        *,
        realtime: bool = True,
    ) -> None:
        if event_capacity < 1:
            raise ValueError("Event capacity must be positive")
        self.factory = factory
        self.recipe = recipe
        self.events: Queue[SyncResult] = Queue(maxsize=event_capacity)
        self.previews: Queue[Preview] = Queue(maxsize=1)
        self._stop = Event()
        self._lock = Lock()
        self._status = ReplayStatus("Stopped", "Unqualified replay; detector unavailable; evidence not recorded")
        self._thread = Thread(target=self._run, name="rdt-replay", daemon=True)
        self._realtime = realtime

    @property
    def status(self) -> ReplayStatus:
        """Return the latest lifecycle snapshot independently of bounded events."""
        with self._lock:
            return self._status

    @property
    def alive(self) -> bool:
        """Return whether the worker is still running or releasing its source."""
        return self._thread.is_alive()

    def start(self) -> None:
        """Start this single-use replay run; callers create a new instance to restart."""
        if self._thread.ident is not None:
            raise RuntimeError("Replay runs are single-use; create a new operator-confirmed run")
        self._set_status("Opening", "Opening replay source")
        self._thread.start()

    def stop(self) -> None:
        """Request stop without blocking the UI on decoder shutdown."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for a worker in tests or a non-UI shutdown path.

        Args:
            timeout (float | None): Maximum wait in seconds, or no limit.
        """
        self._thread.join(timeout)

    def _set_status(self, state: str, reason: str) -> None:
        with self._lock:
            self._status = ReplayStatus(state, reason)

    def _publish_preview(self, preview: Preview) -> None:
        try:
            self.previews.get_nowait()
        except Empty:
            pass
        self.previews.put_nowait(preview)

    def _run(self) -> None:
        source: FrameSource | None = None
        tracker = CycleSynchronizer(self.recipe.synchronization)
        started = monotonic()
        first_time: float | None = None
        try:
            source = self.factory()
            self._set_status("Replaying", "Unqualified source-time replay; no defect assessment")
            while not self._stop.is_set():
                frame = source.read()
                if frame is None:
                    self._set_status("Ended", "Replay boundary reached; recording completeness is unverified")
                    break
                if first_time is None:
                    first_time = frame.timestamp
                    started = monotonic()
                if self._realtime and isfinite(frame.timestamp):
                    if self._stop.wait(max(0, frame.timestamp - first_time - (monotonic() - started))):
                        break
                marker = self.recipe.classify(frame.image, self.recipe.marker)
                position = self.recipe.classify(frame.image, self.recipe.position)
                observation = Observation(
                    frame.frame_id, frame.timestamp, marker, position == MarkerState.PRESENT, position_marker=position
                )
                result = tracker.observe(observation)
                frame.image.flags.writeable = False
                self._publish_preview(Preview(frame, result))
                if result.state == SyncState.LOST:
                    self._set_status("Fault", result.reason)
                    break
                if result.cycle_started or result.inspection_ready:
                    try:
                        self.events.put_nowait(result)
                    except Full:
                        result = tracker.invalidate(observation, "Event buffer overflow; inspection coverage unknown")
                        self._publish_preview(Preview(frame, result))
                        self._set_status("Fault", result.reason)
                        break
            else:
                self._set_status("Stopped", "Replay stopped by operator; no evidence saved")
            if self._stop.is_set() and self.status.state != "Fault":
                self._set_status("Stopped", "Replay stopped by operator; no evidence saved")
        except (OSError, ValueError, RuntimeError, cv2.error) as error:
            self._set_status("Fault", f"Replay unavailable: {error}")
        finally:
            if source is not None:
                try:
                    source.close()
                except (OSError, RuntimeError, cv2.error) as error:
                    self._set_status("Fault", f"Replay source release failed: {error}")
