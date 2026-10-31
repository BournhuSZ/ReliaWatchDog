# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Read-only catalog of explicitly selected local recipe files."""

from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from rdt_monitor.paths import RECIPE_DIRECTORY
from rdt_monitor.recipes import load_recipe


class RecipeLibrary(QObject):
    """Browse saved recipes without activating a detector or changing a run."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.directory = RECIPE_DIRECTORY
        self._entries = []
        self._family = ""
        self._uut = ""
        self._selected = ""
        self._message = ""

    @Property("QVariantMap", notify=changed)
    def status(self) -> dict[str, object]:
        """Return grouped, selection-aware metadata for the catalog."""
        families = sorted({row["family"] for row in self._entries}, key=str.casefold)
        rows = [row for row in self._entries if row["family"] == self._family]
        uuts = sorted({row["uut"] for row in rows}, key=str.casefold)
        revisions = [row for row in rows if row["uut"] == self._uut]
        selected = next((row for row in revisions if row["path"] == self._selected), None)
        return dict(
            directory=str(self.directory),
            families=families,
            family=self._family,
            uuts=uuts,
            uut=self._uut,
            revisions=revisions,
            selected=self._selected,
            detail=selected or {},
            message=self._message,
        )

    @Slot()
    def refresh(self) -> None:
        """Scan only direct JSON children; skip invalid files and report their count."""
        entries = []
        invalid = 0
        try:
            files = sorted(self.directory.glob("*.json"), key=lambda p: p.name.casefold())
            if len(files) > 2000:
                files = files[:2000]
                self._message = "Showing the first 2,000 JSON files."
            else:
                self._message = ""
            for path in files:
                try:
                    recipe = load_recipe(path)
                except (ValueError, OSError):
                    invalid += 1
                    continue
                specimen = recipe.specimen
                family = specimen.family or "Unassigned family"
                uut = specimen.name or "Unnamed UUT"
                if specimen.article_number:
                    uut += " · " + specimen.article_number
                detector = recipe.detector
                entries.append(
                    dict(
                        path=str(path.resolve()),
                        filename=path.name,
                        family=family,
                        uut=uut,
                        name=specimen.name or "Not recorded",
                        article=specimen.article_number or "Not recorded",
                        batch=specimen.batch or "Not recorded",
                        recipe=recipe.profile.name if recipe.profile else path.stem,
                        mode=recipe.mode_label,
                        model=detector.model_file if detector else "No detector · counting only",
                        digest=detector.model_sha256 if detector else "—",
                        threshold=f"{detector.threshold:.3f} ± {detector.review_margin:.3f}" if detector else "—",
                        dimensions=f"{recipe.image_width} × {recipe.image_height}",
                    )
                )
        except OSError as error:
            self._message = f"Cannot read recipe folder: {error}"
        if invalid:
            self._message += ("  " if self._message else "") + f"{invalid} invalid or unreadable JSON file(s) skipped."
        self._entries = entries
        self._family = self._uut = self._selected = ""
        self.changed.emit()

    @Slot(str)
    def setDirectory(self, directory: str) -> None:
        """Select a folder and refresh its catalog."""
        self.directory = Path(directory)
        self.refresh()

    @Slot(str)
    def selectFamily(self, family: str) -> None:
        """Select one family and clear dependent selections."""
        self._family, self._uut, self._selected = family, "", ""
        self.changed.emit()

    @Slot(str)
    def selectUut(self, uut: str) -> None:
        """Select one UUT and clear the recipe revision selection."""
        self._uut, self._selected = uut, ""
        self.changed.emit()

    @Slot(str)
    def selectRecipe(self, path: str) -> None:
        """Select only a recipe present in the current family and UUT."""
        if any(
            row["path"] == path and row["family"] == self._family and row["uut"] == self._uut for row in self._entries
        ):
            self._selected = path
            self.changed.emit()
