# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Semantic colors matching the effective Widgets styles and shared QML presentation."""

_DARK = {
    "background": "#0A1017",
    "surface": "#101A25",
    "raised": "#1E222B",
    "imageBackground": "#080D14",
    "border": "#2A3A4B",
    "text": "#E8EAED",
    "heading": "#F5F8FC",
    "secondary": "#9EADBD",
    "disabledText": "#9AA0A6",
    "disabledSurface": "#171A21",
    "focus": "#69A9F5",
    "accent": "#60A5FA",
    "accentSurface": "#20394E",
    "button": "#1E222B",
    "buttonHover": "#2A2F3A",
    "buttonPressed": "#171A21",
    "start": "#195C32",
    "startHover": "#227542",
    "startPressed": "#124526",
    "startText": "#F4FFF7",
    "startBorder": "#389C59",
    "stop": "#2A1A1F",
    "stopHover": "#3A2027",
    "stopText": "#FFD2D7",
    "stopBorder": "#74434C",
    "normal": "#4ADE80",
    "normalSurface": "#14291F",
    "normalBorder": "#2B6841",
    "review": "#FACC15",
    "reviewSurface": "#302515",
    "reviewBorder": "#795A25",
    "defect": "#FB7185",
    "defectSurface": "#2A1A1F",
    "defectBorder": "#74434C",
    "neutral": "#A7B4C4",
    "neutralSurface": "#1B2734",
    "neutralBorder": "#344454",
    "verdictSurface": "#09131C",
    "startMarker": "#3B82F6",
    "positionMarker": "#EAB308",
    "inspectionRoi": "#22D3EE",
}

_LIGHT = {
    "background": "#EEF1F5",
    "surface": "#FFFFFF",
    "raised": "#F0F3F6",
    "imageBackground": "#E3E7EC",
    "border": "#C5CED8",
    "text": "#20242A",
    "heading": "#172433",
    "secondary": "#5E6A77",
    "disabledText": "#626B76",
    "disabledSurface": "#EAEDF1",
    "focus": "#2673C5",
    "accent": "#175DA8",
    "accentSurface": "#DCEAFB",
    "button": "#FFFFFF",
    "buttonHover": "#E5E9EE",
    "buttonPressed": "#D7DDE5",
    "start": "#237646",
    "startHover": "#2A8350",
    "startPressed": "#1B5F39",
    "startText": "#FFFFFF",
    "startBorder": "#237646",
    "stop": "#FFF5F6",
    "stopHover": "#FCE6E9",
    "stopText": "#8E293B",
    "stopBorder": "#D7A2AA",
    "normal": "#21683B",
    "normalSurface": "#EAF6EE",
    "normalBorder": "#9BC9A8",
    "review": "#795411",
    "reviewSurface": "#FFF5DF",
    "reviewBorder": "#E5C77E",
    "defect": "#B4233C",
    "defectSurface": "#FFF5F6",
    "defectBorder": "#D7A2AA",
    "neutral": "#535D69",
    "neutralSurface": "#F0F3F6",
    "neutralBorder": "#C7D0D9",
    "verdictSurface": "#FFFFFF",
    "startMarker": "#3B82F6",
    "positionMarker": "#EAB308",
    "inspectionRoi": "#22D3EE",
}


def colors(light: bool) -> dict[str, str]:
    """Return a copy of the workstation palette without exposing mutable global tokens.

    Args:
        light (bool): Whether to use the existing light display mode.

    Returns:
        dict[str, str]: Semantic colors; verdict colors never determine a domain outcome.
    """
    palette = (_LIGHT if light else _DARK).copy()
    palette["overlayBackground"] = "#080D14"
    return palette
