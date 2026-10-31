# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Automatic or operator-declared cycle collection, never automatic normal labels or training."""

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread
from typing import Literal
from uuid import uuid4

import cv2
import numpy as np

from rdt_monitor.domain import SyncResult, SyncState
from rdt_monitor.replay import Image, Region


def local_file(root: Path, name: str) -> Path:
    """Confine generated or loaded leaf filenames to their selected session directory.

    Args:
        root (Path): Explicit session directory.
        name (str): Plain filename, never a path or specimen identity.

    Returns:
        Path: Confined filename.

    Raises:
        ValueError: If the name or destination escapes the session or uses a symlink.
    """
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", name):
        raise ValueError("Collection filename must be a confined plain basename")
    candidate = root / name
    if root.is_symlink() or candidate.is_symlink() or candidate.resolve().parent != root.resolve():
        raise ValueError("Collection path escapes its session directory")
    return candidate


def atomic_bytes(path: Path, content: bytes) -> None:
    """Atomically replace a confined session file, preserving it on replacement failure.

    Args:
        path (Path): Validated output file.
        content (bytes): Complete encoded content.
    """
    path = local_file(path.parent, path.name)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class CollectionSettings:
    """Locked collection inputs, separate from normal confirmation.

    Args:
        specimen_id (str): Specimen metadata; never used in filenames.
        run_id (str): Synchronization run or generated manual-collection identity.
        recipe_version (str): Locked synchronization revision or manual capture protocol.
        region (Region): Independent specimen training ROI.
        width (int): Required source width.
        height (int): Required source height.
        target (int): Number of distinct cycle snapshots, from 20 to 30.
        cycle_mode (Literal["auto", "manual"]): Marker detection or operator-declared cycles.
    """

    specimen_id: str
    run_id: str
    recipe_version: str
    region: Region
    width: int
    height: int
    target: int = 30
    cycle_mode: Literal["auto", "manual"] = "auto"

    def __post_init__(self) -> None:
        if self.cycle_mode not in {"auto", "manual"}:
            raise ValueError("Collection cycle mode must be auto or manual")
        for value in (self.specimen_id, self.run_id, self.recipe_version):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError("Collection identity must contain 1-128 characters")
        for value in (self.width, self.height, self.target):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("Collection dimensions and count must be integers")
        if (
            not 20 <= self.target <= 30
            or not 1 <= min(self.width, self.height) <= max(self.width, self.height) <= 65535
        ):
            raise ValueError("Collect 20-30 images with valid source dimensions")
        if not isinstance(self.region, Region):
            raise ValueError("Collection requires a valid specimen ROI")


@dataclass(frozen=True)
class SampleRecord:
    """Source and crop identity for an unreviewed cycle snapshot.

    Args:
        cycle (int): Detected cycle number in auto mode, operator-declared number in manual mode.
        frame_id (int): Exact source frame number.
        received_at (float): Host monotonic read-completion time, not sensor time.
        captured_utc (str): Host UTC collection time.
        source_file (str): Source PNG basename.
        crop_file (str): Specimen crop PNG basename.
        source_sha256 (str): Source PNG integrity digest.
        crop_sha256 (str): Crop PNG integrity digest.
    """

    cycle: int
    frame_id: int
    received_at: float
    captured_utc: str
    source_file: str
    crop_file: str
    source_sha256: str
    crop_sha256: str

    def __post_init__(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (self.cycle, self.frame_id)):
            raise ValueError("Snapshot cycle/frame identity must be integers")
        if self.cycle < 1 or self.frame_id < 0 or not isinstance(self.received_at, (int, float)):
            raise ValueError("Invalid snapshot identity or time")
        if isinstance(self.received_at, bool) or not isfinite(self.received_at):
            raise ValueError("Snapshot time must be finite")
        if not isinstance(self.captured_utc, str) or len(self.captured_utc) > 64:
            raise ValueError("Invalid snapshot UTC timestamp")
        for digest in (self.source_sha256, self.crop_sha256):
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid image integrity digest")


@dataclass(frozen=True)
class CollectionDocument:
    """Persistent candidate set; completion never implies normal or eligible monitoring.

    Args:
        session_id (str): Generated session identity.
        settings (CollectionSettings): Locked capture inputs.
        records (tuple[SampleRecord, ...]): Successfully saved source/crop pairs.
        state (str): Collecting, Awaiting confirmation, Incomplete, or Confirmed normal.
        reason (str): Persistent collection failure or stop reason.
        confirmed_at (str | None): Explicit user confirmation time, not authentication.
        schema_version (int): Supported metadata schema.
    """

    session_id: str
    settings: CollectionSettings
    records: tuple[SampleRecord, ...] = ()
    state: str = "Collecting"
    reason: str = ""
    confirmed_at: str | None = None
    schema_version: int = 1


def save_document(folder: Path, document: CollectionDocument) -> None:
    """Persist complete candidate metadata atomically.

    Args:
        folder (Path): Selected/generated session directory.
        document (CollectionDocument): Current collection state.
    """
    encoded = json.dumps(asdict(document), indent=2, allow_nan=False).encode("utf-8")
    atomic_bytes(local_file(folder, "collection.json"), encoded)


def metadata_digest(path: Path) -> str:
    """Hash bounded collection metadata for explicit review-to-training integrity.

    Args:
        path (Path): Selected collection metadata.

    Returns:
        str: SHA-256 digest of the exact file bytes.

    Raises:
        ValueError: If the metadata exceeds 1 MiB.
    """
    return hashlib.sha256(_metadata_bytes(path)).hexdigest()


def _metadata_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        encoded = stream.read(1024 * 1024 + 1)
    if len(encoded) > 1024 * 1024:
        raise ValueError("Collection metadata exceeds 1 MiB")
    return encoded


def load_document(path: Path, expected_sha256: str | None = None) -> CollectionDocument:
    """Load bounded untrusted metadata without opening any referenced image.

    Args:
        path (Path): User-selected collection JSON.
        expected_sha256 (str | None): Exact reviewed metadata digest, checked before parsing these same bytes.

    Returns:
        CollectionDocument: Validated candidate settings and confined image references.

    Raises:
        ValueError: If metadata, paths or completion/confirmation claims are invalid.
        OSError: If the file cannot be read.
    """
    encoded = _metadata_bytes(path)
    if expected_sha256 is not None and hashlib.sha256(encoded).hexdigest() != expected_sha256:
        raise ValueError("Collection metadata changed after review; reload and review again")
    try:
        raw = json.loads(encoded)
        if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(CollectionDocument)}:
            raise ValueError("Unsupported collection metadata fields")
        settings = raw["settings"]
        if not isinstance(settings, dict) or not isinstance(settings.get("region"), dict):
            raise ValueError("Invalid collection settings")
        settings["region"] = Region(**settings["region"])
        raw["settings"] = CollectionSettings(**settings)
        records = raw["records"]
        if not isinstance(records, list) or len(records) > raw["settings"].target:
            raise ValueError("Invalid collection record count")
        raw["records"] = tuple(SampleRecord(**record) for record in records)
        document = CollectionDocument(**raw)
        if type(document.schema_version) is not int or document.schema_version != 1:
            raise ValueError("Unsupported collection schema")
        if not isinstance(document.session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", document.session_id):
            raise ValueError("Invalid collection identity")
        if document.state not in {"Collecting", "Awaiting confirmation", "Incomplete", "Confirmed normal"}:
            raise ValueError("Invalid collection state")
        if not isinstance(document.reason, str) or len(document.reason) > 2000:
            raise ValueError("Invalid collection failure reason")
        if (
            document.state in {"Awaiting confirmation", "Confirmed normal"}
            and len(document.records) != document.settings.target
        ):
            raise ValueError("Collection is incomplete")
        if document.state == "Confirmed normal":
            if not isinstance(document.confirmed_at, str) or not document.confirmed_at:
                raise ValueError("Normal confirmation is missing")
        elif document.confirmed_at is not None:
            raise ValueError("Unconfirmed collection has a confirmation timestamp")
        previous_cycle, previous_frame, previous_time = 0, -1, float("-inf")
        for index, record in enumerate(document.records, start=1):
            if (
                record.cycle <= previous_cycle
                or record.frame_id <= previous_frame
                or record.received_at <= previous_time
            ):
                raise ValueError("Collection snapshots must have distinct ordered cycles, frames and times")
            if record.source_file != f"source-{index:04d}.png" or record.crop_file != f"crop-{index:04d}.png":
                raise ValueError("Unexpected collection image filename")
            local_file(path.parent, record.source_file)
            local_file(path.parent, record.crop_file)
            previous_cycle, previous_frame, previous_time = record.cycle, record.frame_id, record.received_at
        return document
    except (TypeError, KeyError, UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError("Collection metadata is malformed") from error


class CandidateCollection:
    """Bounded background PNG writer accepting detected or operator-declared cycle snapshots.

    Args:
        parent (Path): Explicit output directory; specimen IDs never determine paths.
        settings (CollectionSettings): Locked specimen, synchronization and ROI settings.
        capacity (int): Maximum queued snapshots before collection fails visibly.
    """

    def __init__(self, parent: Path, settings: CollectionSettings, capacity: int = 4) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 32:
            raise ValueError("Collection queue capacity must be an integer from 1 to 32")
        self.folder = parent.resolve() / f"collection-{uuid4().hex}"
        self._document = CollectionDocument(self.folder.name.removeprefix("collection-"), settings)
        self._queue: Queue[tuple[Image, int, int, float, str] | None] = Queue(maxsize=capacity)
        self._lock = Lock()
        self._stop = Event()
        self.finished = Event()
        self._accepted = 0
        self._last_cycle = 0
        self._last_frame = -1
        self._last_time = float("-inf")
        self._thread = Thread(target=self._run, name="rdt-candidate-writer", daemon=True)

    @property
    def document(self) -> CollectionDocument:
        """Return immutable progress; only persisted pairs contribute to the saved count."""
        with self._lock:
            return self._document

    def start(self) -> None:
        """Start the single-use writer; never initiate training."""
        self._thread.start()

    def try_document(self) -> CollectionDocument | None:
        """Return immutable progress when available, without waiting on writer persistence.

        Returns:
            CollectionDocument | None: Current snapshot, or None while the writer owns the lock.
        """
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._document
        finally:
            self._lock.release()

    def capture(self, image: Image, result: SyncResult) -> None:
        """Queue a distinct valid cycle image without file I/O in the camera worker.

        Args:
            image (Image): Exact BGR source frame that validated the returned position.
            result (SyncResult): Completed-cycle observation associated with these pixels.

        Raises:
            ValueError: If the collection is not automatic.
        """
        with self._lock:
            if self._document.settings.cycle_mode != "auto":
                raise ValueError("Automatic capture requires auto cycle mode")
            if self._stop.is_set() or self._accepted >= self._document.settings.target:
                return
            if (
                not result.inspection_ready
                or result.state != SyncState.TRACKING
                or result.observed_cycles <= self._last_cycle
            ):
                return
            self._enqueue_locked(image, result.observed_cycles, result.frame_id, result.timestamp)

    def capture_manual(self, image: Image, frame_id: int, received_at: float) -> bool:
        """Queue one operator-declared cycle without claiming marker detection.

        Args:
            image (Image): Current camera pixels selected by the operator.
            frame_id (int): Frame identity within the locked camera connection.
            received_at (float): Host monotonic frame read-completion time.

        Returns:
            bool: Whether a new distinct frame was accepted.

        Raises:
            ValueError: If the collection is not manual.
        """
        with self._lock:
            if self._document.settings.cycle_mode != "manual":
                raise ValueError("Manual capture requires manual cycle mode")
            if self._stop.is_set() or self._accepted >= self._document.settings.target:
                return False
            return self._enqueue_locked(image, self._accepted + 1, frame_id, received_at)

    def _enqueue_locked(self, image: Image, cycle: int, frame_id: int, received_at: float) -> bool:
        if frame_id <= self._last_frame or received_at <= self._last_time:
            return False
        try:
            self._queue.put_nowait((image.copy(), cycle, frame_id, received_at, datetime.now(UTC).isoformat()))
        except Full:
            self._fail_locked("Collection writer backlog overflow; candidates incomplete")
            return False
        self._accepted += 1
        self._last_cycle, self._last_frame, self._last_time = cycle, frame_id, received_at
        return True

    def interrupt(self, reason: str) -> None:
        """Latch incomplete collection when coverage ends before the target is accepted.

        Args:
            reason (str): Explicit stop, synchronization failure or camera coverage loss.
        """
        with self._lock:
            if self._accepted < self._document.settings.target and not self.finished.is_set():
                self._fail_locked(reason)
        try:
            self._queue.put_nowait(None)
        except Full:
            pass

    def _fail_locked(self, reason: str) -> None:
        self._document = replace(self._document, state="Incomplete", reason=reason[:2000])
        self._stop.set()

    def _run(self) -> None:
        try:
            self.folder.parent.mkdir(parents=True, exist_ok=True)
            self.folder.mkdir(exist_ok=False)
            save_document(self.folder, self.document)
            while not self._stop.is_set():
                try:
                    pending = self._queue.get(timeout=0.2)
                except Empty:
                    continue
                if pending is None:
                    continue
                image, cycle, frame_id, received_at, captured = pending
                settings = self.document.settings
                if image.dtype != np.uint8 or image.shape != (settings.height, settings.width, 3):
                    raise ValueError("Collection source geometry or pixel format changed")
                left, top, right, bottom = settings.region.pixels(image)
                encoded: list[bytes] = []
                for pixels in (image, image[top:bottom, left:right]):
                    success, buffer = cv2.imencode(".png", pixels)
                    if not success:
                        raise ValueError("Could not encode collection image")
                    encoded.append(buffer.tobytes())
                index = len(self.document.records) + 1
                source_name, crop_name = f"source-{index:04d}.png", f"crop-{index:04d}.png"
                atomic_bytes(local_file(self.folder, source_name), encoded[0])
                atomic_bytes(local_file(self.folder, crop_name), encoded[1])
                record = SampleRecord(
                    cycle,
                    frame_id,
                    received_at,
                    captured,
                    source_name,
                    crop_name,
                    hashlib.sha256(encoded[0]).hexdigest(),
                    hashlib.sha256(encoded[1]).hexdigest(),
                )
                with self._lock:
                    records = (*self._document.records, record)
                    state = self._document.state
                    if not self._stop.is_set() and len(records) == settings.target:
                        state = "Awaiting confirmation"
                    self._document = replace(self._document, records=records, state=state)
                    document = self._document
                save_document(self.folder, document)
                if len(records) == settings.target:
                    break
            save_document(self.folder, self.document)
        except (OSError, ValueError, TypeError, cv2.error) as error:
            with self._lock:
                self._fail_locked(str(error) or "Candidate collection failed")
        finally:
            self.finished.set()
