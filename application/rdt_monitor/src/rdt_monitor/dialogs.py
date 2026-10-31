# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Native recipe/setup dialogs shared with the transitional Qt Quick window."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QWindow
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget

from rdt_monitor.app import ReplaySettings, _apply_display_mode
from rdt_monitor.camera import CameraFrame
from rdt_monitor.paths import RECIPE_DIRECTORY
from rdt_monitor.recipes import SpecimenDetails, StoredRecipe
from rdt_monitor.replay import ReplayRecipe
from rdt_monitor.setup_choice import SetupChoice

if TYPE_CHECKING:
    from rdt_monitor.setup_presentation import SetupModel


class MonitorDialogs:
    """Retain validated setup editing while its QML replacement is developed.

    Args:
        window (QWindow): Qt Quick window to own transient application-modal dialogs.
    """

    def __init__(self, window: QWindow) -> None:
        self.window = window

    def apply_theme(self, light: bool) -> None:
        """Apply the same workstation theme to transitional Widgets dialogs."""
        _apply_display_mode("light" if light else "dark")

    def choose_recipe(self) -> Path | None:
        """Open a native recipe picker without loading or activating its selection.

        Returns:
            Path | None: Explicit file choice, or None after cancellation.
        """
        dialog = QFileDialog(None, "Load recipe", str(RECIPE_DIRECTORY), "JSON recipe (*.json)")
        try:
            self._prepare(dialog)
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            return Path(dialog.selectedFiles()[0]) if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def choose_recipe_folder(self, initial: str) -> Path | None:
        """Choose a local folder containing recipe JSON files."""
        dialog = QFileDialog(None, "Choose recipe folder", initial)
        try:
            self._prepare(dialog)
            dialog.setFileMode(QFileDialog.FileMode.Directory)
            dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
            return Path(dialog.selectedFiles()[0]) if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def choose_collection_folder(self, initial: str) -> Path | None:
        """Choose a directory for generated candidate sessions."""
        dialog = QFileDialog(None, "Collection output directory", initial)
        try:
            self._prepare(dialog)
            dialog.setFileMode(QFileDialog.FileMode.Directory)
            dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
            return Path(dialog.selectedFiles()[0]) if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def choose_training_python(self, initial: str = "") -> Path | None:
        """Choose a trusted training interpreter without running it automatically."""
        return self._choose_file(
            "Trusted Anomalib training Python", initial, "Python (python.exe python3.exe);;Executables (*.exe)"
        )

    def choose_collection(self, initial: str = "") -> Path | None:
        """Choose persisted candidates for explicit review."""
        return self._choose_file("Load candidate collection", initial, "Collection (collection.json)")

    def choose_training_export(self, initial: str) -> Path | None:
        """Choose this collection's exported OpenVINO model."""
        return self._choose_file("Select collection training export", initial, "OpenVINO model (*.xml)")

    def choose_image(self) -> Path | None:
        """Choose an offline image without changing camera or recipe inputs."""
        return self._choose_file("Open offline specimen image", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")

    def choose_recording(self) -> Path | None:
        """Choose a local replay file without opening a decoder or starting a run."""
        return self._choose_file("Open local recording", "", "Video (*.mp4 *.avi *.mov *.mkv);;All files (*)")

    def choose_model(self) -> Path | None:
        """Choose a trusted exported detector; model loading happens in a worker."""
        return self._choose_file("Load trusted Anomalib export", "", "Anomalib export (*.xml *.onnx)")

    def choose_snapshot(self) -> Path | None:
        """Choose an explicitly overwritten manual snapshot destination."""
        return self._choose_file("Save offline snapshot", "inspection.png", "PNG image (*.png)", save=True)

    def choose_recipe_revision(self) -> Path | None:
        """Choose a new JSON recipe filename; the managed writer rejects overwrite."""
        return self._choose_file(
            "Save new recipe revision",
            str(RECIPE_DIRECTORY / "synchronization-recipe.json"),
            "JSON recipe (*.json)",
            save=True,
            suffix="json",
        )

    def _choose_file(
        self, title: str, initial: str, filters: str, *, save: bool = False, suffix: str = "png"
    ) -> Path | None:
        dialog = QFileDialog(None, title, initial, filters)
        try:
            self._prepare(dialog)
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave if save else QFileDialog.AcceptMode.AcceptOpen)
            dialog.setFileMode(QFileDialog.FileMode.AnyFile if save else QFileDialog.FileMode.ExistingFile)
            if save:
                dialog.setDefaultSuffix(suffix)
                if suffix == "json":
                    dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, True)
            return Path(dialog.selectedFiles()[0]) if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def confirm_setup(
        self,
        recipe: ReplayRecipe,
        selection: tuple[StoredRecipe, Path] | None,
        specimen_id: str,
        frame: CameraFrame,
    ) -> SetupChoice | None:
        """Edit a frozen image and return values only after explicit operator confirmation.

        Args:
            recipe (ReplayRecipe): Current synchronization settings.
            selection (tuple[StoredRecipe, Path] | None): Loaded reusable recipe.
            specimen_id (str): Actual specimen serial, preserved independently of recipe identity.
            frame (CameraFrame): Fresh setup snapshot; capture continues independently.

        Returns:
            SetupChoice | None: Confirmed inputs, or None when canceled.

        Raises:
            ValueError: If a loaded recipe cannot be represented on this source image.
        """
        pixels = frame.image
        image = QImage(
            pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_BGR888
        ).copy()
        dialog = ReplaySettings(recipe, None, camera_image=image, specimen_id=specimen_id)
        try:
            self._prepare(dialog)
            if selection is not None and not dialog._apply_stored_recipe(*selection):
                raise ValueError(dialog._error.text())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return SetupChoice(
                dialog.specimen_id,
                dialog.recipe,
                SpecimenDetails(dialog.specimen_name.text().strip(), dialog.specimen_batch.text().strip()),
                dialog._stored_recipe or selection,
                image.width(),
                image.height(),
            )
        finally:
            dialog.deleteLater()

    def _prepare(self, dialog: QWidget) -> None:
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.winId()
        dialog.windowHandle().setTransientParent(self.window)
        screen = self.window.screen()
        if screen is not None:
            dialog.windowHandle().setScreen(screen)
            available = screen.availableGeometry().adjusted(12, 12, -12, -48)
            dialog.setMinimumSize(
                min(dialog.minimumWidth(), available.width()), min(dialog.minimumHeight(), available.height())
            )
            dialog.resize(min(dialog.width(), available.width()), min(dialog.height(), available.height()))
            frame = dialog.frameGeometry()
            center = self.window.frameGeometry().center()
            dialog.move(
                max(available.left(), min(center.x() - frame.width() // 2, available.right() - frame.width() + 1)),
                max(available.top(), min(center.y() - frame.height() // 2, available.bottom() - frame.height() + 1)),
            )


class QmlMonitorDialogs(MonitorDialogs):
    """Use QML for setup while retaining native file pickers and their modal ownership."""

    def __init__(self, window: QWindow, setup: SetupModel) -> None:
        super().__init__(window)
        self.setup = setup

    def confirm_setup(
        self, recipe: ReplayRecipe, selection: tuple[StoredRecipe, Path] | None, specimen_id: str, frame: CameraFrame
    ) -> SetupChoice | None:
        """Present the QML frozen-frame editor without instantiating Widgets setup."""
        return self.setup.confirm(recipe, selection, specimen_id, frame)
