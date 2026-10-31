# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Atomic edits of counting-only replay settings using the existing domain contracts."""

from dataclasses import replace
from math import isfinite

from rdt_monitor.domain import SyncConfig
from rdt_monitor.replay import Region, ReplayRecipe


def settings_values(recipe: ReplayRecipe) -> dict[str, object]:
    """Return editable replay values without exposing mutable domain state.

    Args:
        recipe (ReplayRecipe): Committed replay-only configuration.

    Returns:
        dict[str, object]: Normalized regions, source-time limits and brightness.
    """
    return dict(
        start=[recipe.marker.left, recipe.marker.top, recipe.marker.width, recipe.marker.height],
        position=[recipe.position.left, recipe.position.top, recipe.position.width, recipe.position.height],
        stable=recipe.synchronization.stable_frames,
        gap=recipe.synchronization.max_gap_seconds,
        timeout=recipe.synchronization.cycle_timeout_seconds,
        brightness=recipe.brightness,
    )


def parse_settings(values: dict[str, object], original: ReplayRecipe) -> ReplayRecipe:
    """Validate an entire numeric replay edit before replacing the committed recipe.

    Args:
        values (dict[str, object]): QML form values; region coordinates are normalized.
        original (ReplayRecipe): Current replay recipe, independent of live recipes.

    Returns:
        ReplayRecipe: Valid legacy-brightness replay configuration.

    Raises:
        ValueError: If any input is missing, malformed or rejected by domain validation.
    """
    try:
        integers = {}
        for name in ("stable", "brightness"):
            number = float(values[name])
            if isinstance(values[name], bool) or not isfinite(number) or not number.is_integer():
                raise ValueError(f"{name.capitalize()} must be a finite whole number")
            integers[name] = int(number)
        return replace(
            original,
            marker=Region(*(float(value) for value in values["start"])),
            position=Region(*(float(value) for value in values["position"])),
            synchronization=SyncConfig(integers["stable"], float(values["gap"]), float(values["timeout"])),
            brightness=integers["brightness"],
            marker_appearance=None,
            position_appearance=None,
        )
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("Provide both X/Y/width/height regions and all replay timing/brightness values") from error
