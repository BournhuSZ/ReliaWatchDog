# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Industrial desktop replay workbench; production monitoring remains unavailable."""

import json
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from queue import Empty
from time import monotonic
from uuid import uuid4

import cv2
import numpy as np
from PySide6.QtCore import (
    QPointF,
    QProcess,
    QProcessEnvironment,
    QRect,
    QRectF,
    QSettings,
    QSignalBlocker,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rdt_monitor.camera import CameraFrame, CameraPreview, MarkerTrial
from rdt_monitor.collection import (
    CandidateCollection,
    CollectionDocument,
    CollectionSettings,
    load_document,
    local_file,
    metadata_digest,
)
from rdt_monitor.controller import InspectionPoll, MonitorController, ObservationUpdate, SourcePoll
from rdt_monitor.detection import (
    DetectionFailure,
    DetectionResult,
    DetectionSettings,
    ImageImportWorker,
    InspectionWorker,
)
from rdt_monitor.domain import SyncConfig, SyncResult, SyncState
from rdt_monitor.lifecycle import InspectionOutcome, Readiness, RunMode, RunState
from rdt_monitor.live import LiveInspection
from rdt_monitor.palette import colors as display_colors
from rdt_monitor.paths import INSPECTION_DIRECTORY, RECIPE_DIRECTORY
from rdt_monitor.recipes import (
    RecipeProfile,
    SpecimenDetails,
    StoredRecipe,
    load_recipe,
    save_inspection_recipe,
    save_managed_recipe,
    specimen_setup,
)
from rdt_monitor.rendering import inspection_overlay as _inspection_overlay
from rdt_monitor.replay import (
    Image,
    MarkerAppearance,
    Preview,
    Region,
    ReplayPipeline,
    ReplayRecipe,
    fixture_recipe,
)


def _fit_dialog_to_screen(
    dialog: QDialog,
    preferred_size: tuple[int, int],
    minimum_size: tuple[int, int],
) -> None:
    """Size and center a dialog within the owning Windows screen's usable area."""
    parent = dialog.parentWidget()
    screen = parent.screen() if parent is not None else QApplication.primaryScreen()
    if screen is None:
        return
    available = screen.availableGeometry().adjusted(12, 12, -12, -12)
    width = min(preferred_size[0], available.width())
    height = min(preferred_size[1], available.height())
    dialog.setMinimumSize(min(minimum_size[0], width), min(minimum_size[1], height))
    dialog.resize(width, height)
    dialog.move(available.center() - dialog.rect().center())


class _VerdictLabel(QLabel):
    """Render the latest inspection as a static signal, never a station-health claim."""

    def heightForWidth(self, width: int) -> int:
        return self.minimumHeight()

    def paintEvent(self, event: QPaintEvent) -> None:
        tokens = display_colors(QApplication.instance().property("display_mode") == "light")
        colors = {
            "grey": tokens["neutral"],
            "green": tokens["normal"],
            "yellow": tokens["review"],
            "red": tokens["defect"],
        }
        color = str(self.property("verdict_color") or "grey")
        foreground = QColor(colors[color])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        diameter = min(self.width() - 16, self.height() - 32, 176)
        ring = QRectF((self.width() - diameter) / 2, 5, diameter, diameter)
        painter.setBrush(QColor(tokens["verdictSurface"]))
        painter.setPen(QPen(foreground, 2.5))
        painter.drawEllipse(ring)
        center = ring.center()
        painter.setPen(QPen(foreground, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        symbol = QPainterPath()
        y = ring.top() + diameter * 0.25
        if color == "green":
            symbol.moveTo(center.x() - 13, y)
            symbol.lineTo(center.x() - 3, y + 10)
            symbol.lineTo(center.x() + 15, y - 10)
        elif color == "red":
            symbol.moveTo(center.x() - 10, y - 10)
            symbol.lineTo(center.x() + 10, y + 10)
            symbol.moveTo(center.x() + 10, y - 10)
            symbol.lineTo(center.x() - 10, y + 10)
        elif color == "yellow":
            symbol.moveTo(center.x(), y - 13)
            symbol.lineTo(center.x(), y + 2)
            symbol.moveTo(center.x(), y + 11)
            symbol.lineTo(center.x(), y + 12)
        else:
            symbol.moveTo(center.x() - 12, y)
            symbol.lineTo(center.x() + 12, y)
        painter.drawPath(symbol)
        title = {"green": "NORMAL", "yellow": "NEEDS REVIEW", "red": "DEFECT", "grey": "NOT INSPECTED"}[color]
        if color == "grey" and "Counting only" in self.text():
            title = "COUNTING ONLY"
        font = QFont(self.font())
        compact = diameter < 160
        font.setPixelSize((17 if compact else 21) if color in {"green", "red"} else (11 if compact else 15))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(foreground)
        painter.drawText(
            QRectF(ring.left() + 6, ring.top() + diameter * 0.45, diameter - 12, 26),
            Qt.AlignmentFlag.AlignCenter,
            title,
        )
        font.setPixelSize(9 if compact else 11)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.drawText(
            QRectF(ring.left() + 6, ring.top() + diameter * 0.65, diameter - 12, 18),
            Qt.AlignmentFlag.AlignCenter,
            "LATEST INSPECTION" if color != "grey" else "NO VALID VERDICT",
        )
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(tokens["secondary"]))
        detail = self.text().partition(" - ")[0] if self.text().startswith("Cycle ") else "No inspection result"
        painter.drawText(QRectF(0, ring.bottom() + 6, self.width(), 20), Qt.AlignmentFlag.AlignCenter, detail)


class LiveView(QWidget):
    """Aspect-preserving preview with source-aligned marker and position regions."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image = QImage()
        self._recipe: ReplayRecipe | None = fixture_recipe()
        self.inspection_region: Region | None = None

    def display(self, preview: Preview, recipe: ReplayRecipe) -> None:
        """Copy source pixels before handing ownership to Qt rendering.

        Args:
            preview (Preview): Exact source frame and its synchronization result.
            recipe (ReplayRecipe): Matching frozen region definitions.
        """
        self._display_image(preview.frame.image)
        self._recipe = recipe
        self.update()

    def display_camera(self, frame: CameraFrame, recipe: ReplayRecipe | None = None) -> None:
        """Display camera pixels with only explicitly selected trial outlines.

        Args:
            frame (CameraFrame): Exact live frame and host-read completion time.
            recipe (ReplayRecipe | None): Locked trial regions, or no overlays.
        """
        self._display_image(frame.image)
        self._recipe = recipe
        self.update()

    def _display_image(self, image: Image) -> None:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        self._image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()

    def clear(self) -> None:
        """Remove the previous run's image so it cannot appear live on restart."""
        self._image = QImage()
        self.update()

    def image_rect(self) -> QRectF:
        """Return the actual displayed image bounds, excluding letterbox margins.

        Returns:
            QRectF: Centered image rectangle, or empty bounds without an image.
        """
        if self._image.isNull():
            return QRectF()
        size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(
            (self.width() - size.width()) / 2, (self.height() - size.height()) / 2, size.width(), size.height()
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        """Render letterboxed pixels and outlines without changing widget geometry.

        Args:
            event (QPaintEvent): Qt paint notification.
        """
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#08090C"))
        if self._image.isNull():
            painter.setPen(QColor("#9AA0A6"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No source frame")
            return
        target = self.image_rect()
        painter.drawImage(target, self._image)
        self._paint_regions(painter, target)

    def _paint_regions(self, painter: QPainter, target: QRectF) -> None:
        regions = []
        if self._recipe is not None:
            regions.extend(
                (
                    (self._recipe.marker, "#3B82F6", "START MARKER"),
                    (self._recipe.position, "#EAB308", "POSITION MARKER"),
                )
            )
        if self.inspection_region is not None:
            regions.append((self.inspection_region, "#22D3EE", "INSPECTION ROI"))
        for region, color, label in regions:
            rectangle = QRectF(
                target.left() + region.left * target.width(),
                target.top() + region.top * target.height(),
                region.width * target.width(),
                region.height * target.height(),
            )
            painter.setPen(QPen(QColor(color), 2))
            painter.drawRect(rectangle)
            painter.drawText(QPointF(rectangle.left(), max(target.top() + 14, rectangle.top() - 6)), label)


class RegionEditor(LiveView):
    """Frozen-image region editor with source-aligned drawing, moving, and resizing."""

    regionChanged = Signal(int, object)
    selectionChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.regions: tuple[Region | None, Region | None] = (None, None)
        self.active_role = 0
        self.single_region = False
        self.region_labels = ("START", "POSITION")
        self._mode = ""
        self._handle = ""
        self._origin = QPointF()
        self._original = QRectF()
        self._pending: QRectF | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName("Frozen camera marker region editor")

    def select_region(self, role: int) -> None:
        """Select Start (zero) or Position (one) for drawing and overlap priority.

        Args:
            role (int): Region role, zero or one.

        Raises:
            ValueError: If the role is unknown.
        """
        if role not in (0, 1):
            raise ValueError("Region role must be Start or Position")
        self.active_role = role
        self.selectionChanged.emit(role)
        self.update()

    def clear_selected(self) -> None:
        """Clear only the selected region, leaving the other region unchanged."""
        self.regionChanged.emit(self.active_role, None)

    def _rectangle(self, role: int) -> QRectF:
        region = self.regions[role]
        return QRectF(region.left, region.top, region.width, region.height) if region is not None else QRectF()

    def _screen_rect(self, region: QRectF) -> QRectF:
        target = self.image_rect()
        return QRectF(
            target.left() + region.left() * target.width(),
            target.top() + region.top() * target.height(),
            region.width() * target.width(),
            region.height() * target.height(),
        )

    def _handles(self, rectangle: QRectF) -> dict[str, QPointF]:
        return {
            "nw": rectangle.topLeft(),
            "n": QPointF(rectangle.center().x(), rectangle.top()),
            "ne": rectangle.topRight(),
            "e": QPointF(rectangle.right(), rectangle.center().y()),
            "se": rectangle.bottomRight(),
            "s": QPointF(rectangle.center().x(), rectangle.bottom()),
            "sw": rectangle.bottomLeft(),
            "w": QPointF(rectangle.left(), rectangle.center().y()),
        }

    def _hit_handle(self, position: QPointF) -> str:
        if self.regions[self.active_role] is None:
            return ""
        for name, center in self._handles(self._screen_rect(self._rectangle(self.active_role))).items():
            if QRectF(center.x() - 7, center.y() - 7, 14, 14).contains(position):
                return name
        return ""

    def _can_commit(self, rectangle: QRectF) -> bool:
        return rectangle.width() >= min(1, 2 / self._image.width()) and rectangle.height() >= min(
            1, 2 / self._image.height()
        )

    def _point(self, position: QPointF) -> QPointF:
        target = self.image_rect()
        return QPointF(
            min(1, max(0, (position.x() - target.left()) / target.width())),
            min(1, max(0, (position.y() - target.top()) / target.height())),
        )

    def _paint_regions(self, painter: QPainter, target: QRectF) -> None:
        painter.save()
        painter.setClipRect(target)
        labels: list[QRectF] = []
        for role in (1 - self.active_role, self.active_role):
            color = "#3B82F6" if role == 0 else "#EAB308"
            label = self.region_labels[role]
            rectangle = (
                self._pending if role == self.active_role and self._pending is not None else self._rectangle(role)
            )
            if rectangle.isEmpty():
                continue
            screen = self._screen_rect(rectangle)
            painter.setPen(QPen(QColor(color), 2))
            painter.drawRect(screen)
            label_width = painter.fontMetrics().horizontalAdvance(label) + 10
            label_rect = QRectF(
                min(screen.left(), target.right() - label_width),
                max(target.top(), screen.top() - 23),
                label_width,
                21,
            )
            for previous in labels:
                if label_rect.intersects(previous):
                    label_rect.moveTop(min(target.bottom() - 21, previous.bottom() + 3))
            labels.append(label_rect)
            painter.fillRect(label_rect, QColor("#0F1115"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
            if role == self.active_role:
                for center in self._handles(screen).values():
                    painter.fillRect(QRectF(center.x() - 4, center.y() - 4, 8, 8), QColor(color))
        painter.restore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin an image-confined drawing, move, or resize gesture.

        Args:
            event (QMouseEvent): Native pointer press.
        """
        if event.button() != Qt.MouseButton.LeftButton or not self.image_rect().contains(event.position()):
            return
        self._handle = self._hit_handle(event.position())
        self._mode = "resize" if self._handle else "draw"
        if not self._handle and self.regions[self.active_role] is not None:
            for role in (self.active_role, 1 - self.active_role):
                if self.regions[role] is not None and self._screen_rect(self._rectangle(role)).contains(
                    event.position()
                ):
                    self.select_region(role)
                    self._mode = "move"
                    break
        self._origin = self._point(event.position())
        self._original = self._rectangle(self.active_role)
        self._pending = QRectF(self._origin, self._origin) if self._mode == "draw" else QRectF(self._original)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Update geometry in image coordinates, clamping gestures at image edges.

        Args:
            event (QMouseEvent): Native pointer movement.
        """
        if not self._mode:
            handle = self._hit_handle(event.position())
            cursor = Qt.CursorShape.CrossCursor
            if handle:
                cursor = {
                    "nw": Qt.CursorShape.SizeFDiagCursor,
                    "se": Qt.CursorShape.SizeFDiagCursor,
                    "ne": Qt.CursorShape.SizeBDiagCursor,
                    "sw": Qt.CursorShape.SizeBDiagCursor,
                    "n": Qt.CursorShape.SizeVerCursor,
                    "s": Qt.CursorShape.SizeVerCursor,
                    "e": Qt.CursorShape.SizeHorCursor,
                    "w": Qt.CursorShape.SizeHorCursor,
                }[handle]
            elif any(
                region is not None and self._screen_rect(self._rectangle(role)).contains(event.position())
                for role, region in enumerate(self.regions)
            ):
                cursor = Qt.CursorShape.SizeAllCursor
            self.setCursor(cursor)
            return
        point = self._point(event.position())
        rectangle = QRectF(self._original)
        if self._mode == "draw":
            rectangle = QRectF(self._origin, point).normalized()
        elif self._mode == "move":
            rectangle.moveLeft(min(1 - rectangle.width(), max(0, rectangle.left() + point.x() - self._origin.x())))
            rectangle.moveTop(min(1 - rectangle.height(), max(0, rectangle.top() + point.y() - self._origin.y())))
        else:
            minimum_width = min(rectangle.width(), 2 / self._image.width())
            minimum_height = min(rectangle.height(), 2 / self._image.height())
            if "w" in self._handle:
                rectangle.setLeft(min(point.x(), rectangle.right() - minimum_width))
            if "e" in self._handle:
                rectangle.setRight(max(point.x(), rectangle.left() + minimum_width))
            if "n" in self._handle:
                rectangle.setTop(min(point.y(), rectangle.bottom() - minimum_height))
            if "s" in self._handle:
                rectangle.setBottom(max(point.y(), rectangle.top() + minimum_height))
        self._pending = rectangle
        if self._can_commit(rectangle):
            self.regionChanged.emit(
                self.active_role, Region(rectangle.left(), rectangle.top(), rectangle.width(), rectangle.height())
            )
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Commit the gesture and advance from a newly drawn Start to Position.

        Args:
            event (QMouseEvent): Native pointer release.
        """
        if event.button() != Qt.MouseButton.LeftButton or not self._mode:
            return
        self.mouseMoveEvent(event)
        if self._mode == "draw" and self._pending is not None:
            target = self._screen_rect(self._pending)
            other_role = 1 - self.active_role
            if target.width() < 4 and target.height() < 4 and self.regions[other_role] is not None:
                if self._screen_rect(self._rectangle(other_role)).contains(event.position()):
                    self.select_region(other_role)
        advance = (
            self._mode == "draw"
            and not self.single_region
            and self.active_role == 0
            and self.regions[0] is not None
            and self._pending is not None
            and self._can_commit(self._pending)
        )
        self._mode = ""
        self._pending = None
        if advance:
            self.select_region(1)
        self.update()


class ReplaySettings(QDialog):
    """Development-only marker settings; no supervisor or qualification claim.

    Args:
        recipe (ReplayRecipe): Current unqualified settings.
        parent (QWidget): Owning window.
        camera_image (QImage | None): Frozen camera setup frame; requires explicit regions and confirmation.
        specimen_id (str): Specimen identity associated with saved camera settings.
    """

    def __init__(
        self, recipe: ReplayRecipe, parent: QWidget | None, *, camera_image: QImage | None = None, specimen_id: str = ""
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cycle synchronization setup" if camera_image is not None else "Replay settings")
        self.recipe = recipe
        self.specimen_id = specimen_id
        self.run_setup: StoredRecipe | None = None
        self._recipe_path: Path | None = None
        self._stored_recipe: tuple[StoredRecipe, Path] | None = None
        self._loaded_references: tuple[MarkerAppearance, MarkerAppearance] | None = None
        self._reference_geometry: tuple[Region, Region] | None = None
        self.recognition_mode: QComboBox | None = None
        layout = QVBoxLayout(self)
        self.preview: RegionEditor | None = None
        self._confirmed: QCheckBox | None = None
        self.role_buttons: list[QPushButton] = []
        settings_layout = layout
        if camera_image is not None:
            self.resize(1000, 680)
            self.setMinimumSize(820, 540)
            workspace = QHBoxLayout()
            layout.addLayout(workspace, 1)
            image_layout = QVBoxLayout()
            workspace.addLayout(image_layout, 1)
            image_layout.addWidget(QLabel("FROZEN CAMERA FRAME / synchronization setup"))
            self.preview = RegionEditor()
            self.preview.setMinimumSize(320, 260)
            self.preview._image = camera_image.copy()
            self.preview._recipe = None
            role_bar = QHBoxLayout()
            self._role_group = QButtonGroup(self)
            for role, name in enumerate(("Start marker", "Position marker")):
                button = QPushButton(name)
                button.setCheckable(True)
                button.setProperty("regionRole", "start" if role == 0 else "position")
                button.setToolTip(f"Select {name.lower()}")
                button.clicked.connect(lambda checked, role=role: self.preview.select_region(role))
                self._role_group.addButton(button, role)
                self.role_buttons.append(button)
                role_bar.addWidget(button)
            self.role_buttons[0].setChecked(True)
            self.clear_region_button = QToolButton()
            self.clear_region_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            self.clear_region_button.setToolTip("Clear selected region")
            self.clear_region_button.setAccessibleName("Clear selected region")
            self.clear_region_button.setEnabled(False)
            self.clear_region_button.clicked.connect(self.preview.clear_selected)
            role_bar.addWidget(self.clear_region_button)
            role_bar.addStretch()
            image_layout.addLayout(role_bar)
            image_layout.addWidget(self.preview, 1)
            side = QWidget()
            side.setFixedWidth(340)
            settings_layout = QVBoxLayout(side)
            settings_layout.setContentsMargins(8, 0, 0, 0)
            workspace.addWidget(side)
            self.recipe_identity = QLabel(f"Specimen: {self.specimen_id or '-'}")
            self.recipe_identity.setTextFormat(Qt.TextFormat.PlainText)
            self.recipe_identity.setWordWrap(True)
            settings_layout.addWidget(self.recipe_identity)
            self.setup_tabs = QTabWidget()
            settings_layout.addWidget(self.setup_tabs)
            basic, advanced = QWidget(), QWidget()
            self.setup_tabs.addTab(basic, "Basic")
            self.setup_tabs.addTab(advanced, "Advanced")
            settings_layout = QVBoxLayout(basic)
            self._advanced_layout = QVBoxLayout(advanced)
            self.advanced_panel = QWidget()
            advanced_scroll = QScrollArea()
            advanced_scroll.setWidgetResizable(True)
            advanced_scroll.setFrameShape(QFrame.Shape.NoFrame)
            advanced_scroll.setWidget(self.advanced_panel)
            self._advanced_layout.addWidget(advanced_scroll)
            self.advanced_layout = QVBoxLayout(self.advanced_panel)
            identity_form = QFormLayout()
            self.specimen_input = QLineEdit(specimen_id)
            self.specimen_input.setMaxLength(128)
            self.specimen_name, self.specimen_batch = QLineEdit(), QLineEdit()
            self.specimen_name.setMaxLength(128)
            self.specimen_batch.setMaxLength(128)
            identity_form.addRow("Specimen serial", self.specimen_input)
            identity_form.addRow("Name (optional)", self.specimen_name)
            identity_form.addRow("Batch (optional)", self.specimen_batch)
            settings_layout.addLayout(identity_form)
            self.specimen_input.textChanged.connect(self._identity_changed)
            self.specimen_name.textChanged.connect(self._settings_changed)
            self.specimen_batch.textChanged.connect(self._settings_changed)
            self.purpose_label = QLabel("Choose a named recipe, then check this specimen's setup.")
            self.purpose_label.setWordWrap(True)
            self.purpose_label.setTextFormat(Qt.TextFormat.PlainText)
            settings_layout.addWidget(self.purpose_label)
            metadata = QFormLayout()
            self.recipe_name = QLineEdit()
            self.recipe_name.setMaxLength(128)
            self.recipe_name.setPlaceholderText("e.g. A-type standard bending")
            self.recipe_notes = QPlainTextEdit()
            self.recipe_notes.setMaximumHeight(80)
            metadata.addRow("Recipe name", self.recipe_name)
            metadata.addRow("Purpose notes", self.recipe_notes)
            self.advanced_layout.addLayout(metadata)
            self.detector_description = QLabel("No detector selected. Use Collect and train to bind a trained model.")
            self.detector_description.setTextFormat(Qt.TextFormat.PlainText)
            self.detector_description.setWordWrap(True)
            self.advanced_layout.addWidget(self.detector_description)
            decisions = QFormLayout()
            self.detector_threshold, self.detector_margin = QDoubleSpinBox(), QDoubleSpinBox()
            self._decision_baseline = (0.5, 0.05)
            self.detector_threshold.setRange(0.001, 0.999)
            self.detector_margin.setRange(0, 0.5)
            for spin, value in ((self.detector_threshold, 0.5), (self.detector_margin, 0.05)):
                spin.setDecimals(3)
                spin.setValue(value)
                spin.setEnabled(False)
            decisions.addRow("Threshold", self.detector_threshold)
            decisions.addRow("Review margin", self.detector_margin)
            self.advanced_layout.addLayout(decisions)
            recipe_controls = QHBoxLayout()
            self.load_recipe_button = QPushButton("Load recipe")
            self.load_recipe_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
            self.load_recipe_button.setToolTip("Load specimen and synchronization settings from JSON")
            self.load_recipe_button.clicked.connect(self._load_recipe)
            self.store_recipe_button = QPushButton("Save recipe")
            self.store_recipe_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
            self.store_recipe_button.setToolTip("Save specimen and synchronization settings to JSON")
            self.store_recipe_button.clicked.connect(self._store_recipe)
            recipe_controls.addWidget(self.load_recipe_button)
            self.advanced_layout.addWidget(self.store_recipe_button)
            settings_layout.addLayout(recipe_controls)
            self.recipe_file = QLabel("Recipe: unsaved")
            self.recipe_file.setTextFormat(Qt.TextFormat.PlainText)
            self.recipe_file.setWordWrap(True)
            settings_layout.addWidget(self.recipe_file)
            self.refresh_reference_button = QPushButton("Use current marker appearance")
            self.refresh_reference_button.clicked.connect(self._refresh_marker_references)
            settings_layout.addWidget(self.refresh_reference_button)
            instruction = QLabel(
                "Check both markers and the cyan inspection ROI after every remount. "
                "Marker adjustment does not align the inspected surface. Exercise test cycles before monitoring."
            )
            instruction.setWordWrap(True)
            settings_layout.addWidget(instruction)
        self._coordinate_panel = QWidget()
        coordinate_form = QFormLayout(self._coordinate_panel)
        coordinate_form.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        if self.preview is not None:
            self.recognition_mode = QComboBox()
            self.recognition_mode.addItems(["Mark appearance", "Brightness (legacy)"])
            self.recognition_mode.setToolTip(
                "Appearance uses the marks in the frozen image; brightness is diagnostic only"
            )
            form.addRow("Recognition", self.recognition_mode)
        self._regions: list[list[QDoubleSpinBox]] = []
        for title, region in (("Start marker", recipe.marker), ("Position marker", recipe.position)):
            row = QGridLayout()
            inputs = []
            for index, (name, value) in enumerate(
                zip(("X", "Y", "W", "H"), (region.left, region.top, region.width, region.height), strict=True)
            ):
                row_index, column = (index // 2, index % 2 * 2) if camera_image is not None else (0, index * 2)
                row.addWidget(QLabel(name), row_index, column)
                spin = QDoubleSpinBox()
                spin.setRange(0, 100)
                spin.setDecimals(4 if camera_image is not None else 1)
                spin.setSuffix(" %")
                spin.setValue(value * 100 if camera_image is None else 0)
                spin.setAccessibleName(f"{title} {name} percent")
                row.addWidget(spin, row_index, column + 1)
                inputs.append(spin)
            self._regions.append(inputs)
            if camera_image is not None:
                title_label = QLabel(title)
                title_label.setObjectName("startRegionLabel" if len(self._regions) == 1 else "positionRegionLabel")
                coordinate_form.addRow(title_label)
                coordinate_form.addRow(row)
            else:
                coordinate_form.addRow(title, row)
        if self.preview is None:
            settings_layout.addWidget(self._coordinate_panel)
        self._brightness = QSpinBox()
        self._brightness.setRange(0, 255)
        self._brightness.setValue(recipe.brightness)
        form.addRow("Bright pixel cutoff (0-255)", self._brightness)
        self._stable = QSpinBox()
        self._stable.setRange(1, 120)
        self._stable.setValue(recipe.synchronization.stable_frames)
        form.addRow("Stable observations (frames)", self._stable)
        self._gap = QDoubleSpinBox()
        self._gap.setRange(0.001, 60)
        self._gap.setDecimals(3)
        self._gap.setSuffix(" s")
        self._gap.setValue(recipe.synchronization.max_gap_seconds)
        form.addRow("Observation gap limit", self._gap)
        self._timeout = QDoubleSpinBox()
        self._timeout.setRange(0.01, 3600)
        self._timeout.setSuffix(" s")
        self._timeout.setValue(recipe.synchronization.cycle_timeout_seconds)
        form.addRow("Cycle-start timeout", self._timeout)
        technical_layout = self.advanced_layout if self.preview is not None else settings_layout
        technical_layout.addLayout(form)
        if self.preview is not None:
            self.advanced_button = QToolButton()
            self.advanced_button.setText("Advanced coordinates")
            self.advanced_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.advanced_button.setArrowType(Qt.ArrowType.RightArrow)
            self.advanced_button.setCheckable(True)
            self.advanced_button.toggled.connect(self._toggle_coordinates)
            technical_layout.addWidget(self.advanced_button)
            technical_layout.addWidget(self._coordinate_panel)
            self._coordinate_panel.hide()
            settings_layout.addStretch()
            self.preview.regionChanged.connect(self._canvas_changed)
            self.preview.selectionChanged.connect(self._select_role)
            self._confirmed = QCheckBox("Setup and test cycles checked / counting only")
            self._confirmed.toggled.connect(self._validate)
            layout.addWidget(self._confirmed)
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setObjectName("warning")
        self._error.setMinimumHeight(36)
        layout.addWidget(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        if self.preview is not None:
            self.save_button.setText("Use for this run")
        layout.addWidget(buttons)
        for inputs in self._regions:
            for spin in inputs:
                spin.valueChanged.connect(self._settings_changed)
        for spin in (self._brightness, self._stable, self._gap, self._timeout):
            spin.valueChanged.connect(self._settings_changed)
        if self.recognition_mode is not None:
            self.recognition_mode.currentIndexChanged.connect(self._recognition_changed)
            self._brightness.setEnabled(False)
        if self.preview is not None:
            self._validate()
            _fit_dialog_to_screen(self, (1000, 680), (820, 540))

    def _identity_changed(self) -> None:
        self.specimen_id = self.specimen_input.text().strip()
        self.recipe_identity.setText(f"Specimen: {self.specimen_id or '-'}")
        self._settings_changed()

    def _refresh_marker_references(self) -> None:
        self._loaded_references = None
        self._settings_changed()

    def _toggle_coordinates(self, expanded: bool) -> None:
        self._coordinate_panel.setVisible(expanded)
        self.advanced_button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _recognition_changed(self) -> None:
        self._brightness.setEnabled(self.recognition_mode.currentIndex() == 1)
        self._settings_changed()

    def _select_role(self, role: int) -> None:
        self.role_buttons[role].setChecked(True)
        self.clear_region_button.setEnabled(self.preview.regions[role] is not None)

    def _store_recipe(self) -> None:
        if self.preview is None or self._confirmed is None:
            self._error.setText("Reusable recipes require camera setup")
            return
        try:
            original = self._stored_recipe[0] if self._stored_recipe is not None else None
            detector = original.detector if original is not None else None
            if detector is not None:
                detector = replace(
                    detector,
                    threshold=(
                        detector.threshold
                        if self.detector_threshold.value() == self._decision_baseline[0]
                        else self.detector_threshold.value()
                    ),
                    review_margin=(
                        detector.review_margin
                        if self.detector_margin.value() == self._decision_baseline[1]
                        else self.detector_margin.value()
                    ),
                )
            profile = RecipeProfile(
                original.profile.family_id if original is not None and original.profile is not None else uuid4().hex,
                uuid4().hex,
                self.recipe_name.text().strip(),
                self.recipe_notes.toPlainText().strip(),
            )
            stored = StoredRecipe(
                self.specimen_id.strip(),
                self._candidate_recipe(require_confirmation=False),
                self.preview._image.width(),
                self.preview._image.height(),
                detector,
                profile,
                SpecimenDetails(self.specimen_name.text().strip(), self.specimen_batch.text().strip()),
            )
        except (OSError, ValueError) as error:
            self._error.setText(str(error))
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save new recipe revision (original is preserved)",
            str(RECIPE_DIRECTORY / "synchronization-recipe.json"),
            "JSON recipe (*.json)",
        )
        if not path:
            return
        try:
            save_managed_recipe(
                Path(path),
                stored,
                source=self._stored_recipe[1] if self._stored_recipe is not None else None,
            )
        except (OSError, ValueError) as error:
            self._error.setText(f"Recipe not saved: {error}")
            return
        self._recipe_path = Path(path)
        self._stored_recipe = (stored, self._recipe_path)
        self._decision_baseline = (self.detector_threshold.value(), self.detector_margin.value())
        self.recipe = stored.recipe
        self._loaded_references = (
            (stored.recipe.marker_appearance, stored.recipe.position_appearance)
            if stored.recipe.marker_appearance is not None and stored.recipe.position_appearance is not None
            else None
        )
        self._reference_geometry = (stored.recipe.marker, stored.recipe.position)
        self.purpose_label.setText(f"{profile.name}\n{profile.notes}\n{stored.mode_label}")
        self._confirmed.setChecked(False)
        self.recipe_file.setText(f"Recipe saved: {self._recipe_path.name}")
        self.recipe_file.setToolTip(path)
        self._error.clear()

    def _load_recipe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load synchronization recipe", str(RECIPE_DIRECTORY), "JSON recipe (*.json)"
        )
        if not path:
            return
        try:
            stored = load_recipe(Path(path))
        except (OSError, ValueError) as error:
            self._error.setText(f"Recipe not loaded: {error}")
            return
        self._apply_stored_recipe(stored, Path(path))

    def _apply_stored_recipe(self, stored: StoredRecipe, path: Path) -> bool:
        if self.preview is None or self.recognition_mode is None or self._confirmed is None:
            self._error.setText("Recipe loading requires camera setup")
            return False
        try:
            if (stored.image_width, stored.image_height) != (self.preview._image.width(), self.preview._image.height()):
                raise ValueError("Recipe image dimensions differ from this camera frame; configure new regions")
            settings = stored.recipe
            controls = (
                (self._brightness, settings.brightness),
                (self._stable, settings.synchronization.stable_frames),
                (self._gap, settings.synchronization.max_gap_seconds),
                (self._timeout, settings.synchronization.cycle_timeout_seconds),
            )
            if any(not control.minimum() <= value <= control.maximum() for control, value in controls):
                raise ValueError("Recipe timing or brightness is outside supported setup limits")
        except (OSError, ValueError) as error:
            self._error.setText(f"Recipe not loaded: {error}")
            return False
        self.recipe = settings
        self.recipe_identity.setText(f"Specimen: {self.specimen_id}")
        self.specimen_name.setText(stored.specimen.name)
        self.specimen_batch.setText(stored.specimen.batch)
        self.recipe_name.setText(stored.profile.name if stored.profile is not None else path.stem)
        self.recipe_notes.setPlainText(stored.profile.notes if stored.profile is not None else "")
        self.purpose_label.setText(f"{self.recipe_name.text()}\n{self.recipe_notes.toPlainText()}\n{stored.mode_label}")
        self.preview.inspection_region = stored.detector.region if stored.detector is not None else None
        if stored.detector is not None:
            self.detector_description.setText(
                f"Model SHA-256: {stored.detector.model_sha256}\n"
                f"Training ROI: {stored.detector.region}\n"
                "Model/ROI changes require a newly trained and validated family recipe."
            )
            self.detector_threshold.setValue(stored.detector.threshold)
            self.detector_margin.setValue(stored.detector.review_margin)
        else:
            self.detector_description.setText("Counting only. Use Collect and train to bind a trained model.")
        self.detector_threshold.setEnabled(stored.detector is not None)
        self.detector_margin.setEnabled(stored.detector is not None)
        self._decision_baseline = (self.detector_threshold.value(), self.detector_margin.value())
        for inputs, region in zip(self._regions, (settings.marker, settings.position), strict=True):
            for spin, value in zip(inputs, (region.left, region.top, region.width, region.height), strict=True):
                with QSignalBlocker(spin):
                    spin.setValue(value * 100)
        for control, value in controls:
            with QSignalBlocker(control):
                control.setValue(value)
        with QSignalBlocker(self.recognition_mode):
            self.recognition_mode.setCurrentIndex(0 if settings.marker_appearance is not None else 1)
        self._brightness.setEnabled(settings.marker_appearance is None)
        self._loaded_references = (
            (settings.marker_appearance, settings.position_appearance)
            if settings.marker_appearance is not None and settings.position_appearance is not None
            else None
        )
        geometry = [Region(*(spin.value() / 100 for spin in inputs)) for inputs in self._regions]
        self._reference_geometry = (geometry[0], geometry[1])
        self._confirmed.setChecked(False)
        self._preview_regions()
        self._validate()
        self._recipe_path = Path(path)
        self._stored_recipe = (stored, path)
        self.recipe_file.setText(f"Recipe loaded: {self._recipe_path.name} | {stored.mode_label}")
        self.recipe_file.setToolTip(stored.description(path))
        bound = stored.detector is not None
        self.store_recipe_button.setEnabled(True)
        if self._confirmed is not None:
            self._confirmed.setText(
                "Markers, inspection crop and test cycles checked / UNQUALIFIED"
                if bound
                else "Setup and test cycles checked / counting only"
            )
        self.save_button.setText("Use for this run")
        return True

    def _canvas_changed(self, role: int, region: Region | None) -> None:
        values = (region.left, region.top, region.width, region.height) if region is not None else (0, 0, 0, 0)
        previous = tuple(spin.value() for spin in self._regions[role])
        for spin, value in zip(self._regions[role], values, strict=True):
            with QSignalBlocker(spin):
                spin.setValue(value * 100)
        if tuple(spin.value() for spin in self._regions[role]) != previous:
            self._settings_changed()

    def _settings_changed(self) -> None:
        if self._confirmed is not None:
            self._confirmed.setChecked(False)
        self._preview_regions()
        self._validate()
        if self.preview is not None:
            self.recipe_file.setText("Recipe: unsaved changes")

    def _preview_regions(self) -> None:
        if self.preview is None:
            return
        regions: list[Region | None] = []
        for inputs in self._regions:
            try:
                regions.append(Region(*(spin.value() / 100 for spin in inputs)))
            except ValueError:
                regions.append(None)
        self.preview.regions = (regions[0], regions[1])
        self.preview._recipe = (
            replace(self.recipe, marker=regions[0], position=regions[1])
            if regions[0] is not None and regions[1] is not None
            else None
        )
        self._select_role(self.preview.active_role)
        self.preview.update()

    def _candidate_recipe(self, *, require_confirmation: bool) -> ReplayRecipe:
        regions = []
        for role, inputs in enumerate(self._regions):
            values = [spin.value() / 100 for spin in inputs]
            if self.preview is not None and (values[2] <= 0 or values[3] <= 0):
                raise ValueError(f"{'Start' if role == 0 else 'Position'} region is not set")
            region = Region(*values)
            if self.preview is not None:
                width, height = self.preview._image.width(), self.preview._image.height()
                if int((region.left + region.width) * width) <= int(region.left * width) or int(
                    (region.top + region.height) * height
                ) <= int(region.top * height):
                    raise ValueError("Each region must cover at least one source image pixel")
            regions.append(region)
        if self._confirmed is not None and regions[0] == regions[1]:
            raise ValueError("Select distinct start and position regions; shared-marker qualification is unavailable")
        synchronization = SyncConfig(self._stable.value(), self._gap.value(), self._timeout.value())
        if require_confirmation and self._confirmed is not None and not self._confirmed.isChecked():
            raise ValueError("Confirm regions and timing before starting synchronization")
        candidate = replace(
            self.recipe,
            marker=regions[0],
            position=regions[1],
            brightness=self._brightness.value(),
            synchronization=synchronization,
            marker_appearance=None,
            position_appearance=None,
        )
        if self.recognition_mode is not None and self.recognition_mode.currentIndex() == 0:
            if self.preview is None:
                raise ValueError("Marker appearance requires a camera setup frame")
            if self._loaded_references is not None and tuple(regions) == self._reference_geometry:
                marker_reference, position_reference = self._loaded_references
            else:
                converted = self.preview._image.convertToFormat(QImage.Format.Format_BGR888)
                pixels = np.frombuffer(converted.constBits(), dtype=np.uint8)
                pixels = pixels.reshape(converted.height(), converted.bytesPerLine())[:, : converted.width() * 3]
                source = pixels.reshape(converted.height(), converted.width(), 3)
                marker_reference = MarkerAppearance.capture(source, regions[0])
                position_reference = MarkerAppearance.capture(source, regions[1])
            candidate = replace(candidate, marker_appearance=marker_reference, position_appearance=position_reference)
        return candidate

    def _validate(self) -> None:
        try:
            self._candidate_recipe(require_confirmation=False)
        except (OSError, ValueError) as error:
            self._error.setText(str(error))
            self.save_button.setEnabled(False)
            if self._confirmed is not None:
                self._confirmed.setEnabled(False)
                self.store_recipe_button.setEnabled(False)
        else:
            self._error.clear()
            if self._confirmed is not None:
                self._confirmed.setEnabled(True)
                self.store_recipe_button.setEnabled(bool(self.specimen_id.strip()))
            self.save_button.setEnabled(
                self._confirmed is None or (self._confirmed.isChecked() and bool(self.specimen_id.strip()))
            )

    def _save(self) -> None:
        try:
            candidate = self._candidate_recipe(require_confirmation=True)
            if self.preview is not None:
                details = SpecimenDetails(self.specimen_name.text().strip(), self.specimen_batch.text().strip())
                if self._stored_recipe is not None:
                    original = self._stored_recipe[0]
                    if original.detector is not None and (
                        (self.detector_threshold.value(), self.detector_margin.value()) != self._decision_baseline
                    ):
                        raise ValueError("Save a new recipe revision before using changed decision settings")
                    original_name = (
                        original.profile.name if original.profile is not None else self._stored_recipe[1].stem
                    )
                    original_notes = original.profile.notes if original.profile is not None else ""
                    if (
                        self.recipe_name.text().strip() != original_name
                        or self.recipe_notes.toPlainText().strip() != original_notes
                    ):
                        raise ValueError("Save a new recipe revision or discard the changed name/purpose notes")
                    self.run_setup = specimen_setup(original, self.specimen_id, candidate, details)
                else:
                    self.run_setup = StoredRecipe(
                        self.specimen_id,
                        candidate,
                        self.preview._image.width(),
                        self.preview._image.height(),
                        specimen=details,
                    )
        except (OSError, ValueError) as error:
            self._error.setText(str(error))
            return
        self.recipe = candidate
        self.accept()


class OfflineInspectionDialog(QDialog):
    """Unqualified image inspection isolated from live synchronization and assessment.

    Args:
        parent (QWidget | None): Owning desktop window.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Offline defect inspection")
        self.resize(1100, 740)
        self.setMinimumSize(800, 600)
        self._source: Image | None = None
        self._model_path: Path | None = None
        self._region: Region | None = None
        self._result: DetectionResult | None = None
        self._worker: InspectionWorker | None = None
        self._image_worker: ImageImportWorker | None = None
        self._busy = False
        self._generation = 0
        self._job_generation = 0
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel("Offline defect inspection")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch()
        badge = QLabel("UNQUALIFIED / OFFLINE")
        badge.setObjectName("warning")
        heading.addWidget(badge)
        layout.addLayout(heading)
        files_form = QFormLayout()
        self.image_path = QLineEdit()
        self.image_path.setReadOnly(True)
        self.model_path = QLineEdit()
        self.model_path.setReadOnly(True)
        self.open_image_button = self._command(
            "Open image", QStyle.StandardPixmap.SP_DialogOpenButton, self._open_image
        )
        self.open_model_button = self._command("Load model", QStyle.StandardPixmap.SP_DriveHDIcon, self._open_model)
        for label, field, button in (
            ("Image", self.image_path, self.open_image_button),
            ("Model", self.model_path, self.open_model_button),
        ):
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(button)
            files_form.addRow(label, row)
        layout.addLayout(files_form)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.editor = RegionEditor()
        self.editor.single_region = True
        self.editor.region_labels = ("INSPECTION ROI", "")
        self.editor.setAccessibleName("Offline specimen inspection region editor")
        self.editor.regionChanged.connect(self._region_changed)
        splitter.addWidget(self.editor)
        controls = QWidget()
        controls.setMinimumWidth(240)
        controls.setMaximumWidth(310)
        side = QVBoxLayout(controls)
        form = QFormLayout()
        self.coordinates: list[QDoubleSpinBox] = []
        for name in ("X", "Y", "Width", "Height"):
            field = QDoubleSpinBox()
            field.setRange(0, 100)
            field.setDecimals(2)
            field.setSuffix(" %")
            field.setAccessibleName(f"Inspection region {name}")
            field.valueChanged.connect(self._numeric_region)
            self.coordinates.append(field)
            form.addRow(name, field)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.001, 0.999)
        self.threshold.setDecimals(3)
        self.threshold.setSingleStep(0.01)
        self.threshold.setValue(0.5)
        self.threshold.setAccessibleName("Unqualified anomaly score threshold")
        self.threshold.valueChanged.connect(self._invalidate)
        form.addRow("Threshold", self.threshold)
        side.addLayout(form)
        self.full_image_button = self._command(
            "Full image ROI", QStyle.StandardPixmap.SP_TitleBarMaxButton, self._full_image
        )
        side.addWidget(self.full_image_button)
        self.heatmap = QCheckBox("Heatmap")
        self.heatmap.setChecked(True)
        self.heatmap.toggled.connect(self._render)
        side.addWidget(self.heatmap)
        self.assessment = QLabel("Unknown / no result")
        self.assessment.setTextFormat(Qt.TextFormat.PlainText)
        self.assessment.setWordWrap(True)
        side.addWidget(self.assessment)
        self.score = QLabel("Score: -")
        side.addWidget(self.score)
        side.addStretch()
        self.inspect_button = self._command("Inspect image", QStyle.StandardPixmap.SP_MediaPlay, self._inspect)
        self.inspect_button.setObjectName("primary")
        side.addWidget(self.inspect_button)
        self.save_image_button = self._command(
            "Save image", QStyle.StandardPixmap.SP_DialogSaveButton, self._save_image
        )
        side.addWidget(self.save_image_button)
        splitter.addWidget(controls)
        splitter.setStretchFactor(0, 1)
        layout.addWidget(splitter, 1)
        self.error = QLabel("")
        self.error.setObjectName("warning")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll_result)
        self._timer.start()
        self._refresh()
        _fit_dialog_to_screen(self, (1100, 740), (800, 600))

    def _command(self, text: str, icon: QStyle.StandardPixmap, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(self.style().standardIcon(icon), text)
        button.setToolTip(text)
        button.clicked.connect(callback)
        return button

    def _refresh(self) -> None:
        for control in (self.open_image_button, self.open_model_button, self.threshold, self.editor, *self.coordinates):
            control.setEnabled(not self._busy)
        self.full_image_button.setEnabled(not self._busy and self._source is not None)
        self.inspect_button.setEnabled(
            not self._busy and self._source is not None and self._model_path is not None and self._region is not None
        )
        self.save_image_button.setEnabled(not self._busy and self._result is not None)

    def _invalidate(self) -> None:
        self._generation += 1
        self._result = None
        self.assessment.setText("Unknown / not inspected")
        self.score.setText("Score: -")
        self.error.clear()
        self._render()
        self._refresh()

    def _open_image(self) -> None:
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open specimen image", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"
        )
        if not path:
            return
        self._source = None
        self.editor.clear()
        self._region_changed(0, None)
        self._invalidate()
        self.image_path.setText(path)
        self.image_path.setToolTip(path)
        try:
            self._image_worker = ImageImportWorker(Path(path))
            self._busy = True
            self.assessment.setText("Unknown / image loading")
            self._refresh()
            self._image_worker.start()
        except RuntimeError as error:
            self._image_worker = None
            self._busy = False
            self.error.setText(str(error))
        self._refresh()

    def _open_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load trusted Anomalib export", "", "Anomalib export (*.xml *.onnx)"
        )
        if path:
            self._model_path = Path(path)
            self.model_path.setText(path)
            self.model_path.setToolTip(path)
            self._invalidate()

    def _region_changed(self, role: int, region: Region | None) -> None:
        if role != 0 or self._busy:
            return
        self._region = region
        self.editor.regions = (region, None)
        values = (region.left, region.top, region.width, region.height) if region is not None else (0, 0, 0, 0)
        for field, value in zip(self.coordinates, values, strict=True):
            blocker = QSignalBlocker(field)
            field.setValue(value * 100)
            del blocker
        self._invalidate()

    def _numeric_region(self, value: float) -> None:
        try:
            region = Region(*(field.value() / 100 for field in self.coordinates))
        except ValueError as error:
            self._region = None
            self.editor.regions = (None, None)
            self._invalidate()
            self.error.setText(str(error))
        else:
            self._region = region
            self.editor.regions = (region, None)
            self._invalidate()

    def _full_image(self) -> None:
        if self._source is not None:
            self._region_changed(0, Region(0, 0, 1, 1))

    def _inspect(self) -> None:
        if self._busy or self._source is None or self._region is None or self._model_path is None:
            return
        self._invalidate()
        try:
            settings = DetectionSettings(self._model_path, self._region, self.threshold.value())
            self._worker = InspectionWorker(self._source, settings)
            self._job_generation = self._generation
            self._busy = True
            self.assessment.setText("Unknown / model loading and inference")
            self._refresh()
            self._worker.start()
        except (OSError, ValueError, RuntimeError) as error:
            self._busy = False
            self.error.setText(str(error))
            self._refresh()

    def _poll_result(self) -> None:
        if self._image_worker is not None:
            try:
                imported = self._image_worker.results.get_nowait()
            except Empty:
                if self._image_worker.alive:
                    return
                imported = DetectionFailure("Image import ended without a result")
            self._image_worker = None
            self._busy = False
            self.assessment.setText("Unknown / not inspected")
            if isinstance(imported, DetectionFailure):
                self.error.setText(imported.reason[:400])
                self.error.setToolTip(imported.reason)
            else:
                self._source = imported
            self._render()
            self._refresh()
            return
        if self._worker is None or not self._busy:
            return
        try:
            result = self._worker.results.get_nowait()
        except Empty:
            if not self._worker.alive:
                self._busy = False
                self.assessment.setText("Unknown / detector unavailable")
                self.error.setText("Detector worker ended without a result")
                self._refresh()
            return
        self._busy = False
        if self._job_generation != self._generation:
            self.assessment.setText("Unknown / inputs changed; inspect again")
        elif isinstance(result, DetectionFailure):
            self.assessment.setText("Unknown / detector unavailable")
            self.error.setText(result.reason[:400])
            self.error.setToolTip(result.reason)
        else:
            self._result = result
            self.assessment.setText("Unqualified / anomaly" if result.anomalous else "Unqualified / normal")
            self.score.setText(f"Score: {result.score:.4f} | Threshold: {result.settings.threshold:.3f}")
        self._render()
        self._refresh()

    def _composite(self) -> Image | None:
        source = self._result.source_image if self._result is not None else self._source
        if source is None:
            return None
        image = source.copy()
        if self._result is not None and self.heatmap.isChecked():
            image = _inspection_overlay(self._result)
        return image

    def _render(self) -> None:
        image = self._composite()
        if image is not None:
            self.editor._display_image(image)
            self.editor.update()

    def _save_image(self) -> None:
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save offline snapshot", "inspection.png", "PNG image (*.png)")
        if not path:
            return
        try:
            if Path(path).suffix.lower() != ".png":
                raise ValueError("Choose a .png filename for the offline snapshot")
            success, encoded = cv2.imencode(".png", self._composite())
            if not success:
                raise ValueError("Could not encode inspection snapshot")
            Path(path).write_bytes(encoded.tobytes())
            self.error.setText("Snapshot saved / not qualified inspection evidence")
        except (OSError, ValueError, cv2.error) as error:
            self.error.setText(f"Snapshot save failed: {error}")


class TrainingCollectionDialog(QDialog):
    """Collect candidates, review images, then explicitly request a separate training job.

    Args:
        parent (MainWindow): Camera and synchronization owner; production gates remain unchanged.
    """

    MANUAL_FRAME_MAX_AGE_SECONDS: float = 1.0

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.owner = parent
        self.setWindowTitle("Collect and train / unqualified")
        self.resize(1120, 820)
        self.setMinimumSize(880, 720)
        self._source: Image | None = None
        self._region: Region | None = None
        self._collection: CandidateCollection | None = None
        self._document: CollectionDocument | None = None
        self._manifest: Path | None = None
        self._manifest_digest = ""
        self._review_worker: ImageImportWorker | None = None
        self._review_index = 0
        self._reviewed: set[int] = set()
        self._export: Path | None = None
        self._result_path: Path | None = None
        self._attached_trial: MarkerTrial | None = None
        self._capture_camera: CameraPreview | None = None
        self._manual_count = 0
        self._manual_last_frame = -1
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._training_output)
        self._process.finished.connect(self._training_finished)
        self._process.errorOccurred.connect(self._training_error)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel("Collect and train")
        title.setObjectName("title")
        layout.addWidget(title)
        self.identity = QLabel("No active synchronization run")
        self.identity.setTextFormat(Qt.TextFormat.PlainText)
        self.identity.setWordWrap(True)
        layout.addWidget(self.identity)
        files_form = QFormLayout()
        self.folder = QLineEdit(str(Path.cwd() / "datasets" / "rdt-candidates"))
        self.folder.setReadOnly(True)
        self.browse_folder = self._button("Output folder", QStyle.StandardPixmap.SP_DirIcon, self._choose_folder)
        python = Path(__file__).resolve().parents[4] / ".venv" / "Scripts" / "python.exe"
        self.python = QLineEdit(str(python if python.is_file() else Path(sys.executable)))
        self.python.setReadOnly(True)
        self.browse_python = self._button("Training Python", QStyle.StandardPixmap.SP_FileIcon, self._choose_python)
        for label, field, button in (
            ("Collection storage", self.folder, self.browse_folder),
            ("Anomalib environment", self.python, self.browse_python),
        ):
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(button)
            files_form.addRow(label, row)
        layout.addLayout(files_form)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.editor = RegionEditor()
        self.editor.single_region = True
        self.editor.region_labels = ("TRAINING ROI", "")
        self.editor.regionChanged.connect(self._region_changed)
        splitter.addWidget(self.editor)
        controls = QWidget()
        controls.setMinimumWidth(275)
        controls.setMaximumWidth(320)
        panels = QVBoxLayout(controls)
        panels.setSpacing(2)
        self.stage_tabs = QTabWidget()
        setup_page, review_page, training_page = QWidget(), QWidget(), QWidget()
        side = QVBoxLayout(setup_page)
        side.setSpacing(2)
        review_side = QVBoxLayout(review_page)
        training_side = QVBoxLayout(training_page)
        self.stage_tabs.addTab(setup_page, "Collection")
        self.stage_tabs.addTab(review_page, "Review")
        self.stage_tabs.addTab(training_page, "Training")
        panels.addWidget(self.stage_tabs)
        form = QFormLayout()
        form.setVerticalSpacing(2)
        self.cycle_mode = QComboBox()
        self.cycle_mode.addItem("Auto", "auto")
        self.cycle_mode.addItem("Manual", "manual")
        self.cycle_mode.setAccessibleName("Collection cycle mode")
        self.cycle_mode.setToolTip(
            "Auto: detect marker cycles. Manual: click Capture cycle after each completed cycle."
        )
        self.target = QSpinBox()
        self.target.setRange(20, 30)
        self.target.setValue(30)
        self.target.setSuffix(" images")
        self.target.setAccessibleName("Images to collect")
        cycle_controls = QHBoxLayout()
        cycle_controls.addWidget(self.cycle_mode)
        cycle_controls.addWidget(self.target)
        form.addRow("Cycle cutoff", cycle_controls)
        self.coordinates: list[QDoubleSpinBox] = []
        for name in ("X", "Y", "Width", "Height"):
            field = QDoubleSpinBox()
            field.setRange(0, 100)
            field.setDecimals(2)
            field.setSuffix(" %")
            field.setAccessibleName(f"Training region {name}")
            field.valueChanged.connect(self._numeric_region)
            self.coordinates.append(field)
            form.addRow(name, field)
        side.addLayout(form)
        self.synchronize_button = self._button(
            "Configure synchronization", QStyle.StandardPixmap.SP_BrowserReload, self._configure_synchronization
        )
        side.addWidget(self.synchronize_button)
        self.use_camera = self._button("Use camera frame", QStyle.StandardPixmap.SP_ComputerIcon, self._use_camera)
        side.addWidget(self.use_camera)
        self.collect_button = self._button(
            "Collect candidates", QStyle.StandardPixmap.SP_DialogSaveButton, self._collect
        )
        self.collect_button.setObjectName("primary")
        side.addWidget(self.collect_button)
        self.capture_cycle_button = self._button(
            "Capture cycle", QStyle.StandardPixmap.SP_DialogApplyButton, self._capture_manual_cycle
        )
        self.capture_cycle_button.setToolTip("Complete a cycle, hold the specimen at its return position, then click")
        side.addWidget(self.capture_cycle_button)
        self.stop_collection = self._button(
            "Stop collection", QStyle.StandardPixmap.SP_MediaStop, self._stop_collection
        )
        side.addWidget(self.stop_collection)
        self.load_button = self._button(
            "Load collection", QStyle.StandardPixmap.SP_DialogOpenButton, self._load_collection
        )
        side.addWidget(self.load_button)
        self.state = QLabel("Unqualified / no collected candidates")
        self.state.setWordWrap(True)
        self.state.setTextFormat(Qt.TextFormat.PlainText)
        panels.insertWidget(0, self.state)
        self.cycle_count = QLabel("Visually observed cycles: unavailable")
        self.saved_count = QLabel("Saved candidates: 0 / 30")
        self.synchronization_status = QLabel()
        self.synchronization_status.setTextFormat(Qt.TextFormat.PlainText)
        self.synchronization_status.setWordWrap(True)
        panels.insertWidget(1, self.cycle_count)
        panels.insertWidget(2, self.saved_count)
        panels.insertWidget(3, self.synchronization_status)
        review_row = QHBoxLayout()
        self.previous = self._button(
            "", QStyle.StandardPixmap.SP_ArrowBack, lambda: self.sample.setValue(self.sample.value() - 1)
        )
        self.previous.setToolTip("Previous candidate")
        self.previous.setAccessibleName("Previous candidate")
        self.next = self._button(
            "", QStyle.StandardPixmap.SP_ArrowForward, lambda: self.sample.setValue(self.sample.value() + 1)
        )
        self.next.setToolTip("Next candidate")
        self.next.setAccessibleName("Next candidate")
        self.sample = QSpinBox()
        self.sample.setRange(0, 0)
        self.sample.setAccessibleName("Candidate image number")
        self.sample.valueChanged.connect(self._review_sample)
        review_row.addWidget(self.previous)
        review_row.addWidget(self.sample, 1)
        review_row.addWidget(self.next)
        review_side.addLayout(review_row)
        self.crop_view = QCheckBox("ROI crop")
        self.crop_view.toggled.connect(self._review_sample)
        review_side.addWidget(self.crop_view)
        self.review_status = QLabel("Reviewed: 0 / 0")
        review_side.addWidget(self.review_status)
        self.confirm = QCheckBox("Specimen remained undamaged")
        self.confirm.setToolTip(
            "Confirm after reviewing every candidate image; damaged images must not train the baseline"
        )
        self.confirm.toggled.connect(self._refresh)
        review_side.addWidget(self.confirm)
        self.train_button = self._button("Train PatchCore", QStyle.StandardPixmap.SP_MediaPlay, self._train)
        training_side.addWidget(self.train_button)
        self.load_export_button = self._button(
            "Load trained model", QStyle.StandardPixmap.SP_DialogOpenButton, self._load_export
        )
        training_side.addWidget(self.load_export_button)
        self.cancel_training = self._button(
            "Cancel training", QStyle.StandardPixmap.SP_MediaStop, self._cancel_training
        )
        training_side.addWidget(self.cancel_training)
        self.inspect_export = self._button(
            "Inspect export", QStyle.StandardPixmap.SP_FileDialogContentsView, self._inspect_export
        )
        training_side.addWidget(self.inspect_export)
        self.live_threshold = QDoubleSpinBox()
        self.live_threshold.setRange(0.001, 0.999)
        self.live_threshold.setDecimals(3)
        self.live_threshold.setValue(0.5)
        self.live_threshold.setAccessibleName("Unqualified live inspection threshold")
        self.live_review_margin = QDoubleSpinBox()
        self.live_review_margin.setRange(0, 0.5)
        self.live_review_margin.setDecimals(3)
        self.live_review_margin.setSingleStep(0.01)
        self.live_review_margin.setValue(0.05)
        self.live_review_margin.setAccessibleName("Live inspection review margin")
        self.live_review_margin.setToolTip("Inclusive needs-review half-band around the threshold; not confidence")
        threshold_form = QFormLayout()
        self.family_name = QLineEdit()
        self.family_name.setMaxLength(128)
        self.family_name.setPlaceholderText("e.g. A-type standard bending")
        self.family_notes = QLineEdit()
        self.family_notes.setMaxLength(2000)
        threshold_form.addRow("Recipe name", self.family_name)
        threshold_form.addRow("Purpose notes", self.family_notes)
        threshold_form.addRow("Unqualified threshold", self.live_threshold)
        threshold_form.addRow("Review margin", self.live_review_margin)
        training_side.addLayout(threshold_form)
        self.review_band_note = QLabel()
        self.review_band_note.setObjectName("caption")
        self.review_band_note.setWordWrap(True)
        training_side.addWidget(self.review_band_note)
        self.live_threshold.valueChanged.connect(self._update_review_band)
        self.live_review_margin.valueChanged.connect(self._update_review_band)
        self._update_review_band()
        self.save_inspection_recipe_button = self._button(
            "Save inspection recipe", QStyle.StandardPixmap.SP_DialogSaveButton, self._save_inspection_recipe
        )
        training_side.addWidget(self.save_inspection_recipe_button)
        side.addStretch()
        review_side.addStretch()
        training_side.addStretch()
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        controls_scroll.setMinimumWidth(300)
        controls_scroll.setMaximumWidth(344)
        controls_scroll.setWidget(controls)
        splitter.addWidget(controls_scroll)
        splitter.setStretchFactor(0, 1)
        layout.addWidget(splitter, 1)
        self.fault = QLabel("")
        self.fault.setObjectName("warning")
        self.fault.setTextFormat(Qt.TextFormat.PlainText)
        self.fault.setWordWrap(True)
        layout.addWidget(self.fault)
        self.training_log = QPlainTextEdit()
        self.training_log.setReadOnly(True)
        self.training_log.setMaximumBlockCount(200)
        self.training_log.setMaximumHeight(100)
        self.training_log.hide()
        self.training_log.setAccessibleName("Training process log")
        layout.addWidget(self.training_log)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        self.target.valueChanged.connect(self._refresh)
        self.cycle_mode.currentIndexChanged.connect(self._refresh)
        self._use_camera()
        self._refresh()
        _fit_dialog_to_screen(self, (1120, 820), (880, 720))

    def _button(self, text: str, icon: QStyle.StandardPixmap, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(self.style().standardIcon(icon), text)
        button.setToolTip(text)
        button.clicked.connect(callback)
        return button

    def _capturing(self) -> bool:
        return self._collection is not None and not self._collection.finished.is_set()

    def _update_review_band(self) -> None:
        threshold, margin = self.live_threshold.value(), self.live_review_margin.value()
        low, high = threshold - margin, threshold + margin
        self.review_band_note.setText(
            f"Needs review: {low:.3f} - {high:.3f} (inclusive)\nNot a confidence interval."
            if 0 <= low <= high <= 1
            else "Review band must stay within 0 - 1. Adjust the threshold or margin before saving."
        )

    def _training(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    def _refresh(self) -> None:
        busy = self._collection is not None or self._training() or self._review_worker is not None
        camera = self.owner.controller.camera
        trial = camera.trial if camera is not None else None
        result = trial.result if trial is not None else None
        streaming = camera is not None and camera.status.state == "Streaming"
        lost = result is not None and result.state == SyncState.LOST
        manual = self.cycle_mode.currentData() == "manual"
        ready = streaming and (
            bool(self.owner.specimen.text().strip())
            if manual
            else trial is not None and not lost and self.owner.controller.run_record is not None
        )
        if self.owner.controller.worker is not None:
            ready = False
        self.synchronize_button.setVisible(not manual)
        self.synchronize_button.setEnabled(bool(not manual and streaming and not busy and (trial is None or lost)))
        self.synchronize_button.setText("Restart synchronization" if lost else "Configure synchronization")
        self.cycle_count.setText(
            f"Visually observed cycles: {result.observed_cycles}"
            if result is not None
            else "Visually observed cycles: unavailable"
        )
        progress = self._collection.document if self._capturing() else self._document
        frame = self.owner.controller.last_frame
        fresh = frame is not None and 0 <= monotonic() - frame.received_at <= self.MANUAL_FRAME_MAX_AGE_SECONDS
        if manual:
            declared = len(progress.records) if progress is not None else 0
            self.cycle_count.setText(f"Operator-declared cycles saved: {declared}")
        saved = len(progress.records) if progress is not None else 0
        target = progress.settings.target if progress is not None else self.target.value()
        self.saved_count.setText(f"Saved candidates: {saved} / {target}")
        if not streaming:
            reason = f"Camera unavailable / {camera.status.reason}" if camera is not None else "Camera unavailable"
        elif manual and not self.owner.specimen.text().strip():
            reason = "Enter a specimen ID on the main screen"
        elif manual and self._capturing() and not fresh:
            reason = "Manual / waiting for a fresh camera frame"
        elif manual and self._capturing() and progress is not None and len(progress.records) < self._manual_count:
            reason = "Manual / saving captured cycle"
        elif manual and self._source is not None and self._region is not None:
            reason = "Manual / click Capture cycle after each cycle"
        elif not manual and lost:
            reason = f"Synchronization lost / {result.reason} / restart required"
        elif not manual and (trial is None or self.owner.controller.run_record is None):
            reason = "Synchronization not configured"
        elif self._source is None:
            reason = "Camera frame not selected"
        elif self._region is None:
            reason = "Training ROI not selected"
        else:
            reason = result.reason if result is not None else "Waiting for both marks visible"
        self.synchronization_status.setText(reason)
        self.collect_button.setToolTip(reason)
        for control in (self.use_camera, self.browse_folder, self.load_button):
            control.setEnabled(not busy)
        self.browse_python.setEnabled(not busy)
        editable = not busy and self._document is None
        self.target.setEnabled(editable)
        self.cycle_mode.setEnabled(editable)
        self.capture_cycle_button.setVisible(manual)
        self.capture_cycle_button.setEnabled(
            manual
            and self._capturing()
            and streaming
            and camera is self._capture_camera
            and frame is not None
            and fresh
            and frame.frame_id > self._manual_last_frame
            and progress is not None
            and len(progress.records) == self._manual_count
            and self._manual_count < progress.settings.target
        )
        self.editor.setEnabled(editable)
        for field in self.coordinates:
            field.setEnabled(editable)
        self.collect_button.setEnabled(
            bool(
                ready and not busy and self._document is None and self._source is not None and self._region is not None
            )
        )
        self.stop_collection.setEnabled(self._capturing())
        count = len(self._document.records) if self._document is not None else 0
        complete = self._document is not None and self._document.state in {"Awaiting confirmation", "Confirmed normal"}
        reviewed = complete and len(self._reviewed) == count and self._review_worker is None
        self.confirm.setEnabled(bool(reviewed and not busy))
        self.train_button.setEnabled(bool(reviewed and not busy and self.confirm.isChecked() and self.python.text()))
        self.cancel_training.setEnabled(self._training())
        for control in (self.sample, self.crop_view):
            control.setEnabled(bool(count and not busy and self._review_worker is None))
        self.previous.setEnabled(bool(count and not busy and self._review_worker is None and self.sample.value() > 1))
        self.next.setEnabled(bool(count and not busy and self._review_worker is None and self.sample.value() < count))
        self.inspect_export.setEnabled(self._export is not None and not busy)
        self.save_inspection_recipe_button.setEnabled(self._export is not None and not busy)
        self.load_export_button.setEnabled(self._document is not None and not busy)
        self.live_threshold.setEnabled(not busy)
        self.live_review_margin.setEnabled(not busy)
        self.review_status.setText(f"Reviewed: {len(self._reviewed)} / {count}")

    def _configure_synchronization(self) -> None:
        if not self.synchronize_button.isEnabled():
            return
        self.owner.start_marker_trial()
        run = self.owner.controller.run_record
        if run is not None:
            self.identity.setText(f"Specimen: {run.specimen_id} | Run: {run.run_id}")
        self._refresh()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Collection output directory", self.folder.text())
        if path:
            self.folder.setText(path)

    def _choose_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Trusted Anomalib training Python",
            self.python.text(),
            "Python (python.exe python3.exe);;Executables (*.exe)",
        )
        if path:
            self.python.setText(path)
            self._refresh()

    def _use_camera(self) -> None:
        if self._collection is not None or self._training() or self._review_worker is not None:
            return
        frame = self.owner.controller.last_frame
        if frame is None:
            return
        self._document = None
        self._manifest = None
        self._source = frame.image.copy()
        self._region_changed(0, None)
        self._reviewed.clear()
        self.confirm.setChecked(False)
        self._export = None
        self.editor._display_image(self._source)
        self.editor.update()
        run = self.owner.controller.run_record
        self.identity.setText(
            f"Specimen: {run.specimen_id} | Run: {run.run_id}" if run is not None else "No synchronization run"
        )
        self.state.setText("Unqualified / collection not started")
        self.stage_tabs.setCurrentIndex(0)
        self.fault.clear()
        self._refresh()

    def _region_changed(self, role: int, region: Region | None) -> None:
        if role != 0 or self._capturing() or self._training():
            return
        self._region = region
        self.editor.regions = (region, None)
        values = (region.left, region.top, region.width, region.height) if region else (0, 0, 0, 0)
        for field, value in zip(self.coordinates, values, strict=True):
            blocker = QSignalBlocker(field)
            field.setValue(value * 100)
            del blocker
        self.confirm.setChecked(False)
        self.editor.update()
        self._refresh()

    def _numeric_region(self, value: float) -> None:
        try:
            region = Region(*(field.value() / 100 for field in self.coordinates))
        except ValueError as error:
            self._region = None
            self.editor.regions = (None, None)
            self.fault.setText(str(error))
            self._refresh()
            self.editor.update()
        else:
            self._region_changed(0, region)
            self.fault.clear()

    def _collect(self) -> None:
        if not self.collect_button.isEnabled() or self._source is None or self._region is None:
            return
        camera, run = self.owner.controller.camera, self.owner.controller.run_record
        manual = self.cycle_mode.currentData() == "manual"
        if camera is None or (not manual and (camera.trial is None or run is None)):
            return
        try:
            height, width = self._source.shape[:2]
            self._region.pixels(self._source)
            if manual:
                settings = CollectionSettings(
                    self.owner.specimen.text().strip(),
                    uuid4().hex,
                    "manual-capture-v1",
                    self._region,
                    width,
                    height,
                    self.target.value(),
                    cycle_mode="manual",
                )
            else:
                if run is None:
                    raise ValueError("Automatic collection requires a synchronization run")
                settings = CollectionSettings(
                    run.specimen_id, run.run_id, run.recipe_version, self._region, width, height, self.target.value()
                )
            self._collection = CandidateCollection(Path(self.folder.text()), settings)
            self._document = None
            self._manifest = None
            self._reviewed.clear()
            self._export = None
            self.confirm.setChecked(False)
            self.fault.clear()
            self._collection.start()
            self._capture_camera = camera
            self._manual_count = 0
            self._manual_last_frame = -1
            self.identity.setText(
                f"Specimen: {settings.specimen_id} | Collection run: {settings.run_id} | {settings.cycle_mode}"
            )
            if not manual:
                self._attached_trial = camera.trial
                if self._attached_trial is None:
                    raise RuntimeError("Synchronization ended before collection started")
                self._attached_trial.set_cycle_capture(self._collection.capture)
            self.state.setText("Collecting unreviewed candidates / 0 saved")
        except (OSError, ValueError, RuntimeError) as error:
            if self._collection is not None:
                self._collection.interrupt(str(error))
            self.fault.setText(str(error))
        self._refresh()

    def _capture_manual_cycle(self) -> None:
        self._refresh()
        if not self.capture_cycle_button.isEnabled():
            self.fault.setText("Capture unavailable: wait for a fresh camera frame and the previous image to save.")
            return
        frame = self.owner.controller.last_frame
        collection = self._collection
        if frame is None or collection is None:
            return
        if collection.capture_manual(frame.image, frame.frame_id, frame.received_at):
            self._manual_count += 1
            self._manual_last_frame = frame.frame_id
            self.fault.clear()
        else:
            self.fault.setText("Cycle not captured: frame already used or collection no longer accepting images.")
        self._refresh()

    def _stop_collection(self) -> None:
        if self._collection is not None:
            self._collection.interrupt("Collection stopped by user before target")

    def _load_collection(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load candidate collection", self.folder.text(), "Collection (collection.json)"
        )
        if path:
            self._load_manifest(Path(path))

    def _load_manifest(self, path: Path) -> None:
        self._review_worker = None
        self.confirm.setChecked(False)
        self._reviewed.clear()
        self._export = None
        self._document = None
        self._manifest = None
        self.fault.clear()
        try:
            self._manifest_digest = metadata_digest(path)
            document = load_document(path, expected_sha256=self._manifest_digest)
            self._document, self._manifest = document, path
            self.cycle_mode.setCurrentIndex(1 if document.settings.cycle_mode == "manual" else 0)
            self.target.setValue(document.settings.target)
            self.folder.setText(str(path.parent.parent))
            self._region_changed(0, document.settings.region)
            self.identity.setText(
                f"Specimen: {document.settings.specimen_id} | Run: {document.settings.run_id} | {path.parent}"
            )
            self.state.setText(f"Unqualified / {document.state} / {len(document.records)} saved")
            if document.reason:
                self.fault.setText(document.reason)
            blocker = QSignalBlocker(self.sample)
            self.sample.setRange(1 if document.records else 0, len(document.records))
            self.sample.setValue(1 if document.records else 0)
            del blocker
            if document.records:
                self.stage_tabs.setCurrentIndex(1)
                self._review_sample()
        except (OSError, ValueError) as error:
            self.fault.setText(str(error))
            self.state.setText("Unknown / collection unavailable")
        self._refresh()

    def _review_sample(self) -> None:
        if self._document is None or self._manifest is None or self._review_worker is not None:
            return
        index = self.sample.value() - 1
        if not 0 <= index < len(self._document.records):
            return
        record = self._document.records[index]
        name = record.crop_file if self.crop_view.isChecked() else record.source_file
        self.confirm.setChecked(False)
        self._review_index = index
        self._reviewed.discard(index)
        try:
            self._review_worker = ImageImportWorker(local_file(self._manifest.parent, name))
            self._review_worker.start()
        except (OSError, ValueError, RuntimeError) as error:
            self._review_worker = None
            self.fault.setText(str(error))
        self._refresh()

    def _train(self) -> None:
        if not self.train_button.isEnabled() or self._manifest is None:
            return
        self._export = None
        self._result_path = local_file(self._manifest.parent, f"result-{uuid4().hex}.json")
        environment = QProcessEnvironment.systemEnvironment()
        package_root = str(Path(__file__).resolve().parents[1])
        environment.insert("PYTHONPATH", package_root + os.pathsep + environment.value("PYTHONPATH"))
        environment.insert("PYTHONUNBUFFERED", "1")
        self._process.setProcessEnvironment(environment)
        self.training_log.clear()
        self.training_log.show()
        self.stage_tabs.setCurrentIndex(2)
        self.fault.clear()
        self.state.setText("Training requested / verifying confirmed candidates; no live model change")
        self._process.start(
            self.python.text(),
            [
                "-m",
                "rdt_monitor.training",
                "--collection",
                str(self._manifest),
                "--sha256",
                self._manifest_digest,
                "--confirm-undamaged",
                "--result",
                str(self._result_path),
            ],
        )
        self._refresh()

    def _cancel_training(self) -> None:
        if self._training():
            self.state.setText("Cancelling training / no live model change")
            self._process.kill()

    def _training_output(self) -> None:
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.training_log.appendPlainText(text[-12000:])

    def _training_error(self, error: QProcess.ProcessError) -> None:
        self.fault.setText(f"Training process unavailable: {self._process.errorString()}")
        self.state.setText("Unknown / training unavailable")
        self._refresh()

    def _training_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self._training_output()
        self.confirm.setChecked(False)
        self.state.setText("Unknown / training failed or cancelled; no model activated")
        try:
            if (
                code != 0
                or status != QProcess.ExitStatus.NormalExit
                or self._result_path is None
                or self._manifest is None
            ):
                raise ValueError("Training failed or was cancelled; inspect the process log")
            with self._result_path.open("rb") as stream:
                raw = json.loads(stream.read(8193))
            if (
                not isinstance(raw, dict)
                or set(raw) != {"model", "qualified"}
                or raw["qualified"] is not False
                or not isinstance(raw["model"], str)
            ):
                raise ValueError("Training result metadata is invalid")
            model = (self._manifest.parent / raw["model"]).resolve()
            if (
                not model.is_relative_to(self._manifest.parent.resolve())
                or model.suffix != ".xml"
                or not model.is_file()
                or not model.with_suffix(".bin").is_file()
            ):
                raise ValueError("Training export is missing or outside its collection directory")
            self._export = model
            self.state.setText("Unqualified export ready / defect accuracy not validated")
            self.fault.setText(str(model))
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            self.fault.setText(str(error))
        if self._manifest is not None:
            try:
                self._manifest_digest = metadata_digest(self._manifest)
            except (OSError, ValueError):
                self._manifest_digest = ""
        self._refresh()

    def _inspect_export(self) -> None:
        if self._export is not None:
            self.owner.open_offline_inspection()
            dialog = self.owner._inspection_dialog
            if dialog is not None and not dialog._busy:
                dialog._model_path = self._export
                dialog.model_path.setText(str(self._export))
                dialog._invalidate()

    def _load_export(self) -> None:
        if self._manifest is None or self._document is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select this collection's trained OpenVINO model",
            str(self._manifest.parent),
            "OpenVINO model (*.xml)",
        )
        if not path:
            return
        try:
            model = Path(path).resolve()
            if not model.is_relative_to(self._manifest.parent.resolve()):
                raise ValueError("Select a model from a training job inside this collection")
            DetectionSettings(model, self._document.settings.region, self.live_threshold.value())
            self._export = model
            self.state.setText("Unqualified model selected / save inspection recipe to use it")
            self.fault.clear()
        except (OSError, ValueError) as error:
            self.fault.setText(f"Model not selected: {error}")
        self._refresh()

    def _save_inspection_recipe(self) -> None:
        if self._export is None or self._document is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select synchronization recipe to bind to this model", str(RECIPE_DIRECTORY), "JSON recipe (*.json)"
        )
        if not path:
            return
        try:
            stored = load_recipe(Path(path))
            settings = self._document.settings
            if (stored.image_width, stored.image_height) != (settings.width, settings.height):
                raise ValueError("Synchronization recipe dimensions do not match the training collection")
            target, _ = QFileDialog.getSaveFileName(
                self,
                "Save unqualified inspection recipe",
                str(RECIPE_DIRECTORY / "inspection-recipe.json"),
                "JSON recipe (*.json)",
            )
            if not target:
                return
            profile = RecipeProfile(
                stored.profile.family_id if stored.profile is not None else uuid4().hex,
                uuid4().hex,
                self.family_name.text().strip(),
                self.family_notes.text().strip(),
            )
            save_inspection_recipe(
                Path(target),
                replace(stored, specimen_id=settings.specimen_id, profile=profile),
                self._export,
                settings.region,
                self.live_threshold.value(),
                self.live_review_margin.value(),
            )
            self.fault.setText(f"Inspection recipe saved: {target}. Stop, Load recipe on the main screen, then Start.")
        except (OSError, ValueError) as error:
            self.fault.setText(f"Inspection recipe not saved: {error}")

    def poll(self) -> None:
        """Update bounded writer, review and training state without waiting on workers."""
        collection = self._collection
        if collection is not None:
            camera, run = self.owner.controller.camera, self.owner.controller.run_record
            result = self._attached_trial.result if self._attached_trial is not None else None
            automatic = collection.document.settings.cycle_mode == "auto"
            if (
                self.owner.controller.closing
                or camera is None
                or camera is not self._capture_camera
                or camera.status.state != "Streaming"
                or self.owner.controller.disconnect_after_stop
                or (
                    automatic
                    and (
                        camera.trial is not self._attached_trial
                        or run is None
                        or run.run_id != collection.document.settings.run_id
                        or (result is not None and result.state == SyncState.LOST)
                    )
                )
            ):
                collection.interrupt("Camera/synchronization coverage ended during candidate collection")
            progress = collection.document
            if not automatic and self._capturing() and self.owner.controller.last_frame is not None:
                self.editor._display_image(self.owner.controller.last_frame.image)
                self.editor.update()
            self.state.setText(
                f"Unqualified / {progress.state} / {len(progress.records)} of {progress.settings.target} saved"
            )
            if collection.finished.is_set():
                if self._attached_trial is not None:
                    self._attached_trial.set_cycle_capture(None)
                self._attached_trial = None
                self._capture_camera = None
                self._collection = None
                self._load_manifest(collection.folder / "collection.json")
                if progress.state == "Incomplete":
                    self.fault.setText(progress.reason)
        if self._review_worker is not None:
            try:
                image = self._review_worker.results.get_nowait()
            except Empty:
                image = None
            if image is not None:
                self._review_worker = None
                if isinstance(image, DetectionFailure):
                    self.fault.setText(image.reason[:400])
                else:
                    self.editor._display_image(image)
                    self.editor.regions = (None, None) if self.crop_view.isChecked() else (self._region, None)
                    self.editor.update()
                    self._reviewed.add(self._review_index)
        self._refresh()


class MainWindow(QMainWindow):
    """Camera-preview/replay workbench with no enabled production commands."""

    def __init__(self) -> None:
        super().__init__()
        RECIPE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.setWindowTitle("RDT Reliability Monitor")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        self.recipe = fixture_recipe()
        self.controller = MonitorController()
        self._last_camera_status = ""
        self._last_sync_fault = ""
        self._revision = 1
        self._last_status = ""
        self._inspection_dialog: OfflineInspectionDialog | None = None
        self._training_dialog: TrainingCollectionDialog | None = None
        central = QWidget()
        central.setObjectName("monitorSurface")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(12)
        header = QWidget()
        header.setObjectName("monitorHeader")
        heading = QHBoxLayout(header)
        heading.setContentsMargins(0, 0, 0, 4)
        heading.setSpacing(12)
        title = QLabel("RDT RELIABILITY MONITOR")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch()
        self.open_button = self._button("Open recording", QStyle.StandardPixmap.SP_DialogOpenButton, self._open)
        self.fixture_button = self._button("Synthetic fixture", QStyle.StandardPixmap.SP_FileIcon, self._fixture)
        self.settings_button = self._button(
            "Replay settings", QStyle.StandardPixmap.SP_FileDialogDetailedView, self._settings
        )
        for button in (self.open_button, self.fixture_button, self.settings_button):
            button.setParent(central)
            button.hide()
        specimen_label = QLabel("Specimen ID")
        heading.addWidget(specimen_label)
        self.specimen = QLineEdit()
        self.specimen.setAccessibleName("Specimen ID")
        specimen_label.setBuddy(self.specimen)
        self.specimen.setPlaceholderText("Specimen ID")
        self.specimen.setMaxLength(128)
        self.specimen.setMaximumWidth(170)
        self.specimen.setFixedHeight(36)
        heading.addWidget(self.specimen)
        self.details_button = QPushButton("Details")
        self.details_button.setCheckable(True)
        self.details_button.setAccessibleName("Show technical details")
        self.details_button.setToolTip("Show or hide technical details")
        self.details_button.setFixedHeight(36)
        utility_divider = QFrame()
        utility_divider.setObjectName("toolbarDivider")
        utility_divider.setFixedSize(1, 24)
        heading.addWidget(utility_divider)
        heading.addWidget(self.details_button)
        self.basic_settings_button = self._button(
            "Settings", QStyle.StandardPixmap.SP_FileDialogDetailedView, self._basic_settings
        )
        self.basic_settings_button.setToolTip("Basic settings")
        self.basic_settings_button.setFixedHeight(36)
        heading.addWidget(self.basic_settings_button)
        layout.addWidget(header)
        self.load_recipe_button = self._button(
            "Load recipe", QStyle.StandardPixmap.SP_DialogOpenButton, self.load_marker_recipe
        )
        self.load_recipe_button.setToolTip("Load a stored synchronization recipe for review and confirmation")
        self.load_recipe_button.setEnabled(True)
        camera_toolbar = QWidget()
        camera_toolbar.setObjectName("cameraToolbar")
        camera_bar = QHBoxLayout(camera_toolbar)
        camera_bar.setContentsMargins(0, 0, 0, 0)
        camera_bar.setSpacing(8)
        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 99)
        self.camera_index.setAccessibleName("Generic OpenCV camera index")
        self.camera_index.setToolTip("Generic index, not verified manufacturer/model identity; USB smoke check used 0")
        self.connect_button = self._button("Connect camera", QStyle.StandardPixmap.SP_ComputerIcon, self.connect_camera)
        self.disconnect_button = self._button(
            "Disconnect camera",
            QStyle.StandardPixmap.SP_MediaStop,
            self.disconnect_camera,
        )
        self.disconnect_button.setEnabled(False)
        self.connect_button.setFixedHeight(36)
        self.disconnect_button.setFixedHeight(36)
        camera_bar.addWidget(self.connect_button)
        camera_bar.addWidget(self.disconnect_button)
        recipe_divider = QFrame()
        recipe_divider.setObjectName("toolbarDivider")
        recipe_divider.setFixedSize(1, 24)
        camera_bar.addSpacing(4)
        camera_bar.addWidget(recipe_divider)
        camera_bar.addSpacing(4)
        self.recipe_summary = QLabel("No recipe loaded")
        self.recipe_summary.setObjectName("caption")
        self.recipe_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.recipe_summary.setWordWrap(True)
        self.recipe_summary.setMinimumWidth(0)
        self.recipe_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        camera_bar.addWidget(self.recipe_summary, 1)
        self.trial_button = self._button("Start", QStyle.StandardPixmap.SP_MediaPlay, self.start_marker_trial)
        self.trial_button.setToolTip(
            "Confirm synchronization; a model-bound recipe also enables unqualified cycle inspection"
        )
        self.trial_button.setEnabled(False)
        self.trial_button.setObjectName("primary")
        self.trial_button.setMinimumWidth(80)
        self.stop_work_button = self._button("Stop", QStyle.StandardPixmap.SP_MediaStop, self.stop_marker_trial)
        self.stop_work_button.setToolTip("Stop synchronization while keeping the camera preview connected")
        self.stop_work_button.setEnabled(False)
        self.stop_work_button.setObjectName("stopAction")
        self.stop_work_button.setMinimumWidth(80)
        self.camera_details = QLabel("Camera disconnected")
        self.camera_details.setWordWrap(True)
        self.camera_details.setObjectName("caption")
        layout.addWidget(camera_toolbar)
        self.source_label = QLabel("Source: none")
        self.source_label.setWordWrap(True)
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.run_label = QLabel("Run: none | Synchronization not configured")
        self.run_label.setWordWrap(True)
        self.run_label.setTextFormat(Qt.TextFormat.PlainText)
        self.source_label.setObjectName("caption")
        self.run_label.setObjectName("caption")
        vertical = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter = vertical
        vertical.setHandleWidth(6)
        horizontal = QSplitter(Qt.Orientation.Horizontal)
        self.horizontal_splitter = horizontal
        horizontal.setHandleWidth(6)
        horizontal.setChildrenCollapsible(False)
        preview_area = QFrame()
        preview_area.setObjectName("cameraPanel")
        preview_layout = QVBoxLayout(preview_area)
        preview_layout.setContentsMargins(10, 12, 10, 10)
        preview_layout.setSpacing(10)
        preview_header = QHBoxLayout()
        self.preview_title = QLabel("CAMERA VIEW")
        self.preview_title.setObjectName("panelTitle")
        preview_header.addWidget(self.preview_title)
        preview_header.addStretch()
        self.camera_connection = QLabel("Disconnected")
        self.camera_connection.setObjectName("statusBadge")
        self.camera_connection.setProperty("status", "offline")
        self.camera_connection.setProperty("connected", False)
        preview_header.addWidget(self.camera_connection)
        preview_layout.addLayout(preview_header)
        self.frame_label = QLabel("Live view")
        self.frame_label.setObjectName("sectionTitle")
        self.live_view = LiveView()
        preview_layout.addWidget(self.live_view, 1)
        self.freshness = QLabel("Not live")
        self.freshness.setWordWrap(True)
        self.freshness.setObjectName("caption")
        self.freshness.setToolTip("Camera age uses host read-completion time, not a sensor timestamp.")
        self._recognition_status = "Recognition not configured"
        horizontal.addWidget(preview_area)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 0, 0, 0)
        side.setSpacing(8)
        side.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        verdict_panel = QFrame()
        self.verdict_panel = verdict_panel
        verdict_panel.setObjectName("verdictPanel")
        verdict_panel.setFixedHeight(209)
        verdict_layout = QVBoxLayout(verdict_panel)
        verdict_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        verdict_layout.setContentsMargins(14, 12, 14, 12)
        verdict_layout.setSpacing(6)
        assessment_title = QLabel("INSPECTION RESULT")
        assessment_title.setObjectName("panelTitle")
        verdict_layout.addWidget(assessment_title)
        self.condition = _VerdictLabel()
        self.condition.setObjectName("cycleVerdict")
        display_mode = QApplication.instance().property("display_mode")
        self.condition.setProperty("surface_color", "#FFFFFF" if display_mode == "light" else "#09131C")
        self.condition.setFixedHeight(132)
        self.condition.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.condition.setWordWrap(True)
        verdict_layout.addWidget(self.condition)
        self._show_verdict(InspectionOutcome.UNINSPECTED, "Unknown - No inspection yet")
        self.defect_alarm = QLabel()
        self.defect_alarm.setObjectName("defectAlarm")
        self.defect_alarm.setWordWrap(True)
        self.defect_alarm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        verdict_layout.addWidget(self.defect_alarm)
        self._update_defect_alarm()
        side.addWidget(verdict_panel)
        control_panel = QFrame()
        control_panel.setObjectName("controlPanel")
        control_layout = QVBoxLayout(control_panel)
        control_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        control_layout.setContentsMargins(14, 12, 14, 12)
        control_layout.setSpacing(10)
        control_heading = QHBoxLayout()
        control_title = QLabel("MONITORING CONTROL")
        control_title.setObjectName("panelTitle")
        control_heading.addWidget(control_title)
        control_heading.addStretch()
        self.operator_status = QLabel("Stopped")
        self.operator_status.setObjectName("statusBadge")
        self.operator_status.setProperty("status", "offline")
        control_heading.addWidget(self.operator_status)
        control_layout.addLayout(control_heading)
        run_buttons = QHBoxLayout()
        run_buttons.setSpacing(10)
        run_buttons.addWidget(self.trial_button, 1)
        run_buttons.addWidget(self.stop_work_button, 1)
        control_layout.addLayout(run_buttons)
        cycles_row = QHBoxLayout()
        cycles_row.addWidget(QLabel("Cycles"))
        cycles_row.addStretch()
        self.cycles = QLabel("0")
        self.cycles.setObjectName("counter")
        cycles_row.addWidget(self.cycles)
        control_layout.addLayout(cycles_row)
        control_layout.addWidget(self.load_recipe_button)
        self.evidence = QLabel("Not recorded")
        self.evidence.setWordWrap(True)
        self.evidence.setTextFormat(Qt.TextFormat.PlainText)
        self.assessment_note = QLabel("Defect inspection unavailable")
        self.assessment_note.setObjectName("caption")
        self.assessment_note.setWordWrap(True)
        self.results_button = self._button(
            "Open inspection results", QStyle.StandardPixmap.SP_DirOpenIcon, self.open_inspection_results
        )
        control_layout.addWidget(self.results_button)
        side.addWidget(control_panel)
        self.inspected_cycles = QLabel("Inspected 0 | Uninspected 0")
        self.inspected_cycles.setObjectName("caption")
        self.inspected_cycles.setWordWrap(True)
        self.coverage_warning = QLabel()
        self.coverage_warning.setObjectName("warning")
        self.coverage_warning.setWordWrap(True)
        self.coverage_warning.hide()
        side.addWidget(self.coverage_warning)
        qualification = QLabel("Unqualified")
        qualification.setObjectName("caption")
        qualification.setToolTip("Development inspection; defect-detection accuracy is not production-qualified.")
        side.addWidget(qualification)
        side.addStretch()
        self.start_button = self._button("Start replay", QStyle.StandardPixmap.SP_MediaPlay, self.start_replay)
        self.start_button.setObjectName("primary")
        self.start_button.setEnabled(False)
        self.stop_button = self._button("Stop replay", QStyle.StandardPixmap.SP_MediaStop, self.stop_replay)
        self.stop_button.setEnabled(False)
        side.addWidget(self.start_button)
        side.addWidget(self.stop_button)
        self.start_button.hide()
        self.stop_button.hide()
        self.diagnostics_menu = self.menuBar().addMenu("Diagnostics")
        self._diagnostic_controls = []
        for button in (
            self.open_button,
            self.fixture_button,
            self.settings_button,
            self.start_button,
            self.stop_button,
        ):
            action = self.diagnostics_menu.addAction(button.icon(), button.text())
            action.triggered.connect(button.click)
            action.setEnabled(button.isEnabled())
            self._diagnostic_controls.append((action, button))
        self.diagnostics_menu.aboutToShow.connect(self._update_diagnostics)
        self.inspection_menu = self.menuBar().addMenu("Inspection")
        self.offline_inspection_action = self.inspection_menu.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView), "Offline images"
        )
        self.offline_inspection_action.triggered.connect(self.open_offline_inspection)
        self.training_action = self.inspection_menu.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Collect and train"
        )
        self.training_action.triggered.connect(self.open_training_collection)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setObjectName("monitorControlsScroll")
        self.controls_scroll.setAccessibleName("Inspection and monitoring controls")
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.controls_scroll.setMinimumWidth(310)
        self.controls_scroll.setMaximumWidth(370)
        self.controls_scroll.setWidget(sidebar)
        horizontal.addWidget(self.controls_scroll)
        horizontal.setStretchFactor(0, 3)
        horizontal.setStretchFactor(1, 1)
        vertical.addWidget(horizontal)
        self.details_panel = QWidget()
        history_layout = QVBoxLayout(self.details_panel)
        history_layout.setContentsMargins(0, 6, 0, 0)
        technical = QGridLayout()
        technical.setHorizontalSpacing(16)
        for column in range(3):
            technical.setColumnStretch(column, 1)
        camera_form = QHBoxLayout()
        camera_label = QLabel("Camera index")
        camera_label.setBuddy(self.camera_index)
        camera_form.addWidget(camera_label)
        camera_form.addWidget(self.camera_index)
        camera_form.addStretch()
        technical.addLayout(camera_form, 0, 0)
        technical.addWidget(self.camera_details, 0, 1)
        technical.addWidget(self.frame_label, 0, 2)
        technical.addWidget(self.source_label, 1, 0)
        technical.addWidget(self.run_label, 1, 1, 1, 2)
        technical.addWidget(self.freshness, 2, 0)
        technical.addWidget(self.inspected_cycles, 2, 1)
        technical.addWidget(self.assessment_note, 3, 0, 1, 2)
        evidence_row = QHBoxLayout()
        evidence_row.setSpacing(8)
        evidence_row.addWidget(QLabel("Evidence"))
        evidence_row.addWidget(self.evidence)
        evidence_row.addStretch()
        technical.addLayout(evidence_row, 3, 2)
        station_status = QHBoxLayout()
        health_column, sync_column = QVBoxLayout(), QVBoxLayout()
        self.health = self._status(health_column, "Health", "Stopped")
        self.sync = self._status(sync_column, "Synchronization", "Not configured")
        station_status.addLayout(health_column)
        station_status.addLayout(sync_column)
        technical.addLayout(station_status, 4, 0, 1, 3)
        history_layout.addLayout(technical)
        self.details_scroll = QScrollArea()
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setMinimumSize(250, 100)
        self.details_scroll.setWidget(self.details_panel)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        self.bottom_splitter = bottom
        bottom.setHandleWidth(6)
        bottom.addWidget(self.details_scroll)
        self.details_scroll.hide()
        self.event_log_panel = QWidget()
        event_log_layout = QVBoxLayout(self.event_log_panel)
        event_log_layout.setContentsMargins(0, 6, 0, 0)
        event_log_layout.setSpacing(6)
        history_heading = QLabel("Event log")
        history_heading.setObjectName("panelTitle")
        history_heading.setToolTip("Session-only log, limited to 500 events")
        event_log_layout.addWidget(history_heading)
        self.history = QTableWidget(0, 6)
        self.history.setHorizontalHeaderLabels(
            ["UTC / source time", "Run", "Observed cycle", "Frame", "Event", "State"],
        )
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history.setAlternatingRowColors(True)
        self.history.setAccessibleName("Session event log")
        self.history.setMinimumHeight(90)
        self.history.verticalHeader().hide()
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.history.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.history.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column, width in enumerate((125, 110, 120, 72, 145)):
            self.history.setColumnWidth(column, width)
        event_log_layout.addWidget(self.history)
        bottom.addWidget(self.event_log_panel)
        roi_panel = QWidget()
        self._roi_panel = roi_panel
        roi_panel.setMinimumWidth(310)
        roi_panel.setMaximumWidth(370)
        roi_layout = QVBoxLayout(roi_panel)
        roi_layout.setContentsMargins(10, 6, 0, 0)
        roi_layout.setSpacing(6)
        self.roi_caption = QLabel("Latest inspection ROI - no result")
        self.roi_caption.setObjectName("panelTitle")
        self.roi_caption.setWordWrap(True)
        roi_layout.addWidget(self.roi_caption)
        self.roi_view = LiveView()
        self.roi_view.setMinimumSize(120, 60)
        self.roi_view._recipe = None
        self.roi_view.setAccessibleName("Latest inspected ROI with model anomaly heatmap")
        self.roi_view.setToolTip(
            "Suspected defects: stronger red indicates higher model anomaly, not confirmed damage."
        )
        roi_layout.addWidget(self.roi_view, 1)
        bottom.addWidget(roi_panel)
        bottom.setChildrenCollapsible(False)
        bottom.setStretchFactor(0, 1)
        bottom.setStretchFactor(1, 2)
        horizontal.splitterMoved.connect(self._align_evidence_column)
        vertical.addWidget(bottom)
        vertical.setChildrenCollapsible(False)
        self.details_button.toggled.connect(self._toggle_details)
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 1)
        layout.addWidget(vertical, 1)
        self.fault = QLabel("")
        self.fault.setObjectName("warning")
        self.fault.setWordWrap(True)
        self.fault.hide()
        layout.addWidget(self.fault)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        focus_order = (
            self.specimen,
            self.connect_button,
            self.disconnect_button,
            self.load_recipe_button,
            self.trial_button,
            self.stop_work_button,
            self.results_button,
            self.details_button,
            self.basic_settings_button,
            self.camera_index,
            self.history,
        )
        for first, second in zip(focus_order[:-1], focus_order[1:], strict=True):
            QWidget.setTabOrder(first, second)
        self._fit_available_screen()
        QTimer.singleShot(0, self._fit_layout_to_window)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep inspection evidence aligned with the monitoring column after resizing.

        Args:
            event (QResizeEvent): Native window resize notification.
        """
        super().resizeEvent(event)
        if hasattr(self, "_roi_panel"):
            QTimer.singleShot(0, self._fit_layout_to_window)

    def _align_evidence_column(self) -> None:
        width = self.controls_scroll.width()
        self._roi_panel.setFixedWidth(width)
        # Reallocate the splitter immediately after changing the fixed width;
        # otherwise Qt can retain the previous child position until its next layout.
        sizes = self.bottom_splitter.sizes()
        sizes[-1] = width
        self.bottom_splitter.setSizes(sizes)

    def _fit_layout_to_window(self) -> None:
        """Set stable splitter proportions from the actual Windows client size."""
        if self.horizontal_splitter.width() <= 0 or self.vertical_splitter.height() <= 0:
            return
        available_width = self.horizontal_splitter.width()
        sidebar_width = min(max(round(available_width * 0.26), 310), 370)
        sidebar_width = min(sidebar_width, max(1, available_width - 320))
        self.horizontal_splitter.setSizes([available_width - sidebar_width, sidebar_width])

        available_height = self.vertical_splitter.height()
        evidence_height = min(max(round(available_height * 0.24), 120), 210)
        evidence_height = min(evidence_height, max(1, available_height - 300))
        self.vertical_splitter.setSizes([available_height - evidence_height, evidence_height])

        bottom_width = self.bottom_splitter.width()
        evidence_width = min(sidebar_width, max(1, bottom_width - 260))
        detail_width = min(round(bottom_width * 0.25), max(0, bottom_width - evidence_width - 240))
        event_width = max(1, bottom_width - evidence_width - detail_width)
        self.bottom_splitter.setSizes(
            [detail_width if not self.details_scroll.isHidden() else 0, event_width, evidence_width]
        )
        self._align_evidence_column()

    def _fit_available_screen(self, available: QRect | None = None) -> None:
        if available is None:
            screen = self.screen()
            if screen is None:
                return
            available = screen.availableGeometry()
        available = available.adjusted(16, 16, -16, -48)
        self.setMinimumSize(min(960, available.width()), min(640, available.height()))
        self.resize(min(1440, available.width()), min(900, available.height()))
        self.move(available.center() - self.rect().center())

    def open_offline_inspection(self) -> None:
        """Open independent offline inspection without changing the active camera run."""
        if self._inspection_dialog is None:
            self._inspection_dialog = OfflineInspectionDialog(self)
        self._inspection_dialog.show()
        self._inspection_dialog.raise_()
        self._inspection_dialog.activateWindow()

    def _toggle_details(self, visible: bool) -> None:
        self.details_scroll.setVisible(visible)
        self.details_button.setText("Hide details" if visible else "Details")
        self.details_button.setAccessibleName("Hide technical details" if visible else "Show technical details")
        QTimer.singleShot(0, self._fit_layout_to_window)

    def open_training_collection(self) -> None:
        """Open explicit candidate collection/review without starting capture or training."""
        if self._training_dialog is None:
            self._training_dialog = TrainingCollectionDialog(self)
        self._training_dialog.show()
        self._training_dialog.raise_()
        self._training_dialog.activateWindow()

    def open_inspection_results(self) -> None:
        """Open the central results folder without changing the active inspection.

        Unavailable storage or desktop integration is reported explicitly in the
        main window rather than claiming that a folder was opened.
        """
        try:
            INSPECTION_DIRECTORY.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(INSPECTION_DIRECTORY))):
                raise RuntimeError("Windows could not open the folder; open it in File Explorer")
        except (OSError, RuntimeError) as error:
            self._set_notice(f"Inspection results not opened: {error}. Folder: {INSPECTION_DIRECTORY}")

    def _button(self, text: str, icon: QStyle.StandardPixmap, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(self.style().standardIcon(icon), text)
        button.setToolTip(text)
        button.clicked.connect(callback)
        return button

    def _update_diagnostics(self) -> None:
        for action, button in self._diagnostic_controls:
            action.setEnabled(button.isEnabled())

    def _status(self, layout: QVBoxLayout, title: str, value: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName("caption")
        layout.addWidget(label)
        status = QLabel(value)
        status.setWordWrap(True)
        status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(status)
        layout.addSpacing(6)
        return status

    def _set_notice(self, text: str) -> None:
        self.fault.setText(text)
        self.fault.setVisible(bool(text))
        self._refresh_operator_status()

    def _refresh_operator_status(self) -> None:
        snapshot = self.controller.lifecycle.snapshot
        status = {
            RunState.STOPPED: "Stopped",
            RunState.READY: "Running",
            RunState.RUNNING: "Running",
            RunState.DEGRADED: "Needs attention",
            RunState.RECOVERING: "Needs attention",
            RunState.STOPPING: "Stopping",
        }[snapshot.state]
        if self.controller.camera is not None and self.controller.camera.status.state == "Fault":
            status = "Camera unavailable"
        self.operator_status.setText(status)
        status_key = (
            "attention"
            if status in {"Needs attention", "Camera unavailable"}
            else ("active" if status == "Running" else "offline")
        )
        if self.operator_status.property("status") != status_key:
            self.operator_status.setProperty("status", status_key)
            self.operator_status.style().unpolish(self.operator_status)
            self.operator_status.style().polish(self.operator_status)
        connected = False
        connection = "Disconnected"
        self.preview_title.setText("CAMERA VIEW")
        if self.controller.mode == "camera" and self.controller.camera is not None:
            camera = self.controller.camera.status
            if camera.state == "Streaming":
                connected = camera.last_received_at is not None and 0 <= monotonic() - camera.last_received_at <= 1
                connection = "Live" if connected else "Preview stale"
                self.preview_title.setText("LIVE CAMERA VIEW")
            else:
                connection = {"Opening": "Connecting", "Fault": "Unavailable"}.get(camera.state, "Disconnected")
        elif self.controller.pipeline is not None:
            connection = "Replay" if self.controller.pipeline.alive else "Replay stopped"
            self.preview_title.setText("SYNTHETIC PREVIEW" if self.controller.synthetic else "RECORDING VIEW")
        self.camera_connection.setText(connection)
        self.camera_connection.setProperty("connected", connected)
        connection_key = (
            "active"
            if connection in {"Live", "Replay"}
            else ("attention" if connection in {"Preview stale", "Unavailable", "Replay stopped"} else "offline")
        )
        if self.camera_connection.property("status") != connection_key:
            self.camera_connection.setProperty("status", connection_key)
            self.camera_connection.style().unpolish(self.camera_connection)
            self.camera_connection.style().polish(self.camera_connection)
        failed = snapshot.uninspected if snapshot.mode != RunMode.DEVELOPMENT else 0
        self.coverage_warning.setText(f"{failed} cycle(s) not inspected" if failed else "")
        self.coverage_warning.setVisible(bool(failed))

    def _open(self) -> None:
        if self._busy():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open local recording",
            "",
            "Video (*.mp4 *.avi *.mov *.mkv);;All files (*)",
        )
        if path:
            self.controller.mode = "replay"
            self.controller.source = path
            self.controller.synthetic = False
            self.source_label.setText(f"Recording: {Path(path).name}")
            self.source_label.setToolTip(path)
            self.live_view.clear()
            self.start_button.setEnabled(True)

    def _fixture(self) -> None:
        if self._busy():
            return
        self.controller.mode = "replay"
        self.controller.source = "Synthetic marker fixture / not camera footage"
        self.controller.synthetic = True
        self.recipe = fixture_recipe()
        self._revision += 1
        self.source_label.setText("Synthetic fixture")
        self.source_label.setToolTip(self.controller.source)
        self.run_label.setText(f"No active run | replay-v{self._revision}")
        self.live_view.clear()
        self.start_button.setEnabled(True)

    def _settings(self) -> None:
        dialog = ReplaySettings(self.recipe, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.recipe = dialog.recipe
            self._revision += 1
            self.run_label.setText(f"Next recipe | replay-v{self._revision}")

    def start_replay(self) -> None:
        """Begin an explicitly unqualified new run only after an operator command."""
        try:
            record = self.controller.start_replay(
                self.specimen.text(), self.recipe, self._revision, factory=ReplayPipeline
            )
        except (ValueError, RuntimeError) as error:
            self._set_notice(str(error))
            return
        if record is None:
            return
        self.roi_view.clear()
        self.roi_caption.setText("Latest inspection ROI - counting only")
        self.live_view.inspection_region = None
        self._update_defect_alarm()
        self._show_verdict(InspectionOutcome.UNINSPECTED, "Unknown - Counting only / not inspected")
        self.evidence.setText("Not recorded")
        self.assessment_note.setText("Counting only - not inspected")
        self.live_view.clear()
        self.cycles.setText("0")
        self.inspected_cycles.setText("Inspected 0 | Uninspected 0")
        self.sync.setText("Waiting for fresh marker transition")
        self._last_status = ""
        self.run_label.setText(f"Run {record.run_id} | {record.specimen_id} | {record.recipe_version} | locked")
        self._set_notice("")
        self._log("Run started", "Unqualified; no defect assessment or evidence", "0.000 s", 0, "-")
        self._set_running(True)

    def stop_replay(self) -> None:
        """Request replay stop without waiting on decoding in the UI thread."""
        if self.controller.stop_replay(monotonic()):
            self.health.setText("Stopping")
            self.stop_button.setEnabled(False)

    def _set_running(self, running: bool) -> None:
        for control in (self.open_button, self.fixture_button, self.settings_button, self.specimen):
            control.setEnabled(not running)
        self.start_button.setEnabled(not running and self.controller.source is not None)
        self.stop_button.setEnabled(running)
        self.camera_index.setEnabled(not running)
        self.connect_button.setEnabled(not running)
        self.disconnect_button.setEnabled(False)
        self.trial_button.setEnabled(False)
        self.load_recipe_button.setEnabled(not running and not self.controller.closing)

    def _busy(self) -> bool:
        return self.controller.busy

    def connect_camera(self) -> None:
        """Open the existing Keyence API off-thread without starting inspection."""
        if not self.controller.connect_camera(self.camera_index.value(), factory=CameraPreview):
            return
        self._last_camera_status = ""
        self._last_sync_fault = ""
        self.live_view.clear()
        self.cycles.setText("0")
        self.inspected_cycles.setText("Inspected 0 | Uninspected 0")
        self.sync.setText("Unavailable / camera preview only")
        self.source_label.setText(f"Camera | OpenCV index {self.camera_index.value()}")
        self.source_label.setToolTip("Generic OpenCV camera index; manufacturer identity is not verified")
        if self.controller.loaded_recipe is not None:
            stored, path = self.controller.loaded_recipe
            self.run_label.setText(f"{path.name} | {stored.mode_label} | Not started")
            self.run_label.setToolTip(stored.description(path))
        else:
            self.run_label.setText("No active run")
        self.frame_label.setText("Opening camera")
        self.health.setText("Opening camera")
        self.freshness.setText("Waiting for first frame")
        self._recognition_status = "Recognition not configured"
        self._set_notice("")
        self._set_camera_running(True)
        self._log("Camera connect", "Preview only; no inspection", datetime.now(UTC).strftime("%H:%M:%S UTC"), 0, "-")

    def disconnect_camera(self) -> None:
        """Finalize development work before requesting worker-owned camera release."""
        if self.controller.disconnect_camera(monotonic()):
            if self.controller.stopping_trial is not None:
                self.sync.setText("Stopping / finalizing development cycles")
                self.stop_work_button.setEnabled(False)
                self.trial_button.setEnabled(False)
                self.load_recipe_button.setEnabled(False)
                self._finalize_marker_stop()
                return
            self.health.setText("Disconnecting camera")
            self.disconnect_button.setEnabled(False)
            self.trial_button.setEnabled(False)
            self.freshness.setText("Disconnecting / last frame is not live")

    def start_work(self) -> None:
        """Reject production inspection until its actual station services are implemented."""
        self._set_notice(f"Inspection unavailable: {', '.join(Readiness().blockers)}.")

    def start_marker_trial(self) -> None:
        """Start an explicit development trial, never production inspection."""
        self._configure_marker_trial()

    def load_marker_recipe(self) -> None:
        """Select a stored recipe without requiring a camera or starting a run."""
        if self.controller.closing or self.controller.lifecycle.snapshot.state != RunState.STOPPED:
            self._set_notice("Stop the current run before loading a recipe.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load recipe", str(RECIPE_DIRECTORY), "JSON recipe (*.json)")
        if not path:
            return
        try:
            stored = self.controller.select_recipe(Path(path))
        except (OSError, ValueError, RuntimeError) as error:
            self._set_notice(f"Recipe not loaded: {error}")
            return
        self.live_view.inspection_region = stored.detector.region if stored.detector is not None else None
        self.live_view.update()
        self.roi_view.clear()
        self.roi_caption.setText("Latest inspection ROI - no result")
        self.recipe_summary.setText(f"Recipe: {Path(path).name}")
        self.recipe_summary.setToolTip(stored.description(Path(path)))
        name = stored.profile.name if stored.profile is not None else Path(path).stem
        self.recipe_summary.setText(f"Recipe: {name}")
        self.run_label.setText(f"Recipe loaded: {name} | {stored.mode_label} | Not started")
        self.run_label.setToolTip(stored.description(Path(path)))
        self.assessment_note.setText(
            "UNQUALIFIED detector selected | Start to load model"
            if stored.detector is not None
            else "Counting only - loaded recipe has no detector"
        )
        self._log("Recipe loaded", f"{Path(path).name} | {stored.mode_label}", "Host clock", 0, "-")
        self._set_notice("")

    def _configure_marker_trial(self) -> None:
        if self.controller.retired_worker is not None and not self.controller.retired_worker.finished.is_set():
            self._set_notice("Previous detector is still finishing after Stop; wait before starting a new run.")
            return
        if (
            self.controller.camera is None
            or self.controller.closing
            or self.controller.camera.status.state != "Streaming"
            or self.controller.stopping_trial is not None
            or self.controller.disconnect_after_stop
        ):
            return
        previous = self.controller.camera.trial
        result = previous.result if previous is not None else None
        if previous is not None and (result is None or result.state != SyncState.LOST):
            return
        if self.live_view._image.isNull():
            return
        if not self.specimen.text().strip():
            self._set_notice("Enter a specimen ID before starting.")
            self.specimen.setFocus()
            return
        dialog = ReplaySettings(
            previous.recipe if previous is not None else self.controller.camera_recipe or fixture_recipe(),
            self,
            camera_image=self.live_view._image,
            specimen_id=self.specimen.text().strip(),
        )
        if self.controller.loaded_recipe is not None:
            if not dialog._apply_stored_recipe(*self.controller.loaded_recipe):
                self._set_notice(dialog._error.text())
                return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        inspection_recipe = dialog._stored_recipe or self.controller.loaded_recipe
        try:
            setup = self.controller.prepare_setup(
                dialog.specimen_id,
                dialog.recipe,
                self.live_view._image.width(),
                self.live_view._image.height(),
                SpecimenDetails(dialog.specimen_name.text().strip(), dialog.specimen_batch.text().strip()),
                inspection_recipe,
            )
        except (OSError, ValueError) as error:
            self._set_notice(f"Setup not accepted: {error}")
            return
        if previous is not None:
            self.stop_marker_trial()
            if self.controller.stopping_trial is not None:
                self._set_notice("Previous development trial is still stopping; wait before starting again.")
                return
        live = inspection_recipe is not None and inspection_recipe[0].detector is not None
        try:
            record = self.controller.start_camera_run(
                setup, inspection_recipe, INSPECTION_DIRECTORY, worker_factory=LiveInspection
            )
        except (OSError, ValueError) as error:
            self._set_notice(f"Run not started: could not record setup: {error}")
            return
        except RuntimeError as error:
            self._set_notice(str(error))
            return
        worker = self.controller.worker
        self._revision += 1
        self.specimen.setText(dialog.specimen_id)
        self.roi_view.clear()
        self.roi_caption.setText("Latest inspection ROI - no result")
        self.live_view.inspection_region = worker.binding.region if worker is not None else None
        self._update_defect_alarm()
        self._show_verdict(
            InspectionOutcome.UNINSPECTED,
            "Unknown - No inspection yet" if live else "Unknown - Counting only / not inspected",
        )
        self.evidence.setText("Not recorded")
        if live:
            self.assessment_note.setText("UNQUALIFIED live inspection | Waiting for completed cycle")
        else:
            self.assessment_note.setText("Counting only - not inspected")
        self.cycles.setText("0")
        self.inspected_cycles.setText("Inspected 0 | Uninspected 0")
        self.sync.setText("Waiting for fresh marker transition")
        self.run_label.setText(f"Development run {record.run_id} | {record.specimen_id} | settings locked")
        self.run_label.setToolTip(
            f"Recipe: synchronization-v{self._revision} | Generic camera index {self.camera_index.value()}"
        )
        if inspection_recipe is not None:
            stored, path = inspection_recipe
            self.recipe_summary.setText(f"Recipe: {path.name}")
            self.recipe_summary.setToolTip(stored.description(path))
            self.run_label.setText(
                f"Run {self.controller.run_record.run_id} | {path.name} | {stored.mode_label} | settings locked"
            )
            self.run_label.setToolTip(stored.description(path))
        self._last_sync_fault = ""
        self.specimen.setEnabled(False)
        self.trial_button.setEnabled(False)
        self.stop_work_button.setEnabled(True)
        self.load_recipe_button.setEnabled(False)
        self._set_notice("")
        self._log(
            "Synchronization started",
            "Unqualified cycle inspection enabled" if live else "Inspection unavailable; evidence not recorded",
            "Host clock",
            0,
            "-",
        )

    def stop_marker_trial(self) -> None:
        """Stop intake and asynchronously drain development events before finalizing."""
        if not self.controller.stop_camera_run(monotonic()):
            return
        self.sync.setText("Stopping / finalizing development cycles")
        self.stop_work_button.setEnabled(False)
        self.trial_button.setEnabled(False)
        self.load_recipe_button.setEnabled(False)
        self._finalize_marker_stop()

    def _finalize_marker_stop(self) -> None:
        self._render_source_poll(self.controller.poll_stopping(monotonic()))

    def _render_source_poll(self, batch: SourcePoll) -> None:
        for observation in batch.observations:
            self._render_observation(observation)
        self._render_inspections(batch.inspections)
        stopped = batch.stopped
        if stopped is None:
            return
        if stopped.timed_out_ticket is not None:
            last = stopped.timed_out_ticket
            self._show_verdict(
                InspectionOutcome.UNINSPECTED, f"Cycle {last.cycle} - Uninspected / finalization timed out"
            )
            self.evidence.setText("Incomplete")
            self.roi_view.clear()
            self.roi_caption.setText(f"Cycle {last.cycle} ROI - unavailable")
        snapshot = stopped.snapshot
        self.cycles.setText(str(snapshot.observed))
        self.inspected_cycles.setText(f"Inspected {snapshot.inspected} | Uninspected {snapshot.uninspected}")
        self.sync.setText("Stopped / coverage ended")
        self.run_label.setText(f"Stopped | {self.run_label.text()}")
        self.assessment_note.setText(f"Stopped | {self.assessment_note.text()}")
        self.specimen.setEnabled(True)
        self._last_sync_fault = ""
        self._set_notice(snapshot.reason if snapshot.stop_timed_out else self.controller.fault)
        self._log(
            "Development run stopped",
            snapshot.reason,
            datetime.now(UTC).strftime("%H:%M:%S UTC"),
            snapshot.observed,
            "-",
        )
        if self.controller.disconnect_after_stop and self.controller.camera is not None:
            self.disconnect_button.setEnabled(False)

    def _set_camera_running(self, running: bool) -> None:
        for control in (self.open_button, self.fixture_button, self.settings_button, self.camera_index):
            control.setEnabled(not running)
        self.specimen.setEnabled(not running or self.controller.run_record is None)
        self.connect_button.setEnabled(not running and not self.controller.closing)
        self.disconnect_button.setEnabled(running)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.trial_button.setEnabled(False)
        self.stop_work_button.setEnabled(False)
        self.load_recipe_button.setEnabled(
            not self.controller.closing and self.controller.lifecycle.snapshot.state == RunState.STOPPED
        )

    def _poll_camera(self) -> None:
        if self.controller.camera is None:
            self.load_recipe_button.setEnabled(
                not self.controller.closing and self.controller.lifecycle.snapshot.state == RunState.STOPPED
            )
            return
        if self.controller.stopping_trial is not None:
            self._finalize_marker_stop()
        status = self.controller.camera.status
        trial = self.controller.camera.trial
        result = trial.result if trial is not None else None
        synchronization_lost = result is not None and result.state == SyncState.LOST
        commands = self.controller.snapshot()
        self.trial_button.setEnabled(commands.can_start)
        self.trial_button.setText("Restart" if synchronization_lost else "Start")
        self.stop_work_button.setEnabled(commands.can_stop)
        self.load_recipe_button.setEnabled(commands.can_load_recipe)
        self.specimen.setEnabled((trial is None or synchronization_lost) and self.controller.stopping_trial is None)
        batch = self.controller.poll_camera()
        self._render_source_poll(batch)
        if trial is not None:
            if result is not None:
                self.cycles.setText(str(result.observed_cycles))
                self.sync.setText(f"Lost / {result.reason}" if synchronization_lost else result.reason)
            if synchronization_lost and not self._last_sync_fault:
                self._log(
                    "Synchronization fault",
                    result.reason,
                    datetime.now(UTC).strftime("%H:%M:%S UTC"),
                    result.observed_cycles,
                    str(result.frame_id),
                )
                self._last_sync_fault = result.reason
            if status.state in {"Fault", "Stopped"} and not synchronization_lost:
                self.sync.setText(
                    "Lost / camera coverage ended" if status.state == "Fault" else "Stopped / coverage ended"
                )
        frame = batch.frame
        if frame is not None:
            self.live_view.display_camera(frame, trial.recipe if trial is not None else None)
            self.frame_label.setText(f"Frame {frame.frame_id:06d} | host {frame.received_at:.3f} s")
            if trial is not None:
                recipe = trial.recipe
                if recipe.marker_appearance is not None and recipe.position_appearance is not None:
                    start_score = recipe.marker_appearance.score(frame.image, recipe.marker)
                    position_score = recipe.position_appearance.score(frame.image, recipe.position)
                    self._recognition_status = f"Start {start_score:.2f} | Position {position_score:.2f}"
                else:
                    self._recognition_status = "Brightness mode"
            else:
                self._recognition_status = "Recognition not configured"
        run_degraded = self.controller.lifecycle.snapshot.state == RunState.DEGRADED
        health = "Degraded" if (synchronization_lost or run_degraded) and status.state == "Streaming" else status.state
        self.health.setText(f"Camera {health.lower()}" if status.state == "Fault" else health)
        if status.state == "Streaming" and status.last_received_at is not None:
            age = max(0, monotonic() - status.last_received_at)
            freshness = f"Live | {age:.2f} s"
        else:
            freshness = f"Not live | {status.state}"
        self.freshness.setText(f"{freshness}  |  {self._recognition_status}")
        fps = f"{status.reported_fps:g}" if status.reported_fps > 0 else "unavailable"
        self.camera_details.setText(f"{status.width} x {status.height} | {fps} FPS" if status.width else "")
        self.camera_details.setToolTip(
            f"OpenCV index {status.index} | backend {status.backend or 'unavailable'} | "
            f"{status.width} x {status.height} | reported {fps} FPS"
        )
        if synchronization_lost and status.state == "Streaming":
            self._set_notice(f"Synchronization lost: {result.reason}. Review setup before restarting.")
        elif status.state == "Fault":
            self._set_notice(status.reason)
        elif self.controller.lifecycle.snapshot.stop_timed_out:
            self._set_notice(self.controller.lifecycle.snapshot.reason)
        elif run_degraded:
            self._set_notice(self.controller.lifecycle.snapshot.reason)
        elif self.controller.fault:
            self._set_notice(self.controller.fault)
        else:
            self._set_notice("")
        if status.state in {"Fault", "Stopped"} and status.state != self._last_camera_status:
            self._log(f"Camera {status.state}", status.reason, datetime.now(UTC).strftime("%H:%M:%S UTC"), 0, "-")
        self._last_camera_status = status.state
        if not self.controller.camera.alive:
            if self.controller.camera.trial is not None and self.controller.stopping_trial is None:
                self.stop_marker_trial()
            self._set_camera_running(False)
            if self.controller.closing:
                self.close()

    def _log(self, event: str, state: str, timestamp: str, cycle: int, frame: str) -> None:
        if self.history.rowCount() >= 500:
            self.history.removeRow(0)
        row = self.history.rowCount()
        self.history.insertRow(row)
        run_id = self.controller.run_record.run_id if self.controller.run_record else "-"
        for column, value in enumerate((timestamp, run_id, str(cycle), frame, event, state)):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            self.history.setItem(row, column, item)
        self.history.scrollToBottom()

    def _event(self, event: SyncResult, *, source_draining: bool = False) -> None:
        try:
            record = self.controller.observe(
                event,
                source_draining=source_draining
                or (self.controller.mode == "replay" and self.controller.lifecycle.snapshot.state == RunState.STOPPING),
            )
        except (RuntimeError, ValueError) as error:
            self.controller.lifecycle.coverage_lost(f"Cycle accounting failed: {error}")
            self._render_observation(ObservationUpdate(event, error=str(error)))
            return
        self._render_observation(ObservationUpdate(event, record))

    def _render_observation(self, update: ObservationUpdate) -> None:
        event, record = update.event, update.record
        if update.error:
            self._set_notice(f"Cycle accounting failed: {update.error}")
            self._log("Cycle accounting fault", update.error, f"{event.timestamp:.3f} s", event.observed_cycles, "-")
            return
        if record is not None:
            snapshot = self.controller.lifecycle.snapshot
            self.inspected_cycles.setText(f"Inspected {snapshot.inspected} | Uninspected {snapshot.uninspected}")
        if event.cycle_started:
            self._log(
                "Cycle complete",
                "Both marks disappeared and returned",
                f"{event.timestamp:.3f} s",
                event.observed_cycles,
                str(event.frame_id),
            )
        if record is not None:
            self._log(
                "Inspection position",
                record.reason,
                f"{event.timestamp:.3f} s",
                event.observed_cycles,
                str(event.frame_id),
            )

    def _show_verdict(self, outcome: InspectionOutcome, text: str) -> None:
        colors = {
            InspectionOutcome.NO_DEFECT: "green",
            InspectionOutcome.NEEDS_REVIEW: "yellow",
            InspectionOutcome.DEFECT: "red",
            InspectionOutcome.UNINSPECTED: "grey",
        }
        color = colors[outcome]
        self.condition.setProperty("verdict_color", color)
        self.condition.style().unpolish(self.condition)
        self.condition.style().polish(self.condition)
        self.condition.setText(text)
        self.condition.setAccessibleName(f"Latest cycle verdict: {color}, {text}")

    def _update_defect_alarm(self) -> None:
        self.defect_alarm.setText(
            f"DEFECT ALARM: {self.controller.defects} (latched)"
            if self.controller.defects
            else "No latched defect alarm"
        )
        self.defect_alarm.setProperty("latched", bool(self.controller.defects))
        self.defect_alarm.style().unpolish(self.defect_alarm)
        self.defect_alarm.style().polish(self.defect_alarm)
        self.defect_alarm.setAccessibleName(self.defect_alarm.text())

    def _poll_live_inspection(self) -> None:
        self._render_inspections(self.controller.poll_inspections())

    def _render_inspections(self, batch: InspectionPoll) -> None:
        for reason in batch.rejected:
            self._set_notice(reason)
        for update in batch.updates:
            detection = update.detection
            ticket = update.ticket
            evidence_saved = update.evidence_saved
            self._update_defect_alarm()
            verdict = update.verdict
            label = verdict.value
            score = f"{detection.result.score:.3f}" if detection.result is not None else "-"
            if update.is_latest:
                self._show_verdict(verdict, f"Cycle {ticket.cycle} - {label}")
                if detection.result is not None:
                    result = detection.result
                    image = _inspection_overlay(result)
                    left, top, right, bottom = result.settings.region.pixels(image)
                    self.roi_view._display_image(image[top:bottom, left:right])
                    self.roi_view.update()
                    self.roi_caption.setText(f"Cycle {ticket.cycle} ROI - {label}")
                else:
                    self.roi_view.clear()
                    self.roi_caption.setText(f"Cycle {ticket.cycle} ROI - unavailable")
                evidence_label = "Failure record saved" if detection.evidence is not None else "Unavailable"
                self.evidence.setText("Saved" if evidence_saved else evidence_label)
                self.evidence.setToolTip(
                    str(detection.evidence) if detection.evidence is not None else detection.reason
                )
                self.assessment_note.setText(
                    f"UNQUALIFIED | Score {score} | Threshold {update.binding.threshold:.3f} "
                    f"| Review margin {update.binding.review_margin:.3f}"
                )
                self.condition.setToolTip(detection.reason or self.assessment_note.text())
            if detection.reason:
                self._set_notice(detection.reason)
            self._log(
                "Cycle inspection",
                f"{label} | Score {score} | {detection.reason}",
                f"{ticket.timestamp:.3f} s",
                ticket.cycle,
                str(ticket.frame_id),
            )
        if batch.failure:
            self._set_notice(batch.failure)
            if not self.controller.latest_cycle:
                self._show_verdict(InspectionOutcome.UNINSPECTED, "Unknown - Detector / evidence unavailable")
                self.assessment_note.setText(f"UNQUALIFIED | {batch.failure}")
            if batch.unavailable is not None:
                ticket = batch.unavailable
                self._show_verdict(InspectionOutcome.UNINSPECTED, f"Cycle {ticket.cycle} - Uninspected")
                self.evidence.setText("Unavailable")
                self.roi_view.clear()
                self.roi_caption.setText(f"Cycle {ticket.cycle} ROI - unavailable")
        snapshot = self.controller.lifecycle.snapshot
        self.inspected_cycles.setText(
            f"Inspected {snapshot.inspected} | Uninspected {snapshot.uninspected} "
            f"| Pending {len(self.controller.tickets)}"
        )
        self._refresh_operator_status()

    def poll(self) -> None:
        """Consume bounded worker data on the UI thread without blocking capture."""
        self._refresh_operator_status()
        if self.controller.mode == "camera":
            self._poll_camera()
            return
        if self.controller.pipeline is None:
            return
        batch = self.controller.poll_replay(monotonic())
        self._render_source_poll(batch)
        preview = batch.preview
        if preview is not None:
            self.live_view.display(preview, self.recipe)
            self.frame_label.setText(
                f"Frame {preview.frame.frame_id:06d} | source {preview.frame.timestamp:.3f} s",
            )
            self.cycles.setText(str(preview.result.observed_cycles))
            self.sync.setText(preview.result.state.value)
        status = self.controller.pipeline.status
        self.health.setText(status.state)
        freshness = "Replay | not a live camera" if self.controller.pipeline.alive else "Stopped | last replay frame"
        self.freshness.setText(freshness)
        self._set_notice(status.reason if status.state == "Fault" else "")
        if status.state in {"Fault", "Ended", "Stopped"} and status.state != self._last_status:
            self._log(
                status.state, status.reason, datetime.now(UTC).strftime("%H:%M:%S UTC"), int(self.cycles.text()), "-"
            )
        self._last_status = status.state
        if not self.controller.pipeline.alive:
            self._set_running(False)
            if self.controller.closing:
                self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request asynchronous worker shutdown rather than freezing the window.

        Args:
            event (QCloseEvent): Qt close notification.
        """
        if self._training_dialog is not None and self._training_dialog._training():
            self._set_notice("Training is active. Wait for completion or cancel it before exiting.")
            event.ignore()
            return
        collecting = self._training_dialog is not None and self._training_dialog._capturing()
        if collecting:
            self._training_dialog._stop_collection()
        if self.controller.close_ready(monotonic(), collecting=collecting):
            self._timer.stop()
            event.accept()
            return
        if self.controller.request_close(monotonic()):
            QTimer.singleShot(3100, self.close)
        if self.controller.stopping_trial is not None:
            self._finalize_marker_stop()
        self._set_notice("Closing source | waiting for worker shutdown.")
        event.ignore()

    def _basic_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Basic settings")
        dialog.setMinimumWidth(320)
        layout = QVBoxLayout(dialog)
        appearance = QFormLayout()
        display_mode = QComboBox()
        display_mode.setAccessibleName("Display mode")
        display_mode.addItems(["Dark", "Light"])
        preferences = QSettings("Anomalib", "RDT Reliability Monitor")
        display_mode.setCurrentText("Light" if preferences.value("display_mode", "dark") == "light" else "Dark")

        def change_display_mode(text: str) -> None:
            mode = text.lower()
            preferences.setValue("display_mode", mode)
            _apply_display_mode(mode)

        display_mode.currentTextChanged.connect(change_display_mode)
        appearance.addRow("Display mode", display_mode)
        layout.addLayout(appearance)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()


def _apply_display_mode(mode: str) -> None:
    application = QApplication.instance()
    if application is not None:
        application.setProperty("display_mode", mode)
        stylesheet = files("rdt_monitor").joinpath("styles.qss").read_text(encoding="utf-8")
        if mode == "light":
            stylesheet += files("rdt_monitor").joinpath("styles-light.qss").read_text(encoding="utf-8")
        application.setStyleSheet(stylesheet)
        for verdict in application.allWidgets():
            if isinstance(verdict, _VerdictLabel):
                verdict.setProperty("surface_color", "#FFFFFF" if mode == "light" else "#09131C")
                verdict.update()


def main() -> int:
    """Launch the reliability monitor with inspection qualification gates intact.

    Returns:
        int: Qt application exit status.
    """
    application = QApplication(sys.argv)
    application.setStyle("Fusion")
    families = set(QFontDatabase.families())
    family = next((name for name in ("IBM Plex Sans", "Inter") if name in families), application.font().family())
    application.setFont(QFont(family, 10))
    preferences = QSettings("Anomalib", "RDT Reliability Monitor")
    _apply_display_mode(str(preferences.value("display_mode", "dark")))
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
