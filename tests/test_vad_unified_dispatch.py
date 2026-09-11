"""Tests for unified NodePayload dispatch on the VAD task."""

from __future__ import annotations

import pytest

from sure_eval.evaluation.core.types import EvaluationFiles, NodePayload
from sure_eval.evaluation.node_registry import get_registry
from sure_eval.evaluation.tasks.vad.pipeline import evaluate_vad_files


def test_node_payload_artifact_roundtrip() -> None:
    payload = NodePayload(files=EvaluationFiles(roles={"ref": "r", "hyp": "h"}))
    assert payload.artifact("missing") is None
    assert payload.artifact("missing", default="d") == "d"

    marker = object()
    updated = payload.with_artifact("bundle", marker)
    assert updated.artifact("bundle") is marker
    assert "bundle" not in payload.artifacts  # original payload stays immutable


def test_vad_builtin_nodes_expose_build_and_artifacts() -> None:
    reg = get_registry()
    expected = {
        "validation/vad_contract": ((), ("validated_bundle",)),
        "normalization/vad_timebase": (("validated_bundle",), ("normalized_bundle",)),
        "scoring/vad_detection_duration": (("normalized_bundle",), ()),
        "scoring/vad_auc_roc": (("normalized_bundle",), ()),
    }
    for node_id, (consumes, produces) in expected.items():
        registration = reg.resolve(node_id)
        assert registration.build is not None, node_id
        assert registration.consumes == consumes, node_id
        assert registration.produces == produces, node_id


def test_registry_build_enforces_consumes_contract() -> None:
    reg = get_registry()
    node = reg.build("normalization/vad_timebase", metric="f1")
    empty_payload = NodePayload(files=EvaluationFiles(roles={}))
    with pytest.raises(ValueError, match="validated_bundle"):
        node(empty_payload)


def test_registry_build_skips_checks_for_non_payload() -> None:
    # Non-NodePayload loads (e.g. ASR's KeyTextFiles) bypass artifact checks.
    from sure_eval.evaluation.node_registry import _check_artifacts

    _check_artifacts(object(), ("validated_bundle",), node_id="x", phase="consumes")


def _write_vad_fixture(tmp_path):
    ref = tmp_path / "ref.jsonl"
    hyp = tmp_path / "hyp.jsonl"
    ref.write_text(
        '{"key": "u1", "duration": 10.0, "speech_segments": [{"start": 1.0, "end": 3.0}]}\n'
        '{"key": "u2", "duration": 5.0, "speech_segments": [{"start": 0.0, "end": 5.0}]}\n',
        encoding="utf-8",
    )
    hyp.write_text(
        '{"key": "u1", "speech_segments": [{"start": 1.0, "end": 3.0}]}\n'
        '{"key": "u2", "speech_segments": [{"start": 0.0, "end": 5.0}]}\n',
        encoding="utf-8",
    )
    return ref, hyp


def test_evaluate_vad_files_dispatches_via_registry(tmp_path) -> None:
    ref, hyp = _write_vad_fixture(tmp_path)
    report = evaluate_vad_files(reference_jsonl=ref, sample_output=hyp, metric="f1")
    assert report.metric == "f1"
    assert report.score == 1.0  # perfect prediction
    assert (
        report.pipeline_id
        == "vad.any.f1.vad_contract_v1.vad_timebase_strict_v1.vad_detection_duration_v1"
    )
    assert len(report.pipeline_trace) == 3


def test_evaluate_vad_files_auc_roc_route(tmp_path) -> None:
    ref = tmp_path / "ref.jsonl"
    hyp = tmp_path / "hyp.jsonl"
    ref.write_text(
        '{"key": "u1", "duration": 10.0, "speech_segments": [{"start": 0.0, "end": 10.0}]}\n',
        encoding="utf-8",
    )
    hyp.write_text(
        '{"key": "u1", "frame_scores": [{"start": 0.0, "end": 10.0, "score": 0.9}]}\n',
        encoding="utf-8",
    )
    report = evaluate_vad_files(reference_jsonl=ref, sample_output=hyp, metric="auc_roc")
    assert report.metric == "auc_roc"
    assert (
        report.pipeline_id
        == "vad.any.auc_roc.vad_contract_v1.vad_timebase_strict_v1.vad_auc_roc_v1"
    )
