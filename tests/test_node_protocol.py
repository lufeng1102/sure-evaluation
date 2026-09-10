"""Tests for the node plugin protocol (core/node_protocol.py)."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from sure_eval.evaluation.core.node_protocol import NodeRegistration, registration_from_module


def _make_module(**attrs: object) -> types.ModuleType:
    module = types.ModuleType("test_node_module")
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _base_attrs() -> dict[str, object]:
    return {
        "NODE_ID": "normalization/test_norm",
        "STAGE": "normalization",
        "VERSION": "v1",
        "MANIFEST": {"id": "normalization/test_norm", "version": "v1", "stage": "normalization"},
        "NODE_ENV": None,
        "SELECTORS": {"normalizer": "test_norm"},
        "build": lambda **config: (lambda files: (files, None)),
    }


def test_registration_from_module_minimal() -> None:
    module = _make_module(**_base_attrs())
    reg = registration_from_module(module, source="entry_point")

    assert isinstance(reg, NodeRegistration)
    assert reg.node_id == "normalization/test_norm"
    assert reg.stage == "normalization"
    assert reg.version == "v1"
    assert reg.source == "entry_point"
    assert reg.name == "test_norm"
    assert callable(reg.build)
    assert reg.node_env is None
    assert reg.selectors == {"normalizer": "test_norm"}
    assert reg.module == "test_node_module"


def test_registration_explicit_node_id_overrides_missing_attribute() -> None:
    attrs = _base_attrs()
    attrs.pop("NODE_ID")
    attrs["MANIFEST"] = {}  # 不声明 id，由 effective_id 填充
    module = _make_module(**attrs)
    reg = registration_from_module(module, node_id="normalization/named", source="local")

    assert reg.node_id == "normalization/named"
    assert reg.manifest["id"] == "normalization/named"


def test_registration_requires_node_id() -> None:
    module = _make_module(STAGE="normalization", VERSION="v1", MANIFEST={})
    with pytest.raises(ValueError, match="NODE_ID"):
        registration_from_module(module)


def test_registration_node_id_mismatch_raises() -> None:
    module = _make_module(**_base_attrs())
    with pytest.raises(ValueError, match="mismatch"):
        registration_from_module(module, node_id="normalization/other")


def test_registration_missing_stage_raises() -> None:
    attrs = _base_attrs()
    attrs.pop("STAGE")
    module = _make_module(**attrs)
    with pytest.raises(ValueError, match="STAGE"):
        registration_from_module(module)


def test_registration_manifest_defaults_are_filled() -> None:
    module = _make_module(
        NODE_ID="scoring/demo",
        STAGE="scoring",
        VERSION="v3",
        MANIFEST={},
        NODE_ENV=None,
    )
    reg = registration_from_module(module)
    assert reg.manifest["id"] == "scoring/demo"
    assert reg.manifest["version"] == "v3"
    assert reg.manifest["stage"] == "scoring"


def test_registration_manifest_as_path(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "id: normalization/from_file\nversion: v2\nstage: normalization\n",
        encoding="utf-8",
    )
    attrs = _base_attrs()
    attrs["MANIFEST"] = "manifest.yaml"
    module = _make_module(**attrs)
    module.__file__ = str(tmp_path / "node.py")

    reg = registration_from_module(module)
    assert reg.manifest["id"] == "normalization/from_file"
    assert reg.manifest["version"] == "v2"


def test_registration_node_env_dict() -> None:
    attrs = _base_attrs()
    attrs["NODE_ENV"] = {"runtime": {"type": "pip"}, "packages": []}
    module = _make_module(**attrs)
    reg = registration_from_module(module)
    assert reg.node_env == {"runtime": {"type": "pip"}, "packages": []}


def test_registration_build_not_callable_raises() -> None:
    attrs = _base_attrs()
    attrs["build"] = "not_callable"
    module = _make_module(**attrs)
    with pytest.raises(TypeError, match="callable"):
        registration_from_module(module)


def test_registration_without_build_is_allowed() -> None:
    attrs = _base_attrs()
    attrs.pop("build")
    module = _make_module(**attrs)
    reg = registration_from_module(module)
    assert reg.build is None
