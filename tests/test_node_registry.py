"""Tests for the node registry (node_registry.py)."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.node_registry import NodeRegistry

_LOCAL_NODE_SOURCE = '''
NODE_ID = "normalization/demo_local"
STAGE = "normalization"
VERSION = "v1"
MANIFEST = {"id": "normalization/demo_local", "version": "v1", "stage": "normalization"}
NODE_ENV = None
SELECTORS = {"normalizer": "demo_local"}

def build(**config):
    def node(files):
        return files, None
    return node
'''


def _fake_plugin_module() -> types.ModuleType:
    module = types.ModuleType("fake_plugin")
    module.NODE_ID = "normalization/demo_norm"
    module.STAGE = "normalization"
    module.VERSION = "v1"
    module.MANIFEST = {"id": "normalization/demo_norm", "version": "v1", "stage": "normalization"}
    module.NODE_ENV = None
    module.SELECTORS = {"normalizer": "demo_norm"}
    module.build = lambda **config: (lambda files: (files, None))
    return module


def test_resolve_builtin() -> None:
    registration = NodeRegistry().resolve("normalization/whisper_norm")
    assert registration.source == "builtin"
    assert registration.stage == "normalization"
    assert registration.version == "v1"
    assert registration.manifest["id"] == "normalization/whisper_norm"


def test_resolve_builtin_alias() -> None:
    reg = NodeRegistry()
    # scoring/wenet_cer is an alias of scoring/wenet_wer
    registration = reg.resolve("scoring/wenet_cer")
    assert registration.source == "builtin"
    assert registration.manifest["id"] == "scoring/wenet_wer"
    assert "wenet_wer" in str(reg.manifest_path("scoring/wenet_cer"))


def test_manifest_and_path_builtin() -> None:
    reg = NodeRegistry()
    assert reg.manifest("normalization/whisper_norm")["id"] == "normalization/whisper_norm"
    path = reg.manifest_path("normalization/whisper_norm")
    assert path.exists()
    assert path.name == "manifest.yaml"


def test_manifest_unknown_raises() -> None:
    with pytest.raises(KeyError):
        NodeRegistry().manifest("normalization/does_not_exist")


def test_node_env_builtin() -> None:
    reg = NodeRegistry()
    assert reg.node_env("normalization/nemo_norm") is not None
    assert reg.node_env("normalization/whisper_norm") is None


def test_iter_node_ids_contains_builtin() -> None:
    ids = NodeRegistry().iter_node_ids()
    assert "normalization/whisper_norm" in ids
    assert "scoring/wenet_wer" in ids
    assert len(ids) >= 30


def test_iter_env_node_ids() -> None:
    ids = NodeRegistry().iter_env_node_ids()
    assert "normalization/nemo_norm" in ids
    assert "normalization/whisper_norm" not in ids  # in-process node


def test_find_node_by_name() -> None:
    reg = NodeRegistry()
    assert reg.find_node_by_name("scoring", "wenet_wer") == "scoring/wenet_wer"
    assert reg.find_node_by_name("scoring", "does_not_exist") is None


def test_resolve_local_file(tmp_path: Path) -> None:
    node_file = tmp_path / "demo_local.py"
    node_file.write_text(_LOCAL_NODE_SOURCE, encoding="utf-8")

    registration = NodeRegistry().resolve(
        "normalization/demo_local", local_paths=[node_file]
    )
    assert registration.source == "local"
    assert registration.node_id == "normalization/demo_local"
    assert callable(registration.build)
    assert registration.selectors == {"normalizer": "demo_local"}


def test_resolve_local_directory(tmp_path: Path) -> None:
    pkg = tmp_path / "demo_pkg"
    pkg.mkdir()
    (pkg / "node.py").write_text(_LOCAL_NODE_SOURCE, encoding="utf-8")

    registration = NodeRegistry().resolve("normalization/demo_local", local_paths=[pkg])
    assert registration.source == "local"


def test_resolve_unknown_raises() -> None:
    with pytest.raises(KeyError):
        NodeRegistry().resolve("normalization/does_not_exist")


def test_find_by_selector(monkeypatch) -> None:
    module = _fake_plugin_module()
    reg = NodeRegistry()
    monkeypatch.setattr(
        reg, "iter_entry_point_specs", lambda: [("normalization/demo_norm", "fake_plugin")]
    )
    monkeypatch.setattr(nr, "_import_cached", lambda name: module)

    assert reg.find_by_selector("normalization", "normalizer", "demo_norm") == (
        "normalization/demo_norm"
    )
    assert reg.find_by_selector("normalization", "normalizer", "other") is None
    assert reg.find_by_selector("scoring", "normalizer", "demo_norm") is None


def test_resolve_entry_point(monkeypatch) -> None:
    module = _fake_plugin_module()
    reg = NodeRegistry()
    monkeypatch.setattr(
        reg, "iter_entry_point_specs", lambda: [("normalization/demo_norm", "fake_plugin")]
    )
    monkeypatch.setattr(nr, "_import_cached", lambda name: module)

    registration = reg.resolve("normalization/demo_norm")
    assert registration.source == "entry_point"
    assert callable(registration.build)


def test_resolve_prioritizes_builtin_over_entry_point(monkeypatch) -> None:
    """A builtin node wins over an entry point with the same name."""

    module = _fake_plugin_module()
    reg = NodeRegistry()
    monkeypatch.setattr(
        reg,
        "iter_entry_point_specs",
        lambda: [("normalization/whisper_norm", "fake_plugin")],
    )
    monkeypatch.setattr(nr, "_import_cached", lambda name: module)

    registration = reg.resolve("normalization/whisper_norm")
    assert registration.source == "builtin"
