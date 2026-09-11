"""describe-time pipeline_id version-chain validation tests."""

from __future__ import annotations

import pytest

from sure_eval.evaluation.cli_adapters import (
    _node_manifest_version,
    _pipeline_id_component_versions,
    _validate_pipeline_id_versions,
)
from sure_eval.evaluation.node_registry import get_registry


def test_node_manifest_version_builtin_and_conversion() -> None:
    assert _node_manifest_version("scoring/classify") == "v1"
    # conversion node without a manifest defaults to v1
    assert _node_manifest_version("conversion/kws_sure_json_to_samples") == "v1"


def test_component_versions_atomic() -> None:
    assert _pipeline_id_component_versions(
        "classification.any.accuracy.classify_v1"
    ) == ["v1"]


def test_component_versions_atomic_base_name_version_digit() -> None:
    # base name itself contains "_v3"; only the trailing "_v1" is the version
    assert _pipeline_id_component_versions(
        "tts.en.wer.whisper_large_v3_v1.whisper_norm_english_v1.wenet_wer_v1"
    ) == ["v1", "v1", "v1"]


def test_component_versions_bundle() -> None:
    assert _pipeline_id_component_versions(
        "sv.any.multi.eer.cosine_trial_scores_v1.det_eer_v1__min_dcf.cosine_trial_scores_v1.min_dcf_p005_v1"
    ) == ["v1", "v1", "v1", "v1"]


def test_validate_versions_ok_atomic() -> None:
    _validate_pipeline_id_versions(
        "classification.any.accuracy.classify_v1", ["scoring/classify"]
    )


def test_validate_versions_ok_bundle_deduplicated_nodes() -> None:
    # bundle repeats cosine_trial_scores; computation_node_ids is deduplicated
    _validate_pipeline_id_versions(
        "sv.any.multi.eer.cosine_trial_scores_v1.det_eer_v1__min_dcf.cosine_trial_scores_v1.min_dcf_p005_v1",
        [
            "scoring/cosine_trial_scores",
            "scoring/det_eer",
            "scoring/min_dcf_p005",
        ],
    )


def test_validate_versions_stale_bump_raises(monkeypatch) -> None:
    original = get_registry().manifest

    def fake_manifest(node_id):
        manifest = dict(original(node_id))
        if node_id == "scoring/classify":
            manifest["version"] = "v2"
        return manifest

    monkeypatch.setattr(get_registry(), "manifest", fake_manifest)

    with pytest.raises(ValueError, match="version mismatch"):
        _validate_pipeline_id_versions(
            "classification.any.accuracy.classify_v1", ["scoring/classify"]
        )


def test_build_pipeline_spec_rejects_stale_version(monkeypatch) -> None:
    from sure_eval.evaluation.cli_adapters import build_pipeline_spec

    original = get_registry().manifest

    def fake_manifest(node_id):
        manifest = dict(original(node_id))
        if node_id == "scoring/classify":
            manifest["version"] = "v2"
        return manifest

    monkeypatch.setattr(get_registry(), "manifest", fake_manifest)

    with pytest.raises(ValueError, match="version mismatch"):
        build_pipeline_spec(
            "classification", pipeline_id="classification.any.accuracy.classify_v1"
        )
