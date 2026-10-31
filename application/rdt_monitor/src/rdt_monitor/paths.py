# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Existing monitor data locations shared by both presentation entry points."""

from pathlib import Path

RECIPE_DIRECTORY = Path(__file__).resolve().parents[4] / "recipes"
INSPECTION_DIRECTORY = Path(__file__).resolve().parents[4] / "inspection-results"
