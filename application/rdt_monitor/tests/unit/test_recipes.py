# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

import rdt_monitor.recipes as storage
from rdt_monitor.domain import SyncConfig
from rdt_monitor.lifecycle import InspectionOutcome
from rdt_monitor.recipes import RecipeDetector, StoredRecipe, load_recipe, save_inspection_recipe, save_recipe
from rdt_monitor.replay import MarkerAppearance, Region, ReplayRecipe


def named(stored):
    return replace(
        stored,
        profile=storage.RecipeProfile("a" * 32, "b" * 32, "A-type standard bending", "Long duration"),
        specimen=storage.SpecimenDetails("Cushion A", "batch-01"),
    )


def test_named_recipe_round_trip_and_strict_metadata(tmp_path, stored):
    stored = named(stored)
    path = tmp_path / "family.json"
    save_recipe(path, stored)
    assert load_recipe(path) == stored
    document = json.loads(path.read_text())
    assert document["schema_version"] == 5
    document["profile"]["name"] = 123
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_recipe(path)


@pytest.mark.parametrize("field,value", [("name", ""), ("notes", "x" * 2001), ("family_id", "../escape")])
def test_named_recipe_rejects_invalid_metadata(stored, field, value):
    with pytest.raises(ValueError):
        replace(named(stored).profile, **{field: value})


def test_operator_setup_reuses_model_without_modifying_parent(stored, detector):
    parent = named(replace(stored, detector=detector))
    markers = replace(parent.recipe, position=Region(0.7, 0.3, 0.2, 0.2))
    setup = storage.specimen_setup(parent, "A-002", markers, storage.SpecimenDetails("A", "lot-2"))
    assert setup.detector is parent.detector
    assert setup.profile is parent.profile
    assert setup.specimen_id == "A-002"
    assert setup.recipe.position != parent.recipe.position
    assert parent.specimen_id == "PCB-001"
    with pytest.raises(ValueError, match="new recipe revision"):
        storage.specimen_setup(parent, "A-002", replace(markers, brightness=42), storage.SpecimenDetails())


def test_managed_recipe_save_without_credentials_preserves_original(tmp_path, stored, detector):
    parent = named(replace(stored, detector=detector))
    bundle = tmp_path / Path(detector.model_file).parent
    bundle.mkdir()
    (bundle / "model.xml").write_bytes(b"model")
    (bundle / "model.bin").write_bytes(b"weights")
    source = tmp_path / "family.json"
    save_recipe(source, parent)
    before = source.read_bytes()
    target = tmp_path / "alternate.json"
    revision = replace(parent, profile=replace(parent.profile, revision="c" * 32))
    storage.save_managed_recipe(target, revision, source=source)
    assert load_recipe(target).detector == detector
    assert source.read_bytes() == before
    assert len(list(tmp_path.glob("detector-*"))) == 1
    with pytest.raises(FileExistsError):
        storage.save_managed_recipe(source, revision, source=source)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="beside"):
        storage.save_managed_recipe(elsewhere / "recipe.json", revision, source=source)


def test_run_snapshot_retains_setup_and_parent_identity(tmp_path, stored, detector):
    source = tmp_path / "family.json"
    parent = named(replace(stored, detector=detector))
    save_recipe(source, parent)
    setup = storage.specimen_setup(parent, "../../A-002", parent.recipe, storage.SpecimenDetails("A", "lot"))
    path = storage.save_run_setup(tmp_path / "results", "a" * 12, setup, source)
    snapshot = json.loads(path.read_text())
    assert snapshot["setup"]["specimen_id"] == "../../A-002"
    assert snapshot["setup"]["detector"]["model_sha256"] == detector.model_sha256
    assert snapshot["setup"]["profile"]["revision"] == parent.profile.revision
    assert snapshot["recipe_sha256"] == storage.file_digest(source)
    assert source.read_bytes() != path.read_bytes()
    with pytest.raises(FileExistsError):
        storage.save_run_setup(tmp_path / "results", "a" * 12, setup, source)
    with pytest.raises(ValueError):
        storage.save_run_setup(tmp_path / "results", "../outside", setup, source)


@pytest.fixture
def stored():
    return StoredRecipe(
        "PCB-001",
        ReplayRecipe(SyncConfig(3, 0.15, 8), Region(0.1, 0.2, 0.2, 0.3), Region(0.6, 0.2, 0.2, 0.3), 195, 0.1, 0.9),
        640,
        480,
    )


def test_recipe_round_trip_and_replacement(tmp_path, stored):
    path = tmp_path / "recipe.json"
    save_recipe(path, stored)
    assert load_recipe(path) == stored
    assert json.loads(path.read_text())["schema_version"] == 1
    save_recipe(path, StoredRecipe("PCB-002", stored.recipe, 640, 480))
    assert load_recipe(path).specimen_id == "PCB-002"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "content", ["{", "[]", "null", "{}", "x" * 65537], ids=["malformed", "array", "null", "missing", "oversized"]
)
def test_invalid_documents_are_rejected(tmp_path, content):
    path = tmp_path / "recipe.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_recipe(path)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        (None, "schema_version", 3),
        (None, "schema_version", True),
        (None, "specimen_id", "  "),
        (None, "specimen_id", 123),
        (None, "image_width", 0),
        (None, "image_height", False),
        ("recipe", "brightness", 256),
        ("recipe", "brightness", True),
        ("recipe", "absent_fraction", float("nan")),
        ("recipe", "present_fraction", 0),
        ("synchronization", "stable_frames", 0),
        ("synchronization", "stable_frames", True),
        ("synchronization", "max_gap_seconds", float("inf")),
        ("marker", "left", -0.1),
        ("position", "width", 2),
        ("marker", "height", 0.0000001),
    ],
)
def test_invalid_recipe_values_are_rejected(tmp_path, stored, section, field, value):
    path = tmp_path / "recipe.json"
    save_recipe(path, stored)
    document = json.loads(path.read_text())
    target = document if section is None else document["recipe"]
    if section not in (None, "recipe"):
        target = target[section]
    target[field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_recipe(path)


def test_failed_atomic_replace_preserves_previous_recipe(tmp_path, stored, monkeypatch):
    path = tmp_path / "recipe.json"
    save_recipe(path, stored)

    def fail(*args):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="Disk unavailable"):
        save_recipe(path, StoredRecipe("PCB-002", stored.recipe, 640, 480))
    assert load_recipe(path) == stored
    assert list(tmp_path.iterdir()) == [path]


def test_specimen_id_is_metadata_not_a_path(tmp_path, stored):
    path = tmp_path / "selected.json"
    recipe = StoredRecipe("../../outside", stored.recipe, 640, 480)
    save_recipe(path, recipe)
    assert load_recipe(path) == recipe
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("failure", ["huge_number", "huge_dimensions", "nested", "encoding", "identical"])
def test_extreme_or_incompatible_documents_raise_value_error(tmp_path, stored, failure):
    path = tmp_path / "recipe.json"
    save_recipe(path, stored)
    if failure == "nested":
        path.write_text("[" * 1200 + "0" + "]" * 1200)
    elif failure == "encoding":
        path.write_bytes(b"\xff\xff\xff")
    else:
        document = json.loads(path.read_text())
        if failure == "huge_number":
            document["recipe"]["marker"]["left"] = 10**400
        elif failure == "huge_dimensions":
            document["image_width"] = 10**400
        else:
            document["recipe"]["position"] = document["recipe"]["marker"]
        path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_recipe(path)


def test_captured_mark_references_round_trip_and_reject_corrupt_pixels(tmp_path, stored):
    reference = cv2.imread(str(Path(__file__).parents[1] / "fixtures" / "pink-marks-reference.png"))
    marker = Region(9 / 200, 6 / 185, 46 / 200, 49 / 185)
    position = Region(152 / 200, 129 / 185, 45 / 200, 47 / 185)
    recipe = replace(
        stored.recipe,
        marker=marker,
        position=position,
        marker_appearance=MarkerAppearance.capture(reference, marker),
        position_appearance=MarkerAppearance.capture(reference, position),
    )
    saved = StoredRecipe("coil-01", recipe, 200, 185)
    path = tmp_path / "appearance.json"
    save_recipe(path, saved)
    assert path.stat().st_size < 65536
    assert load_recipe(path) == saved
    document = json.loads(path.read_text())
    assert document["schema_version"] == 2
    document["recipe"]["marker_appearance"]["pixels"][0] = True
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_recipe(path)


@pytest.fixture
def detector():
    return RecipeDetector("detector-" + "a" * 32 + "/model.xml", "b" * 64, "c" * 64, Region(0, 0, 1, 1), 0.5)


@pytest.mark.parametrize(
    ("score", "outcome"),
    [
        (0.0, InspectionOutcome.NO_DEFECT),
        (float(np.nextafter(np.float32(0.45), np.float32(0))), InspectionOutcome.NO_DEFECT),
        (0.45, InspectionOutcome.NEEDS_REVIEW),
        (0.50, InspectionOutcome.NEEDS_REVIEW),
        (0.55, InspectionOutcome.NEEDS_REVIEW),
        (float(np.nextafter(np.float32(0.55), np.float32(1))), InspectionOutcome.DEFECT),
        (1.0, InspectionOutcome.DEFECT),
    ],
)
def test_verdict_inclusive_float32_boundaries(detector, score, outcome):
    assert detector.verdict(score) == outcome


@pytest.mark.parametrize("margin", [-0.01, 0.51, float("nan"), float("inf"), True])
def test_invalid_review_bands_rejected(detector, margin):
    with pytest.raises(ValueError, match="Review margin"):
        replace(detector, review_margin=margin)


@pytest.mark.parametrize("threshold", [0.01, 0.99])
def test_band_must_not_extend_outside_score_range(detector, threshold):
    with pytest.raises(ValueError, match="entire review band"):
        replace(detector, threshold=threshold)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_invalid_verdict_scores_rejected(detector, score):
    with pytest.raises(ValueError, match="normalized score"):
        detector.verdict(score)


def test_zero_margin_reviews_exact_threshold_only(detector):
    binding = replace(detector, review_margin=0)
    assert binding.verdict(0.5) == InspectionOutcome.NEEDS_REVIEW
    assert binding.verdict(0.49) == InspectionOutcome.NO_DEFECT
    assert binding.verdict(0.51) == InspectionOutcome.DEFECT


def test_review_margin_round_trip_and_legacy_model_recipe_default(tmp_path, stored, detector):
    path = tmp_path / "inspection.json"
    saved = replace(stored, detector=replace(detector, review_margin=0.1))
    save_recipe(path, saved)
    assert load_recipe(path) == saved
    raw = json.loads(path.read_text())
    assert raw["schema_version"] == 4
    raw["schema_version"] = 3
    raw["detector"].pop("review_margin")
    path.write_text(json.dumps(raw))
    assert load_recipe(path).detector.review_margin == 0.05
    raw["schema_version"] = 4
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="fields"):
        load_recipe(path)


def test_invalid_band_does_not_create_bundle_or_recipe(tmp_path, stored):
    model = tmp_path / "model.xml"
    model.write_bytes(b"model")
    model.with_suffix(".bin").write_bytes(b"weights")
    with pytest.raises(ValueError, match="Review margin"):
        save_inspection_recipe(tmp_path / "invalid.json", stored, model, Region(0, 0, 1, 1), 0.01, 0.05)
    assert not list(tmp_path.glob("detector-*"))
    assert not (tmp_path / "invalid.json").exists()


def test_uut_family_and_article_number_round_trip(tmp_path, stored):
    from uuid import uuid4

    from rdt_monitor.recipes import RecipeProfile, SpecimenDetails

    path = tmp_path / "uut.json"
    original = StoredRecipe(
        "serial",
        stored.recipe,
        stored.image_width,
        stored.image_height,
        profile=RecipeProfile(uuid4().hex, uuid4().hex, "Family recipe"),
        specimen=SpecimenDetails("Part", "Batch", "UUT family", "001234567890"),
    )
    save_recipe(path, original)
    assert load_recipe(path) == original
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 6
    with pytest.raises(ValueError, match="12 digits"):
        SpecimenDetails(article_number="123")
