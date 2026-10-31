# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Validated synchronization and model-bound inspection recipes."""

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import numpy as np

from rdt_monitor.collection import atomic_bytes, local_file
from rdt_monitor.detection import DetectionSettings
from rdt_monitor.domain import SyncConfig
from rdt_monitor.lifecycle import InspectionOutcome
from rdt_monitor.replay import MarkerAppearance, Region, ReplayRecipe


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Recipe fields are missing or unsupported")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer in recipe")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected text in recipe")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a finite number in recipe")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError("Recipe number is too large") from error
    if not isfinite(number):
        raise ValueError("Expected a finite number in recipe")
    return number


def _region(value: object) -> Region:
    fields = _object(value, {"left", "top", "width", "height"})
    return Region(*(_number(fields[name]) for name in ("left", "top", "width", "height")))


def _appearance(value: object) -> MarkerAppearance:
    fields = _object(value, {"pixels"})
    pixels = fields["pixels"]
    if not isinstance(pixels, list) or len(pixels) != 1024:
        raise ValueError("Mark reference must contain 1024 grayscale pixels")
    return MarkerAppearance(tuple(_integer(pixel) for pixel in pixels))


@dataclass(frozen=True)
class RecipeDetector:
    """Frozen unqualified detector binding, relative to its recipe directory.

    Args:
        model_file (str): Confined relative XML filename.
        model_sha256 (str): XML integrity digest.
        weights_sha256 (str): Matching BIN integrity digest.
        region (Region): Training/inspection ROI in the original source frame.
        threshold (float): Explicit unqualified score threshold.
        review_margin (float): Inclusive review half-band around the threshold.
    """

    model_file: str
    model_sha256: str
    weights_sha256: str
    region: Region
    threshold: float
    review_margin: float = 0.05

    def __post_init__(self) -> None:
        if not isinstance(self.model_file, str) or not re.fullmatch(
            r"detector-[0-9a-f]{32}/model\.xml", self.model_file
        ):
            raise ValueError("Detector reference must be a bundled relative XML filename")
        if any(
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in (self.model_sha256, self.weights_sha256)
        ):
            raise ValueError("Detector integrity hashes must be SHA-256 digests")
        if isinstance(self.threshold, bool) or not isfinite(self.threshold) or not 0 < self.threshold < 1:
            raise ValueError("Detector threshold must be strictly between zero and one")
        if (
            isinstance(self.review_margin, bool)
            or not isfinite(self.review_margin)
            or self.review_margin < 0
            or self.threshold - self.review_margin < 0
            or self.threshold + self.review_margin > 1
        ):
            raise ValueError("Review margin must be finite, nonnegative, and keep the entire review band in [0, 1]")

    def verdict(self, score: float) -> InspectionOutcome:
        """Classify a normalized score using an inclusive, unqualified review band.

        Args:
            score (float): Model score, not a defect probability.

        Returns:
            InspectionOutcome: Normal, needs review, or defect. Comparisons use the
            same float32 precision as model outputs, including both band endpoints.

        Raises:
            ValueError: If the score is not finite and normalized.
        """
        if isinstance(score, bool) or not isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Verdict requires one finite normalized score")
        if np.float32(score) < np.float32(self.threshold - self.review_margin):
            return InspectionOutcome.NO_DEFECT
        if np.float32(score) > np.float32(self.threshold + self.review_margin):
            return InspectionOutcome.DEFECT
        return InspectionOutcome.NEEDS_REVIEW

    def settings(self, recipe_path: Path) -> DetectionSettings:
        """Resolve a bundled detector strictly beneath the selected recipe directory.

        Args:
            recipe_path (Path): Explicitly selected JSON recipe.

        Returns:
            DetectionSettings: Confined model, ROI and threshold.

        Raises:
            ValueError: If either model path escapes the recipe directory.
        """
        root = recipe_path.parent.resolve()
        model = root / self.model_file
        for candidate in (model, model.with_suffix(".bin")):
            if not candidate.resolve().is_relative_to(root) or candidate.is_symlink():
                raise ValueError("Detector path escapes the recipe directory")
        return DetectionSettings(model, self.region, self.threshold)


def file_digest(path: Path) -> str:
    """Return a streaming SHA-256 digest of a selected model file.

    Args:
        path (Path): Model or matching weights file.

    Returns:
        str: Hex digest.
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class RecipeProfile:
    """Reusable family recipe metadata, independent of specimen serial numbers.

    Args:
        family_id (str): Stable generated family identity.
        revision (str): Unique immutable recipe revision.
        name (str): Operator-friendly purpose-based recipe name.
        notes (str): Optional purpose notes.
    """

    family_id: str
    revision: str
    name: str
    notes: str = ""

    def __post_init__(self) -> None:
        for value in (self.family_id, self.revision):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
                raise ValueError("Family and revision identities must be generated UUID hex strings")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 128:
            raise ValueError("Recipe name must contain 1-128 characters")
        if not isinstance(self.notes, str) or len(self.notes) > 2000:
            raise ValueError("Recipe purpose notes must contain at most 2000 characters")


@dataclass(frozen=True)
class SpecimenDetails:
    """Optional specimen metadata, never used to construct filesystem paths.

    Args:
        name (str): Specimen display name.
        batch (str): Batch or lot identifier.
    """

    name: str = ""
    batch: str = ""
    family: str = ""
    article_number: str = ""

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or len(value) > 128 for value in (self.name, self.batch, self.family)):
            raise ValueError("Specimen name, batch and family must contain at most 128 characters")
        if not isinstance(self.article_number, str) or (
            self.article_number and not re.fullmatch(r"[0-9]{12}", self.article_number)
        ):
            raise ValueError("Article number must be exactly 12 digits when provided")


@dataclass(frozen=True)
class StoredRecipe:
    """Synchronization settings associated with a specimen and source dimensions.

    Args:
        specimen_id (str): Nonempty specimen identity, never a filesystem path.
        recipe (ReplayRecipe): Validated regions, recognition, and timing settings.
        image_width (int): Source image width used during setup.
        image_height (int): Source image height used during setup.
        detector (RecipeDetector | None): Optional unqualified live detector binding.
        profile (RecipeProfile | None): Named family recipe; absent in legacy recipes.
        specimen (SpecimenDetails): Optional name and batch for the specimen setup.

    Raises:
        ValueError: If identity, dimensions, or regions are unsuitable for storage.
    """

    specimen_id: str
    recipe: ReplayRecipe
    image_width: int
    image_height: int
    detector: RecipeDetector | None = None
    profile: RecipeProfile | None = None
    specimen: SpecimenDetails = SpecimenDetails()

    @property
    def mode_label(self) -> str:
        """Return the selected behavior without implying production approval."""
        return "Unqualified inspection" if self.detector is not None else "Counting only - no detector"

    def description(self, path: Path) -> str:
        """Describe saved marker labels and the optional model binding.

        Args:
            path (Path): Selected recipe path, used to display the resolved model location.

        Returns:
            str: Human-readable identity, marker regions, recognition mode, ROI and model details.
        """
        lines = [
            str(path),
            self.mode_label,
            f"Specimen: {self.specimen_id} | Source: {self.image_width} x {self.image_height}",
            f"Start marker: {self.recipe.marker}",
            f"Position marker: {self.recipe.position}",
            "Recognition: appearance references"
            if self.recipe.marker_appearance is not None
            else "Recognition: brightness (no appearance references)",
        ]
        if self.profile is not None:
            lines.extend((f"Recipe: {self.profile.name}", self.profile.notes, f"Revision: {self.profile.revision}"))
        if self.detector is not None:
            lines.extend(
                (
                    f"Model: {path.parent / self.detector.model_file}",
                    f"Inspection ROI: {self.detector.region} | Threshold {self.detector.threshold:.3f}",
                    f"Review margin: {self.detector.review_margin:.3f} (inclusive boundary band)",
                    f"Model SHA-256: {self.detector.model_sha256}",
                    f"Weights SHA-256: {self.detector.weights_sha256}",
                )
            )
        return "\n".join(lines)

    def __post_init__(self) -> None:
        if not isinstance(self.specimen_id, str) or not self.specimen_id.strip() or len(self.specimen_id) > 128:
            raise ValueError("A specimen ID of 1-128 characters is required")
        if not all(1 <= _integer(value) <= 65535 for value in (self.image_width, self.image_height)):
            raise ValueError("Recipe image dimensions must be in [1, 65535]")
        if self.recipe.marker == self.recipe.position:
            raise ValueError("Start and position regions must be distinct")
        for region in (self.recipe.marker, self.recipe.position):
            if int((region.left + region.width) * self.image_width) <= int(region.left * self.image_width) or int(
                (region.top + region.height) * self.image_height
            ) <= int(region.top * self.image_height):
                raise ValueError("Each recipe region must cover at least one source pixel")


def save_recipe(path: Path, stored: StoredRecipe) -> None:
    """Atomically save a recipe without deriving any path from specimen metadata.

    Args:
        path (Path): Destination explicitly selected by the operator.
        stored (StoredRecipe): Recipe and associated specimen identity.

    Raises:
        OSError: If the destination cannot be written or atomically replaced.
    """
    document = asdict(stored)
    document.pop("detector")
    document.pop("profile")
    document.pop("specimen")
    if stored.recipe.marker_appearance is None:
        document["recipe"].pop("marker_appearance")
        document["recipe"].pop("position_appearance")
        document["schema_version"] = 1
    else:
        document["schema_version"] = 2
    if stored.detector is not None:
        document["detector"] = asdict(stored.detector)
        document["schema_version"] = 4
    if stored.profile is not None:
        document["schema_version"] = 6 if stored.specimen.family or stored.specimen.article_number else 5
        document["profile"] = asdict(stored.profile)
        document["specimen"] = asdict(stored.specimen)
        if document["schema_version"] == 5:
            document["specimen"].pop("family")
            document["specimen"].pop("article_number")
        document["detector"] = asdict(stored.detector) if stored.detector is not None else None
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_recipe(path: Path) -> StoredRecipe:
    """Load a bounded JSON recipe and validate its complete schema and values.

    Args:
        path (Path): Operator-selected JSON recipe file.

    Returns:
        StoredRecipe: Validated settings and specimen identity; not authorization to run.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the file is oversized, malformed, unsupported, or invalid.
    """
    with path.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("Recipe file exceeds 64 KiB")
    try:
        raw = json.loads(content)
        keys = {"schema_version", "specimen_id", "recipe", "image_width", "image_height"}
        if isinstance(raw, dict) and raw.get("schema_version") in (3, 4, 5, 6):
            keys.add("detector")
        if isinstance(raw, dict) and raw.get("schema_version") in (5, 6):
            keys |= {"profile", "specimen"}
        document = _object(raw, keys)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Recipe file must contain valid JSON text") from error
    version = _integer(document["schema_version"])
    if version not in (1, 2, 3, 4, 5, 6):
        raise ValueError("Unsupported recipe schema version")
    specimen_id = document["specimen_id"]
    if not isinstance(specimen_id, str):
        raise ValueError("Recipe specimen ID must be text")
    fields = {"synchronization", "marker", "position", "brightness", "absent_fraction", "present_fraction"}
    if not isinstance(document["recipe"], dict):
        raise ValueError("Recipe settings must be an object")
    has_appearance = version == 2 or (version in (3, 4, 5, 6) and "marker_appearance" in document["recipe"])
    if has_appearance:
        fields |= {"marker_appearance", "position_appearance"}
    settings = _object(document["recipe"], fields)
    timing = _object(settings["synchronization"], {"stable_frames", "max_gap_seconds", "cycle_timeout_seconds"})
    recipe = ReplayRecipe(
        synchronization=SyncConfig(
            _integer(timing["stable_frames"]),
            _number(timing["max_gap_seconds"]),
            _number(timing["cycle_timeout_seconds"]),
        ),
        marker=_region(settings["marker"]),
        position=_region(settings["position"]),
        brightness=_integer(settings["brightness"]),
        absent_fraction=_number(settings["absent_fraction"]),
        present_fraction=_number(settings["present_fraction"]),
        marker_appearance=_appearance(settings["marker_appearance"]) if has_appearance else None,
        position_appearance=_appearance(settings["position_appearance"]) if has_appearance else None,
    )
    detector = None
    if version in (3, 4) or (version in (5, 6) and document["detector"] is not None):
        fields = {"model_file", "model_sha256", "weights_sha256", "region", "threshold"}
        if version >= 4:
            fields.add("review_margin")
        binding = _object(document["detector"], fields)
        detector = RecipeDetector(
            _text(binding["model_file"]),
            _text(binding["model_sha256"]),
            _text(binding["weights_sha256"]),
            _region(binding["region"]),
            _number(binding["threshold"]),
            _number(binding["review_margin"]) if version >= 4 else 0.05,
        )
    profile = None
    specimen = SpecimenDetails()
    if version in (5, 6):
        metadata = _object(document["profile"], {"family_id", "revision", "name", "notes"})
        profile = RecipeProfile(*(_text(metadata[key]) for key in ("family_id", "revision", "name", "notes")))
        details = _object(
            document["specimen"], {"name", "batch", "family", "article_number"} if version == 6 else {"name", "batch"}
        )
        specimen = SpecimenDetails(
            _text(details["name"]),
            _text(details["batch"]),
            _text(details["family"]) if version == 6 else "",
            _text(details["article_number"]) if version == 6 else "",
        )
    return StoredRecipe(
        specimen_id.strip(),
        recipe,
        _integer(document["image_width"]),
        _integer(document["image_height"]),
        detector,
        profile,
        specimen,
    )


def specimen_setup(
    original: StoredRecipe, specimen_id: str, recipe: ReplayRecipe, details: SpecimenDetails
) -> StoredRecipe:
    """Create a run-specific marker setup without changing the family inspection definition.

    Args:
        original (StoredRecipe): Selected family recipe.
        specimen_id (str): Actual specimen serial number.
        recipe (ReplayRecipe): Candidate marker setup.
        details (SpecimenDetails): Specimen name and batch.

    Returns:
        StoredRecipe: Frozen setup retaining the exact detector and family binding.

    Raises:
        ValueError: If technical synchronization changes have not been saved as a new recipe revision.
    """
    unchanged = replace(
        recipe,
        marker=original.recipe.marker,
        position=original.recipe.position,
        marker_appearance=original.recipe.marker_appearance,
        position_appearance=original.recipe.position_appearance,
    )
    if unchanged != original.recipe:
        raise ValueError("Save a new recipe revision before using changed technical synchronization settings")
    return replace(original, specimen_id=specimen_id.strip(), recipe=recipe, specimen=details)


def save_managed_recipe(path: Path, stored: StoredRecipe, *, source: Path | None = None) -> None:
    """Save a new recipe revision, preserving existing recipe files.

    Args:
        path (Path): New destination; existing files cannot be overwritten.
        stored (StoredRecipe): Named recipe with a fresh revision.
        source (Path | None): Original recipe path for resolving an existing detector.

    Raises:
        ValueError: If naming or model reference requirements are not met.
        FileExistsError: If the destination already exists.
    """
    if stored.profile is None:
        raise ValueError("Enter a purpose-based recipe name")
    if path.exists():
        raise FileExistsError("Choose a new filename to preserve the previous recipe revision")
    if stored.detector is not None:
        if source is None or path.parent.resolve() != source.parent.resolve():
            raise ValueError("Save beside the original recipe to reuse its existing detector bundle")
        stored.detector.settings(source)
    save_recipe(path, stored)


def save_run_setup(root: Path, run_id: str, stored: StoredRecipe, source: Path | None) -> Path:
    """Persist a run's complete setup before accepting camera cycles.

    Args:
        root (Path): Explicit evidence root.
        run_id (str): Generated run identity.
        stored (StoredRecipe): Frozen specimen-specific setup.
        source (Path | None): Reusable recipe selected for this run.

    Returns:
        Path: Run setup snapshot, separate from reusable recipe files.

    Raises:
        OSError: If storage or the parent recipe is unavailable.
        ValueError: If output confinement or identity validation fails.
    """
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ValueError("Run setup requires a generated run identity")
    if root.is_symlink() or root.resolve() != root.absolute():
        raise ValueError("Run setup directory escapes the selected evidence root")
    root.mkdir(parents=True, exist_ok=True)
    destination = local_file(root, f"run-{run_id}-setup.json")
    if destination.exists():
        raise FileExistsError("Run setup snapshot already exists")
    document = {
        "schema_version": 1,
        "qualified": False,
        "run_id": run_id,
        "setup_id": uuid4().hex,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "verification": "Operator-declared marker, inspection crop and test-cycle check; not qualification",
        "recipe_source": str(source.resolve()) if source is not None else None,
        "recipe_sha256": file_digest(source) if source is not None else None,
        "setup": asdict(stored),
    }
    atomic_bytes(destination, json.dumps(document, indent=2, allow_nan=False).encode("utf-8"))
    return destination


def save_inspection_recipe(
    path: Path,
    stored: StoredRecipe,
    model: Path,
    region: Region,
    threshold: float,
    review_margin: float = 0.05,
) -> None:
    """Bundle a trained OpenVINO export and atomically save its unqualified recipe.

    Args:
        path (Path): Operator-selected recipe destination.
        stored (StoredRecipe): Confirmed synchronization settings and source dimensions.
        model (Path): Selected trusted XML export.
        region (Region): Exact training ROI.
        threshold (float): Explicit unqualified score threshold.
        review_margin (float): Inclusive half-band for needs-review verdicts.

    Raises:
        ValueError: If inputs or output confinement are invalid.
        OSError: If copying or saving fails.
    """
    if stored.profile is not None:
        if path.exists():
            raise FileExistsError("Choose a new filename to preserve the previous recipe revision")
    DetectionSettings(model, region, threshold)
    if model.suffix.lower() != ".xml":
        raise ValueError("Inspection recipes require an OpenVINO XML/BIN export")
    root = path.parent.resolve()
    folder = root / f"detector-{uuid4().hex}"
    if folder.is_symlink() or folder.resolve().parent != root:
        raise ValueError("Detector bundle escapes recipe directory")
    binding = RecipeDetector(
        f"{folder.name}/model.xml",
        file_digest(model),
        file_digest(model.with_suffix(".bin")),
        region,
        threshold,
        review_margin,
    )
    folder.mkdir()
    for source, name in ((model, "model.xml"), (model.with_suffix(".bin"), "model.bin")):
        destination = folder / name
        if destination.resolve().parent != folder.resolve():
            raise ValueError("Detector destination escapes recipe directory")
        shutil.copyfile(source, destination)
    binding = replace(
        binding,
        model_sha256=file_digest(folder / "model.xml"),
        weights_sha256=file_digest(folder / "model.bin"),
    )
    save_recipe(path, replace(stored, detector=binding))
