# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog

import rdt_monitor.app as desktop
from rdt_monitor.recipes import (
    RecipeProfile,
    StoredRecipe,
    load_recipe,
    save_inspection_recipe,
    save_recipe,
    save_run_setup,
)
from rdt_monitor.replay import MarkerAppearance, Region


@pytest.mark.parametrize("width", [31, 32])
def test_setup_image_buffer_preserves_pixels_and_row_padding(application, width):
    pixels = np.random.default_rng(42).integers(0, 256, (60, width, 3), dtype=np.uint8)
    image = QImage(pixels.data, width, 60, pixels.strides[0], QImage.Format.Format_BGR888).copy()
    recipe = replace(desktop.fixture_recipe(), marker=Region(0.1, 0.1, 0.3, 0.5), position=Region(0.6, 0.1, 0.3, 0.5))
    window = desktop.MainWindow()
    dialog = desktop.ReplaySettings(recipe, window, camera_image=image, specimen_id="actual-specimen")
    try:
        for inputs, region in zip(dialog._regions, (recipe.marker, recipe.position), strict=True):
            for spin, value in zip(inputs, (region.left, region.top, region.width, region.height), strict=True):
                spin.setValue(value * 100)
        dialog.recognition_mode.setCurrentIndex(0)
        candidate = dialog._candidate_recipe(require_confirmation=False)
        assert candidate.marker_appearance == MarkerAppearance.capture(pixels, candidate.marker)
        assert candidate.position_appearance == MarkerAppearance.capture(pixels, candidate.position)
        window.live_view._display_image(pixels)
        before = window.live_view._image.pixelColor(0, 0)
        pixels[:] = 0
        assert window.live_view._image.pixelColor(0, 0) == before
    finally:
        dialog.close()
        window.close()
        application.processEvents()


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup_dialog(application, tmp_path):
    window = desktop.MainWindow()
    pixels = np.random.default_rng(42).integers(0, 256, (120, 160, 3), dtype=np.uint8)
    image = QImage(pixels.data, 160, 120, pixels.strides[0], QImage.Format.Format_BGR888).copy()
    recipe = desktop.fixture_recipe()
    model = tmp_path / "trained.xml"
    model.write_bytes(b"model")
    model.with_suffix(".bin").write_bytes(b"weights")
    path = tmp_path / "family.json"
    save_inspection_recipe(
        path, StoredRecipe("A-001", recipe, 160, 120), model, desktop.Region(0.2, 0.2, 0.6, 0.6), 0.5
    )
    stored = replace(
        load_recipe(path), profile=RecipeProfile("a" * 32, "b" * 32, "A-type standard bending", "Long duration")
    )
    save_recipe(path, stored)
    dialog = desktop.ReplaySettings(recipe, window, camera_image=image, specimen_id="A-002")
    assert dialog._apply_stored_recipe(stored, path)
    yield dialog, stored, path
    dialog.close()
    window.close()
    application.processEvents()


def test_basic_defaults_preserve_actual_specimen_and_allow_advanced_without_credentials(setup_dialog):
    dialog, stored, _ = setup_dialog
    assert dialog.setup_tabs.currentIndex() == 0
    assert dialog.setup_tabs.tabText(0) == "Basic"
    assert dialog.setup_tabs.tabText(1) == "Advanced"
    assert dialog.specimen_input.text() == "A-002"
    assert dialog.recipe_name.text() == stored.profile.name
    dialog.setup_tabs.setCurrentIndex(1)
    assert dialog.advanced_panel.isEnabled()
    assert not any("Authorize" in button.text() for button in dialog.findChildren(desktop.QPushButton))
    assert not dialog._confirmed.isChecked()
    assert dialog.preview.inspection_region == stored.detector.region


def test_operator_relabels_markers_and_records_run_without_replacing_model(setup_dialog, tmp_path):
    dialog, stored, path = setup_dialog
    original = path.read_bytes()
    dialog._regions[1][0].setValue(65)
    dialog.specimen_name.setText("Cushion")
    dialog.specimen_batch.setText("lot-2")
    dialog._confirmed.setChecked(True)
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.run_setup.specimen_id == "A-002"
    assert dialog.run_setup.recipe.position != stored.recipe.position
    assert dialog.run_setup.detector == stored.detector
    snapshot = save_run_setup(tmp_path / "results", "c" * 12, dialog.run_setup, path)
    record = json.loads(snapshot.read_text())
    assert record["setup"]["specimen"]["batch"] == "lot-2"
    assert record["setup"]["profile"]["family_id"] == stored.profile.family_id
    assert path.read_bytes() == original
    assert len(list(tmp_path.glob("detector-*"))) == 1


def test_technical_edits_can_be_saved_without_credentials(setup_dialog, monkeypatch, tmp_path):
    dialog, _, _ = setup_dialog
    dialog._stable.setValue(dialog._stable.value() + 1)
    dialog._confirmed.setChecked(True)
    dialog._save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "new recipe revision" in dialog._error.text()
    target = tmp_path / "technical-revision.json"
    monkeypatch.setattr(
        desktop.QFileDialog,
        "getSaveFileName",
        lambda *args: (str(target), ""),
    )
    dialog._store_recipe()
    assert load_recipe(target).recipe.synchronization.stable_frames == dialog._stable.value()
    assert not dialog._error.text()
    dialog._confirmed.setChecked(True)
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_new_revision_without_credentials_reuses_bundle_and_preserves_original(setup_dialog, monkeypatch, tmp_path):
    dialog, stored, path = setup_dialog
    original = path.read_bytes()
    dialog.recipe_name.setText("A-type shifted fixture")
    dialog.recipe_notes.setPlainText("Second mounting setup")
    dialog._regions[1][0].setValue(65)
    target = tmp_path / "alternate.json"
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    dialog._store_recipe()
    saved = load_recipe(target)
    assert saved.profile.family_id == stored.profile.family_id
    assert saved.profile.revision != stored.profile.revision
    assert saved.profile.name == "A-type shifted fixture"
    assert saved.detector == stored.detector
    assert path.read_bytes() == original
    assert len(list(tmp_path.glob("detector-*"))) == 1
    dialog._confirmed.setChecked(True)
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_advanced_decision_changes_must_be_saved_not_silently_ignored(setup_dialog):
    dialog, _, _ = setup_dialog
    dialog.detector_threshold.setValue(0.6)
    dialog._confirmed.setChecked(True)
    dialog._save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "new recipe revision" in dialog._error.text()
