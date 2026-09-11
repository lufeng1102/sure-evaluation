"""Tests for plugin-declared route injection (contracts.load_task_routes)."""

from __future__ import annotations

import importlib.metadata
import types

from sure_eval.evaluation.scripts import contracts
from sure_eval.evaluation.scripts.contracts import _load_external_routes, load_task_routes

_EXTERNAL_ROUTE = {
    "language": "en",
    "metric": "wer",
    "pipeline_id": "asr.en.wer.lowercase_norm_v1.wenet_wer_v1",
    "nodes": ["normalization/lowercase_norm", "scoring/wenet_wer"],
    "input_contract": "scoring/wenet_wer",
    "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",
}


def _fake_routes_module() -> types.ModuleType:
    module = types.ModuleType("fake_routes")
    module.ROUTES = [_EXTERNAL_ROUTE]
    return module


def test_load_external_routes_collects_matching_task(monkeypatch) -> None:
    def fake_entry_points(group=None):
        if group == "sure_eval.routes":
            return [types.SimpleNamespace(name="asr", value="fake_routes")]
        return []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    monkeypatch.setattr(contracts, "import_module", lambda name: _fake_routes_module())

    routes = _load_external_routes("asr")
    assert routes == [_EXTERNAL_ROUTE]
    assert _load_external_routes("tts") == []


def test_load_external_routes_ignores_broken_plugin(monkeypatch) -> None:
    def fake_entry_points(group=None):
        return [types.SimpleNamespace(name="asr", value="missing.module")]

    def failing_import(_name: str):
        raise ImportError("no such module")

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    monkeypatch.setattr(contracts, "import_module", failing_import)

    assert _load_external_routes("asr") == []


def test_load_task_routes_aggregates_external(monkeypatch) -> None:
    monkeypatch.setattr(contracts, "_load_external_routes", lambda task: [_EXTERNAL_ROUTE])

    routes, path = load_task_routes("asr")
    assert path.name == "routes.yaml"
    assert routes["task"] == "ASR"
    pipeline_ids = {route.get("pipeline_id") for route in routes["routes"]}
    # 内置 route 仍在（whisper_norm 默认链路）
    assert "asr.en.wer.whisper_norm_english_v1.wenet_wer_v1" in pipeline_ids
    # 外部 route 被合并进来
    assert "asr.en.wer.lowercase_norm_v1.wenet_wer_v1" in pipeline_ids
