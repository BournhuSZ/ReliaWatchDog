# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Legibility of semantic state text in the shared light/dark palette."""

import pytest

from rdt_monitor.palette import colors


def luminance(color):
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear, strict=True))


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize(
    "foreground, background",
    [
        ("text", "surface"),
        ("secondary", "background"),
        ("startText", "start"),
        ("stopText", "stop"),
        ("normal", "verdictSurface"),
        ("review", "verdictSurface"),
        ("defect", "verdictSurface"),
        ("neutral", "verdictSurface"),
        ("normal", "normalSurface"),
        ("review", "reviewSurface"),
        ("defect", "defectSurface"),
        ("neutral", "neutralSurface"),
        ("accent", "accentSurface"),
        ("startMarker", "overlayBackground"),
        ("positionMarker", "overlayBackground"),
        ("inspectionRoi", "overlayBackground"),
        ("startText", "startHover"),
        ("startText", "startPressed"),
        ("stopText", "stopHover"),
        ("text", "buttonHover"),
        ("text", "buttonPressed"),
        ("disabledText", "disabledSurface"),
    ],
)
def test_semantic_text_has_at_least_normal_text_contrast(light, foreground, background):
    palette = colors(light)
    lighter, darker = sorted((luminance(palette[foreground]), luminance(palette[background])), reverse=True)
    assert (lighter + 0.05) / (darker + 0.05) >= 4.5
