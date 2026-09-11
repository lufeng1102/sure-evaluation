"""Tests for unified registry dispatch on TTS/VC and the audio_semantic chain."""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation.core.types import PipelineNodeResult
from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.node_registry import get_registry
from sure_eval.evaluation.nodes.transcription.common.audio_semantic import (
    _transcription_components,
    transcribe_audio,
)

_TRANSCRIPTION_NODE_IDS = (
    "transcription/paraformer_zh",
    "transcription/whisper_large_v3",
    "transcription/qwen3_asr_1_7b",
    "transcription/cohere_transcribe_arabic_07_2026",
)


class _FakeRunner:
    def transcribe(self, audio_path: str, *, language: str = "en") -> str:
        return f"transcript-of-{language}"


def test_transcription_nodes_expose_build() -> None:
    reg = get_registry()
    for node_id in _TRANSCRIPTION_NODE_IDS:
        assert reg.resolve(node_id).build is not None, node_id


def test_transcribe_audio_dispatches_builtin() -> None:
    transcript, trace = transcribe_audio(
        "x.wav", language="en", runner=_FakeRunner(), role="prediction_audio"
    )
    assert transcript == "transcript-of-en"
    assert [node.node_id for node in trace] == ["transcription/whisper_large_v3"]


def _fake_transcription_module(node_id: str) -> types.ModuleType:
    module = types.ModuleType("fake_transcription")
    module.NODE_ID = node_id
    module.STAGE = "transcription"
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": "transcription"}
    module.NODE_ENV = None
    module.SELECTORS = {}

    def build(*, runner=None, **config):
        def node(audio_path, *, language="en", role="prediction_audio"):
            return f"fake-{language}", (
                PipelineNodeResult(
                    stage="transcription",
                    node_id=node_id,
                    version="v1",
                    details={"external": True},
                ),
            )

        return node

    module.build = build
    return module


def _install_external_transcription(monkeypatch, node_id: str) -> None:
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(lambda: [(node_id, "fake_transcription")]),
    )
    monkeypatch.setattr(
        nr, "_import_cached", lambda name: _fake_transcription_module(node_id)
    )


def test_transcribe_audio_dispatches_external(monkeypatch) -> None:
    _install_external_transcription(monkeypatch, "transcription/fake_asr")

    transcript, trace = transcribe_audio(
        "x.wav",
        language="en",
        runner=_FakeRunner(),
        role="prediction_audio",
        transcription_node_id="transcription/fake_asr",
    )
    assert transcript == "fake-en"
    assert [node.node_id for node in trace] == ["transcription/fake_asr"]
    assert trace[0].details["external"] is True


def test_transcription_components_builtin_and_external(monkeypatch) -> None:
    assert [c.component_id for c in _transcription_components("zh", None)] == [
        "frontend/funasr_loader_16k_mono",
        "transcription/paraformer_zh",
    ]
    assert [c.component_id for c in _transcription_components("en", None)] == [
        "transcription/whisper_large_v3",
    ]

    _install_external_transcription(monkeypatch, "transcription/fake_asr")
    assert [c.component_id for c in _transcription_components("en", "transcription/fake_asr")] == [
        "transcription/fake_asr",
    ]


def test_tts_vc_scoring_family_external(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.tts.pipeline import _scoring_family as tts_family
    from sure_eval.evaluation.tasks.vc.pipeline import _scoring_family as vc_family

    def install(node_id: str, selector_key: str, selector_value: str) -> None:
        module = types.ModuleType("fake_scoring")
        module.NODE_ID = node_id
        module.STAGE = "scoring"
        module.VERSION = "v1"
        module.MANIFEST = {"id": node_id, "version": "v1", "stage": "scoring"}
        module.NODE_ENV = None
        module.SELECTORS = {selector_key: selector_value}
        module.build = lambda **config: (lambda rows: PipelineNodeResult(
            stage="scoring", node_id=node_id, version="v1", details={}
        ))
        monkeypatch.setattr(
            nr.NodeRegistry,
            "iter_entry_point_specs",
            staticmethod(lambda: [(node_id, "fake_scoring")]),
        )
        monkeypatch.setattr(nr, "_import_cached", lambda name: module)

    install("scoring/fake_backend", "speaker", "my_backend")

    assert tts_family("sim/my_backend") == "speaker"
    assert vc_family("sim/my_backend") == "speaker"
    assert tts_family("dnsmos") == "mos"
    assert tts_family("tts_cer") is None
