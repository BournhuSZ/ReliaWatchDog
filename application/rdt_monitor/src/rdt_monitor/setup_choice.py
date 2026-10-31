# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Confirmed setup inputs shared by Widgets and Qt Quick presentations."""

from dataclasses import dataclass
from pathlib import Path

from rdt_monitor.recipes import SpecimenDetails, StoredRecipe
from rdt_monitor.replay import ReplayRecipe


@dataclass(frozen=True)
class SetupChoice:
    """Explicit operator choice; controller validation and setup commit remain separate."""

    specimen_id: str
    recipe: ReplayRecipe
    details: SpecimenDetails
    selection: tuple[StoredRecipe, Path] | None
    width: int
    height: int
