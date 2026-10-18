# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Explicit, operator-confirmed PatchCore training in a separate Python process."""

import argparse
import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from rdt_monitor.collection import atomic_bytes, load_document, local_file, save_document
from rdt_monitor.replay import Image

logger = logging.getLogger(__name__)


def _read_image(path: Path, digest: str) -> tuple[bytes, Image]:
    with path.open("rb") as stream:
        content = stream.read(128 * 1024 * 1024 + 1)
    if len(content) > 128 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Collected image changed or exceeds the file limit; review a new collection")
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError("Collected image cannot be decoded")
    return content, image


def _fit_patchcore(dataset: Path, output: Path) -> Path:
    os.environ["HF_HUB_DISABLE_XET"] = "1"

    from anomalib.data import Folder
    from anomalib.engine import Engine
    from anomalib.models import Patchcore

    from rdt_monitor.detection import DetectionSettings, inspect_image, load_exported_model
    from rdt_monitor.replay import Region

    data = Folder(
        name="rdt-normal-baseline",
        root=dataset,
        normal_dir="good",
        train_batch_size=4,
        eval_batch_size=4,
        num_workers=0,
        seed=42,
    )
    weights = (
        Path(__file__).resolve().parents[4] / "models" / "backbones" / "wide_resnet50_2.racm_in1k" / "model.safetensors"
    )
    if weights.exists():
        import timm

        logger.info("Loading local pretrained PatchCore backbone: %s", weights)
        backbone = timm.create_model(
            "wide_resnet50_2.racm_in1k",
            pretrained=True,
            pretrained_cfg_overlay={"file": str(weights), "hf_hub_id": None, "url": None},
            features_only=True,
            exportable=True,
            out_indices=(2, 3),
        )
        model = Patchcore(pre_trained=False)
        extractor = model.model.feature_extractor
        if backbone.feature_info.channels() != list(extractor.out_dims):
            raise ValueError("Local backbone feature dimensions do not match PatchCore")
        extractor.feature_extractor = backbone
        logger.info("Local pretrained backbone loaded successfully; no Hugging Face download required")
    else:
        logger.info("Loading pretrained PatchCore backbone via HTTPS (Xet disabled); first use may download weights")
        model = Patchcore()
    engine = Engine(
        accelerator="cpu", devices=1, default_root_dir=output / "fit", logger=False, enable_checkpointing=True
    )
    logger.info("Fitting normal-only PatchCore baseline; this does not validate defect detection accuracy")
    engine.fit(model=model, datamodule=data)
    exported = engine.export(
        model=model,
        export_type="openvino",
        export_root=output,
        input_size=(256, 256),
        onnx_kwargs={"opset_version": 18, "dynamic_axes": {}, "dynamic_shapes": ({},)},
    )
    if exported is None:
        raise ValueError("Training did not produce an exported model")
    exported = Path(exported).resolve()
    if not exported.is_relative_to(output.resolve()):
        raise ValueError("Exported model escaped its training job directory")
    first = next((dataset / "good").glob("*.png"))
    sample = cv2.imdecode(np.frombuffer(first.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    inspect_image(sample, DetectionSettings(exported, Region(0, 0, 1, 1), 0.5), load_exported_model(exported))
    return exported


def train_collection(
    manifest: Path,
    expected_sha256: str,
    confirmed_undamaged: bool,
    backend: Callable[[Path, Path], Path] = _fit_patchcore,
) -> Path:
    """Train a new version only after explicit review and source/crop integrity checks.

    Args:
        manifest (Path): Complete collected candidate metadata selected for review.
        expected_sha256 (str): Metadata digest frozen when the operator chose Train.
        confirmed_undamaged (bool): Explicit user confirmation; never inferred from cycle count.
        backend (Callable[[Path, Path], Path]): Training/export adapter; injectable for offline contract tests.

    Returns:
        Path: New exported model; never activates or replaces a live detector.

    Raises:
        ValueError: If confirmation, completion, integrity or export confinement fails.
        OSError: If verified inputs or output files cannot be read or written.
        ImportError: If the separate environment lacks Anomalib training/export dependencies.
        RuntimeError: If fitting or exporting fails.
    """
    if confirmed_undamaged is not True:
        raise ValueError("Explicit confirmation that the collected specimen remained undamaged is required")
    document = load_document(manifest, expected_sha256=expected_sha256)
    if document.state not in {"Awaiting confirmation", "Confirmed normal"}:
        raise ValueError("Only a complete collected candidate set may be confirmed and trained")
    if document.settings.width * document.settings.height > 32_000_000:
        raise ValueError("Training collection source exceeds the 32 megapixel limit")
    crops: list[bytes] = []
    for record in document.records:
        _, source = _read_image(local_file(manifest.parent, record.source_file), record.source_sha256)
        crop_content, crop = _read_image(local_file(manifest.parent, record.crop_file), record.crop_sha256)
        if source.shape != (document.settings.height, document.settings.width, 3):
            raise ValueError("Collected source dimensions changed")
        left, top, right, bottom = document.settings.region.pixels(source)
        if not np.array_equal(crop, source[top:bottom, left:right]):
            raise ValueError("Training crop does not match its exact source image and ROI")
        crops.append(crop_content)
        if sum(map(len, crops)) > 256 * 1024 * 1024:
            raise ValueError("Training crop snapshot exceeds 256 MiB")
    job = local_file(manifest.parent, f"training-{uuid4().hex}")
    job.mkdir(exist_ok=False)
    good = local_file(job, "good")
    good.mkdir(exist_ok=False)
    for index, crop in enumerate(crops, 1):
        atomic_bytes(local_file(good, f"crop-{index:04d}.png"), crop)
    confirmed = replace(document, state="Confirmed normal", confirmed_at=datetime.now(UTC).isoformat())
    save_document(manifest.parent, confirmed)
    atomic_bytes(
        local_file(job, "dataset.json"),
        json.dumps(
            {
                "source_manifest_sha256": expected_sha256,
                "confirmation": "Explicit operator confirmation, not authenticated approval or defect qualification",
                "collection": asdict(confirmed),
            },
            indent=2,
            allow_nan=False,
        ).encode("utf-8"),
    )
    logger.info("Verified %s candidate source/crop pairs; explicit normal confirmation recorded", len(crops))
    exported = backend(job, job).resolve()
    if not exported.is_file() or not exported.is_relative_to(job.resolve()):
        raise ValueError("Training did not return a model confined to its new job directory")
    logger.info("Export ready for offline inspection only: %s", exported)
    return exported


def main() -> int:
    """Run a manually requested collection training/export job outside Qt.

    Returns:
        int: Zero only after training, export and artifact checks succeed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--confirm-undamaged", action="store_true")
    parser.add_argument("--result", type=Path, required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        folder = arguments.collection.parent.resolve()
        result_path = local_file(folder, arguments.result.name)
        if arguments.result.parent.resolve() != folder or result_path.exists():
            raise ValueError("Training result must be a new file inside the selected collection")
        exported = train_collection(arguments.collection, arguments.sha256, arguments.confirm_undamaged)
        atomic_bytes(result_path, json.dumps({"model": str(exported.relative_to(folder)), "qualified": False}).encode())
    except (ImportError, OSError, ValueError, RuntimeError, TypeError, cv2.error) as error:
        logger.error("Training unavailable or failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
