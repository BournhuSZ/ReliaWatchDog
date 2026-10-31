# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Source-aligned presentation copies, independent of Qt and inference preprocessing."""

import numpy as np

from rdt_monitor.detection import DetectionResult
from rdt_monitor.replay import Image


def inspection_overlay(result: DetectionResult) -> Image:
    """Apply the existing Widgets heatmap to a copy of its exact inspected source.

    Args:
        result (DetectionResult): Validated score/map and immutable source snapshot.

    Returns:
        Image: Original-size BGR copy; only the inspected ROI is annotated.
    """
    image = result.source_image.copy()
    left, top, right, bottom = result.settings.region.pixels(image)
    colors = np.zeros((bottom - top, right - left, 3), dtype=np.float32)
    colors[..., 2] = 255
    colors[..., 1] = 115 * (1 - result.anomaly_map)
    alpha = (result.anomaly_map * 0.6)[..., None]
    image[top:bottom, left:right] = np.clip(
        image[top:bottom, left:right] * (1 - alpha) + colors * alpha, 0, 255
    ).astype(np.uint8)
    return image
