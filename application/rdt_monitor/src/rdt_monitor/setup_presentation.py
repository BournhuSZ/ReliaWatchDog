# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Frozen-frame QML setup, preserving existing recipe and marker contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import Property, QEventLoop, QObject, Signal, Slot

from rdt_monitor.recipes import (
    RecipeProfile,
    SpecimenDetails,
    StoredRecipe,
    load_recipe,
    save_managed_recipe,
    specimen_setup,
)
from rdt_monitor.replay import MarkerAppearance, Region, ReplayRecipe
from rdt_monitor.replay_settings import parse_settings, settings_values
from rdt_monitor.setup_choice import SetupChoice

if TYPE_CHECKING:
    from rdt_monitor.camera import CameraFrame
    from rdt_monitor.dialogs import MonitorDialogs
    from rdt_monitor.presentation import FrameProvider
    from rdt_monitor.replay import Image


class SetupModel(QObject):
    """Edit one owned frozen frame without changing the live controller.

    Args:
        provider (FrameProvider): Image provider registered under ``setup``.
        parent (QObject | None): Lifetime owner.
    """

    changed = Signal()
    finished = Signal()

    def __init__(self, provider: FrameProvider, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.dialogs: MonitorDialogs | None = None
        self._open = self._confirmed = False
        self._source: Image | None = None
        self._image_url = self._error = self._validation = self._use_error = ""
        self._selection: tuple[StoredRecipe, Path] | None = None
        self._recipe: ReplayRecipe | None = None
        self._values: dict[str, object] = {}
        self._regions: list[Region | None] = [None, None]
        self._coordinates = [[0.0] * 4, [0.0] * 4]
        self._active_role = 0
        self._use_references = True
        self._choice: SetupChoice | None = None

    @Property("QVariantMap", notify=changed)
    def status(self) -> dict[str, object]:
        """Return coherent setup-only values, validity and read-only model geometry."""
        detector = self._selection[0].detector if self._selection else None
        regions = [self._rectangle(region) for region in self._regions]
        background = [
            dict(
                left=region.left,
                top=region.top,
                width=region.width,
                height=region.height,
                role=role,
                label="Start" if role == 0 else "Position",
                colorRole="startMarker" if role == 0 else "positionMarker",
                selectable=True,
            )
            for role, region in enumerate(self._regions)
            if region is not None and role != self._active_role
        ]
        if detector is not None:
            region = detector.region
            background.append(
                dict(
                    left=region.left,
                    top=region.top,
                    width=region.width,
                    height=region.height,
                    role=-1,
                    label="Inspection ROI / trained",
                    colorRole="inspectionRoi",
                    selectable=False,
                )
            )
        return dict(
            open=self._open,
            imageSource=self._image_url,
            values=self._values.copy(),
            regions=regions,
            region=regions[self._active_role],
            coordinates=self._coordinates[self._active_role].copy(),
            role=self._active_role,
            backgroundRegions=background,
            confirmed=self._confirmed,
            valid=self._open and not self._validation and self._source is not None,
            canConfirm=self._open and not self._validation and not self._use_error,
            canUse=self._open and self._confirmed and not self._validation and not self._use_error,
            canSave=self._open and not self._validation and bool(str(self._values.get("recipeName", "")).strip()),
            error=self._error or self._validation or self._use_error,
            recipeFile=self._selection[1].name if self._selection else "Unsaved counting-only setup",
            bound=detector is not None,
            detector=f"Model SHA-256: {detector.model_sha256}\nTraining ROI and model are fixed by the family recipe."
            if detector
            else "Counting only / no defect detector. Use Collect and train to bind a model.",
        )

    @staticmethod
    def _rectangle(region: Region | None) -> list[float]:
        return [region.left, region.top, region.width, region.height] if region else []

    def begin(
        self, recipe: ReplayRecipe, selection: tuple[StoredRecipe, Path] | None, specimen: str, frame: CameraFrame
    ) -> None:
        """Begin a draft from an owned frozen snapshot, preserving the actual serial."""
        if self._open:
            raise RuntimeError("Setup is already open")
        self._source = frame.image.copy()
        self._source.flags.writeable = False
        self._recipe, self._selection = recipe, None
        self._values = dict(
            settings_values(recipe),
            specimenId=specimen,
            name="",
            batch="",
            family="",
            articleNumber="",
            recipeName="",
            notes="",
            recognition="appearance",
            threshold=0.5,
            margin=0.05,
        )
        self._regions = [None, None]
        self._coordinates = [[0.0] * 4, [0.0] * 4]
        self._active_role = 0
        self._error = ""
        self._choice = None
        self._confirmed = False
        self._use_references = True
        try:
            if selection is not None:
                self._load(*selection)
            self._image_url = self.provider.publish("preview", self._source).replace(
                "image://frames/", "image://setup/"
            )
            self._open = True
            self._update()
        except (OSError, ValueError):
            self._release()
            raise

    def confirm(
        self, recipe: ReplayRecipe, selection: tuple[StoredRecipe, Path] | None, specimen: str, frame: CameraFrame
    ) -> SetupChoice | None:
        """Run a Qt modal event loop while camera polling continues; return an explicit choice."""
        if self._open:
            raise RuntimeError("Setup is already open")
        loop = QEventLoop(self)
        self.finished.connect(loop.quit)
        try:
            self.begin(recipe, selection, specimen, frame)
            loop.exec()
            return self._choice
        finally:
            self.finished.disconnect(loop.quit)
            loop.deleteLater()
            self._release()

    def _release(self) -> None:
        self._source = None
        self._image_url = ""
        self._open = False
        self.provider.clear()
        self.changed.emit()

    def _load(self, stored: StoredRecipe, path: Path) -> None:
        height, width = self._source.shape[:2]
        if (stored.image_width, stored.image_height) != (width, height):
            raise ValueError("Recipe image dimensions differ from this frozen frame; configure a new recipe")
        limits = stored.recipe.synchronization
        if not (
            1 <= limits.stable_frames <= 120
            and 0.001 <= limits.max_gap_seconds <= 60
            and 0.01 <= limits.cycle_timeout_seconds <= 3600
        ):
            raise ValueError("Recipe timing is outside supported setup limits")
        if stored.detector is not None:
            stored.detector.settings(path)
        self._recipe, self._selection = stored.recipe, (stored, path)
        self._regions = [stored.recipe.marker, stored.recipe.position]
        self._coordinates = [self._rectangle(region) for region in self._regions]
        self._values.update(settings_values(stored.recipe))
        self._values.update(
            name=stored.specimen.name,
            batch=stored.specimen.batch,
            family=stored.specimen.family,
            articleNumber=stored.specimen.article_number,
            recipeName=stored.profile.name if stored.profile else path.stem,
            notes=stored.profile.notes if stored.profile else "",
            recognition="appearance" if stored.recipe.marker_appearance else "brightness",
            threshold=stored.detector.threshold if stored.detector else 0.5,
            margin=stored.detector.review_margin if stored.detector else 0.05,
        )
        self._use_references = True

    def _candidate(self) -> StoredRecipe:
        if self._source is None or any(region is None for region in self._regions):
            raise ValueError("Select both Start and Position marker regions")
        recipe = parse_settings(
            {**self._values, "start": self._rectangle(self._regions[0]), "position": self._rectangle(self._regions[1])},
            self._recipe,
        )
        limits = recipe.synchronization
        if not (
            1 <= limits.stable_frames <= 120
            and 0.001 <= limits.max_gap_seconds <= 60
            and 0.01 <= limits.cycle_timeout_seconds <= 3600
        ):
            raise ValueError("Use 1-120 stable frames, a 0.001-60 s gap and a 0.01-3600 s timeout")
        if self._values["recognition"] == "appearance":
            original = self._selection[0].recipe if self._selection else None
            if (
                self._use_references
                and original
                and original.marker_appearance
                and (recipe.marker, recipe.position) == (original.marker, original.position)
            ):
                recipe = replace(
                    recipe,
                    marker_appearance=original.marker_appearance,
                    position_appearance=original.position_appearance,
                )
            else:
                recipe = replace(
                    recipe,
                    marker_appearance=MarkerAppearance.capture(self._source, recipe.marker),
                    position_appearance=MarkerAppearance.capture(self._source, recipe.position),
                )
        elif self._values["recognition"] != "brightness":
            raise ValueError("Choose appearance or legacy brightness recognition")
        detector = self._selection[0].detector if self._selection else None
        if detector is not None:
            detector = replace(
                detector, threshold=float(self._values["threshold"]), review_margin=float(self._values["margin"])
            )
        return StoredRecipe(
            str(self._values["specimenId"]).strip(),
            recipe,
            self._source.shape[1],
            self._source.shape[0],
            detector,
            specimen=SpecimenDetails(
                str(self._values["name"]).strip(),
                str(self._values["batch"]).strip(),
                str(self._values["family"]).strip(),
                str(self._values["articleNumber"]).strip(),
            ),
        )

    def _choice_for_run(self, candidate: StoredRecipe) -> SetupChoice:
        if self._selection:
            original, _ = self._selection
            if candidate.detector != original.detector:
                raise ValueError("Save a new recipe revision before using changed decision settings")
            name = original.profile.name if original.profile else self._selection[1].stem
            notes = original.profile.notes if original.profile else ""
            if (str(self._values["recipeName"]).strip(), str(self._values["notes"]).strip()) != (name, notes):
                raise ValueError("Save a new recipe revision or discard changed name/purpose notes")
            specimen_setup(original, candidate.specimen_id, candidate.recipe, candidate.specimen)
        return SetupChoice(
            candidate.specimen_id,
            candidate.recipe,
            candidate.specimen,
            self._selection,
            candidate.image_width,
            candidate.image_height,
        )

    def _update(self) -> None:
        self._validation = self._use_error = ""
        if self._open:
            try:
                candidate = self._candidate()
            except (OSError, ValueError, TypeError) as error:
                self._validation = str(error)
            else:
                try:
                    self._choice_for_run(candidate)
                except ValueError as error:
                    self._use_error = str(error)
        self.changed.emit()

    @Slot(str, "QVariant")
    def editField(self, name: str, value: object) -> None:
        """Change an allowed draft field and require renewed confirmation."""
        if not self._open or name not in self._values or name in {"start", "position"}:
            return
        if self._values[name] == value:
            return
        self._values[name] = value
        self._confirmed = False
        self._error = ""
        self._update()

    @Slot(int)
    def selectRole(self, role: int) -> None:
        """Select a marker without changing its geometry or confirmation."""
        if role in (0, 1) and self._open:
            self._active_role = role
            self.changed.emit()

    @Slot(float, float, float, float)
    def setRegion(self, left: float, top: float, width: float, height: float) -> None:
        """Validate the selected normalized rectangle; invalid geometry clears it."""
        if not self._open:
            return
        if self._coordinates[self._active_role] == [left, top, width, height]:
            return
        self._regions[self._active_role] = None
        self._coordinates[self._active_role] = [left, top, width, height]
        self._confirmed = False
        self._error = ""
        try:
            self._regions[self._active_role] = Region(left, top, width, height)
        except ValueError as error:
            self._error = str(error)
        self._update()

    @Slot()
    def clearRegion(self) -> None:
        """Clear only the selected marker, never the trained inspection ROI."""
        if self._open:
            self._regions[self._active_role] = None
            self._coordinates[self._active_role] = [0.0] * 4
            self._confirmed = False
            self._update()

    @Slot()
    def refreshReferences(self) -> None:
        """Capture from the frozen image instead of reusing loaded mark references."""
        if self._open:
            self._use_references = self._confirmed = False
            self._update()

    @Slot(bool)
    def setConfirmed(self, confirmed: bool) -> None:
        """Confirm the current valid markers, inspection crop and test-cycle check."""
        self._confirmed = bool(confirmed and self._open and not self._validation and not self._use_error)
        self.changed.emit()

    @Slot()
    def chooseRecipe(self) -> None:
        """Load a recipe into the draft; cancellation preserves the current draft."""
        if self._open and self.dialogs is not None:
            path = self.dialogs.choose_recipe()
            if path is not None:
                self.loadRecipe(str(path))

    @Slot(str)
    def loadRecipe(self, path: str) -> None:
        """Validate dimensions and bundle integrity before replacing draft inputs."""
        if not self._open:
            return
        try:
            self._load(load_recipe(Path(path)), Path(path))
            self._confirmed = False
            self._error = ""
        except (OSError, ValueError) as error:
            self._error = f"Recipe not loaded: {error}"
        self._update()

    @Slot()
    def chooseRevision(self) -> None:
        """Choose a new recipe filename only for a valid named draft."""
        if self._open and self.status["canSave"] and self.dialogs is not None:
            path = self.dialogs.choose_recipe_revision()
            if path is not None:
                self.saveRevision(str(path))

    @Slot(str)
    def saveRevision(self, path: str) -> None:
        """Write a fresh family revision with the existing no-overwrite/bundle rules."""
        if not self._open:
            return
        try:
            candidate = self._candidate()
            original = self._selection[0] if self._selection else None
            source = self._selection[1] if self._selection else None
            if source and load_recipe(source) != original:
                raise ValueError("Reusable recipe changed during setup; load it again")
            profile = RecipeProfile(
                original.profile.family_id if original and original.profile else uuid4().hex,
                uuid4().hex,
                str(self._values["recipeName"]).strip(),
                str(self._values["notes"]).strip(),
            )
            stored = replace(candidate, profile=profile)
            save_managed_recipe(Path(path), stored, source=source)
            self._load(stored, Path(path))
            self._confirmed = False
            self._error = ""
        except (OSError, ValueError, TypeError) as error:
            self._error = f"Recipe not saved: {error}"
        self._update()

    @Slot()
    def accept(self) -> None:
        """Finish only an explicitly confirmed valid setup; the controller commits it later."""
        if not self._open:
            return
        try:
            if not self._confirmed:
                raise ValueError("Confirm the markers, inspection crop and test-cycle check first")
            self._choice = self._choice_for_run(self._candidate())
        except (OSError, ValueError, TypeError) as error:
            self._error = str(error)
            self._update()
            return
        self._open = False
        self.changed.emit()
        self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        """Cancel without starting, saving a run, or changing controller configuration."""
        if self._open:
            self._choice = None
            self._open = False
            self._confirmed = False
            self.changed.emit()
            self.finished.emit()

    def dispose(self) -> None:
        """Release retained frozen pixels and unblock any modal setup loop."""
        self.cancel()
        self._release()
