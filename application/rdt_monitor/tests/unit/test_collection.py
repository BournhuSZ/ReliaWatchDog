# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import builtins
import hashlib
import json
import os
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from rdt_monitor.collection import CandidateCollection, CollectionSettings, atomic_bytes, load_document, local_file
from rdt_monitor.domain import SyncResult, SyncState
from rdt_monitor.replay import Region
from rdt_monitor.training import train_collection


def test_training_disables_xet_before_importing_model_dependencies(monkeypatch, tmp_path):
    from rdt_monitor.training import _fit_patchcore

    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "anomalib.data":
            assert os.environ.get("HF_HUB_DISABLE_XET") == "1"
            raise ImportError("Verified training dependency import boundary")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    with pytest.raises(ImportError, match="Verified training dependency import boundary"):
        _fit_patchcore(tmp_path, tmp_path)


@pytest.mark.parametrize("invalid", [False, True])
def test_training_uses_local_backbone_without_network_fallback(monkeypatch, tmp_path, invalid):
    import rdt_monitor.training as training

    module = tmp_path / "application" / "rdt_monitor" / "src" / "rdt_monitor" / "training.py"
    monkeypatch.setattr(training, "__file__", str(module))
    weights = tmp_path / "models" / "backbones" / "wide_resnet50_2.racm_in1k" / "model.safetensors"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"injected local-weight contract")
    backbone = SimpleNamespace(feature_info=SimpleNamespace(channels=lambda: [512, 1024]))
    extractor = SimpleNamespace(out_dims=[512, 1024], feature_extractor=None)
    calls = []

    def create(name, **kwargs):
        assert name == "wide_resnet50_2.racm_in1k"
        assert kwargs["pretrained"] is True
        assert kwargs["pretrained_cfg_overlay"] == {"file": str(weights), "hf_hub_id": None, "url": None}
        assert kwargs["out_indices"] == (2, 3)
        if invalid:
            raise ValueError("Invalid local weights")
        return backbone

    def patchcore(**kwargs):
        assert kwargs == {"pre_trained": False}
        calls.append("model")
        return SimpleNamespace(model=SimpleNamespace(feature_extractor=extractor))

    def fit(**kwargs):
        assert extractor.feature_extractor is backbone

    def export(**kwargs):
        assert kwargs["export_type"] == "openvino"
        assert kwargs["input_size"] == (256, 256)
        assert kwargs["onnx_kwargs"] == {
            "opset_version": 18,
            "dynamic_axes": {},
            "dynamic_shapes": ({},),
        }
        raise RuntimeError("Verified local backbone reached single-image export")

    def engine(**kwargs):
        assert kwargs["enable_checkpointing"] is True
        assert kwargs["default_root_dir"] == tmp_path / "fit"
        return SimpleNamespace(fit=fit, export=export)

    monkeypatch.setitem(sys.modules, "timm", SimpleNamespace(create_model=create))
    monkeypatch.setitem(sys.modules, "anomalib.data", SimpleNamespace(Folder=lambda **kwargs: None))
    monkeypatch.setitem(sys.modules, "anomalib.engine", SimpleNamespace(Engine=engine))
    monkeypatch.setitem(sys.modules, "anomalib.models", SimpleNamespace(Patchcore=patchcore))
    if invalid:
        with pytest.raises(ValueError, match="Invalid local weights"):
            training._fit_patchcore(tmp_path, tmp_path)
        assert calls == []
    else:
        with pytest.raises(RuntimeError, match="single-image export"):
            training._fit_patchcore(tmp_path, tmp_path)
        assert calls == ["model"]


def settings():
    return CollectionSettings("../specimen-metadata-only", "run-1", "sync-v1", Region(0.1, 0.2, 0.5, 0.6), 20, 10, 20)


def completion(cycle):
    return SyncResult(cycle * 10, float(cycle), SyncState.TRACKING, cycle, True, True, "Cycle complete")


def collect(tmp_path, mode="auto"):
    writer = CandidateCollection(tmp_path, replace(settings(), cycle_mode=mode), capacity=32)
    writer.start()
    for cycle in range(1, 21):
        image = np.full((10, 20, 3), cycle, dtype=np.uint8)
        if mode == "manual":
            assert writer.capture_manual(image, cycle * 10, float(cycle))
        else:
            writer.capture(image, completion(cycle))
    assert writer.finished.wait(5)
    return writer


def test_collection_exact_frames_and_crops_stop_at_target_without_normal_labels(tmp_path):
    writer = collect(tmp_path)
    document = load_document(writer.folder / "collection.json")
    assert document.state == "Awaiting confirmation"
    assert document.confirmed_at is None
    assert len(document.records) == 20
    assert document.settings.specimen_id == "../specimen-metadata-only"
    assert writer.folder.parent == tmp_path.resolve()
    for index, record in enumerate(document.records, 1):
        source = cv2.imread(str(writer.folder / record.source_file))
        crop = cv2.imread(str(writer.folder / record.crop_file))
        assert np.all(source == index) and source.shape == (10, 20, 3)
        assert np.all(crop == index) and crop.shape == (6, 10, 3)
        assert record.frame_id == index * 10
    writer.capture(np.zeros((10, 20, 3), dtype=np.uint8), completion(21))
    assert len(writer.document.records) == 20


def test_manual_collection_saves_only_explicit_distinct_frames_and_persists_mode(tmp_path):
    writer = CandidateCollection(tmp_path, replace(settings(), cycle_mode="manual"), capacity=32)
    writer.start()
    try:
        with pytest.raises(ValueError, match="auto cycle mode"):
            writer.capture(np.zeros((10, 20, 3), dtype=np.uint8), completion(1))
        for cycle in range(1, 21):
            image = np.full((10, 20, 3), cycle, dtype=np.uint8)
            assert writer.capture_manual(image, cycle * 10, float(cycle))
            assert not writer.capture_manual(image, cycle * 10, float(cycle))
        assert not writer.capture_manual(image, 999, 999.0)
        assert writer.finished.wait(5)
        document = load_document(writer.folder / "collection.json")
        assert document.settings.cycle_mode == "manual"
        assert document.state == "Awaiting confirmation"
        assert document.confirmed_at is None
        assert len(document.records) == 20
        for cycle, record in enumerate(document.records, 1):
            assert record.cycle == cycle
            assert record.frame_id == cycle * 10
            assert np.all(cv2.imread(str(writer.folder / record.source_file)) == cycle)
    finally:
        writer.interrupt("Test cleanup")
        assert writer.finished.wait(5)


def test_legacy_collection_defaults_to_automatic_cycle_mode(tmp_path):
    writer = collect(tmp_path)
    path = writer.folder / "collection.json"
    raw = asdict(writer.document)
    del raw["settings"]["cycle_mode"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_document(path).settings.cycle_mode == "auto"
    raw["settings"]["cycle_mode"] = "invalid"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="cycle mode"):
        load_document(path)


def test_automatic_collection_rejects_manual_capture(tmp_path):
    writer = CandidateCollection(tmp_path, settings())
    with pytest.raises(ValueError, match="manual cycle mode"):
        writer.capture_manual(np.zeros((10, 20, 3), dtype=np.uint8), 1, 1.0)


def test_interrupted_collection_is_not_training_eligible(tmp_path):
    writer = CandidateCollection(tmp_path, settings())
    writer.start()
    writer.interrupt("Synchronization lost")
    assert writer.finished.wait(5)
    document = load_document(writer.folder / "collection.json")
    assert document.state == "Incomplete"
    assert document.reason == "Synchronization lost"
    assert document.confirmed_at is None


def test_collection_backlog_fails_closed_without_blocking_capture(tmp_path):
    writer = CandidateCollection(tmp_path, settings(), capacity=1)
    image = np.zeros((10, 20, 3), dtype=np.uint8)
    writer.capture(image, completion(1))
    writer.capture(image, completion(2))
    assert writer.document.state == "Incomplete"
    assert "backlog" in writer.document.reason
    writer.start()
    assert writer.finished.wait(5)
    assert load_document(writer.folder / "collection.json").state == "Incomplete"


@pytest.mark.parametrize(
    "filename", ["../escape.png", "/escape.png", "C:\\escape.png", "..\\escape.png", "\\\\host\\file.png"]
)
def test_collection_rejects_out_of_root_paths(tmp_path, filename):
    with pytest.raises(ValueError):
        local_file(tmp_path, filename)


def test_untrusted_metadata_cannot_invent_confirmation_or_image_paths(tmp_path):
    writer = collect(tmp_path)
    path = writer.folder / "collection.json"
    raw = asdict(writer.document)
    raw["state"] = "Confirmed normal"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="confirmation"):
        load_document(path)
    raw = asdict(writer.document)
    raw["records"][0]["source_file"] = "../escape.png"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="filename"):
        load_document(path)


def test_training_never_starts_without_explicit_undamaged_confirmation(tmp_path):
    writer = collect(tmp_path)
    path = writer.folder / "collection.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    called = []
    with pytest.raises(ValueError, match="Explicit confirmation"):
        train_collection(path, digest, False, lambda *args: called.append(args))
    assert called == []
    assert load_document(path).state == "Awaiting confirmation"


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_confirmed_training_uses_verified_separate_crop_snapshot(tmp_path, mode):
    writer = collect(tmp_path, mode)
    path = writer.folder / "collection.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    called = []

    def backend(dataset, output):
        assert load_document(path).state == "Confirmed normal"
        assert len(list((dataset / "good").glob("*.png"))) == 20
        assert (dataset / "dataset.json").is_file()
        provenance = json.loads((dataset / "dataset.json").read_text(encoding="utf-8"))
        assert provenance["collection"]["settings"]["cycle_mode"] == mode
        called.append(dataset)
        exported = output / "test-contract-not-trained.onnx"
        exported.write_bytes(b"injected export; not a defect model")
        return exported

    exported = train_collection(path, digest, True, backend)
    assert exported.is_file()
    assert len(called) == 1
    assert load_document(path).confirmed_at


@pytest.mark.parametrize("change", ["manifest", "source", "crop"])
def test_changed_candidates_cannot_train_after_confirmation(tmp_path, change):
    writer = collect(tmp_path)
    path = writer.folder / "collection.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = path if change == "manifest" else writer.folder / f"{change}-0001.png"
    target.write_bytes(target.read_bytes() + b"changed")
    called = []
    with pytest.raises(ValueError, match="changed"):
        train_collection(path, digest, True, lambda *args: called.append(args))
    assert called == []


def test_incomplete_collection_cannot_train_even_with_confirmation(tmp_path):
    writer = CandidateCollection(tmp_path, settings())
    writer.start()
    writer.interrupt("Stopped early")
    assert writer.finished.wait(5)
    path = writer.folder / "collection.json"
    with pytest.raises(ValueError, match="complete"):
        train_collection(path, hashlib.sha256(path.read_bytes()).hexdigest(), True)


def test_failed_atomic_replace_preserves_existing_session_file(tmp_path, monkeypatch):
    import rdt_monitor.collection as storage

    path = tmp_path / "collection.json"
    path.write_bytes(b"previous metadata")

    def fail_replace(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        atomic_bytes(path, b"new metadata")
    assert path.read_bytes() == b"previous metadata"
    assert list(tmp_path.iterdir()) == [path]


def test_writer_storage_failure_never_becomes_complete(tmp_path, monkeypatch):
    import rdt_monitor.collection as storage

    def fail_write(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "atomic_bytes", fail_write)
    writer = CandidateCollection(tmp_path, settings())
    writer.start()
    assert writer.finished.wait(5)
    assert writer.document.state == "Incomplete"
    assert "disk full" in writer.document.reason


def test_symlinked_image_cannot_escape_collection(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    try:
        (root / "source-0001.png").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or administrator privileges")
    with pytest.raises(ValueError, match="escapes"):
        local_file(root, "source-0001.png")
