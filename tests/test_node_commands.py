"""Tests for the node CLI helpers (node_commands.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sure_eval.evaluation.node_commands import _slugify, create_node, list_nodes


def test_slugify() -> None:
    assert _slugify("My Norm") == "my_norm"
    assert _slugify("My Lower Norm") == "my_lower_norm"
    assert _slugify("  Some_Thing!!  ") == "some_thing"
    assert _slugify("!!!") == "node"


def test_list_nodes_contains_builtin() -> None:
    payload = list_nodes()
    assert payload["count"] >= 30
    node_ids = {node["node_id"] for node in payload["nodes"]}
    assert "normalization/whisper_norm" in node_ids
    assert "scoring/wenet_wer" in node_ids
    builtin = [node for node in payload["nodes"] if node["source"] == "builtin"]
    assert len(builtin) >= 30


def test_create_node_scaffolds_files(tmp_path: Path) -> None:
    payload = create_node("My Norm", stage="normalization", output_dir=tmp_path)

    assert payload["node_id"] == "normalization/my_norm"
    package_dir = Path(payload["package_dir"])
    assert (package_dir / "pyproject.toml").exists()
    assert (package_dir / "sure_eval_plugin.yaml").exists()
    node_py = package_dir / "src" / "sure_eval_node_my_norm" / "node.py"
    assert node_py.exists()

    content = node_py.read_text(encoding="utf-8")
    assert 'NODE_ID = "normalization/my_norm"' in content
    assert "def build(" in content


def test_create_node_scoring_uses_scorer_selector(tmp_path: Path) -> None:
    payload = create_node("My Scorer", stage="scoring", output_dir=tmp_path)
    node_py = Path(payload["package_dir"]) / "src" / "sure_eval_node_my_scorer" / "node.py"
    content = node_py.read_text(encoding="utf-8")
    assert "'scorer': 'my_scorer'" in content


def test_create_node_pyproject_declares_entry_point(tmp_path: Path) -> None:
    payload = create_node("My Norm", stage="normalization", output_dir=tmp_path)
    pyproject = Path(payload["package_dir"]) / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert '[project.entry-points."sure_eval.nodes"]' in content
    assert '"normalization/my_norm" = "sure_eval_node_my_norm.node"' in content
    assert 'where = ["src"]' in content

    manifest = (Path(payload["package_dir"]) / "sure_eval_plugin.yaml").read_text(encoding="utf-8")
    assert 'module: "sure_eval_node_my_norm"' in manifest
    assert 'module: "sure_eval_node_my_norm.node"' in manifest
    assert payload["plugin_add_hint"].startswith("sure-eval plugin add ")


def test_create_node_requires_stage(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_node("My Norm", stage="  ", output_dir=tmp_path)
