"""Tests for unified registry dispatch on SE/TSE audio scoring nodes."""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation.core.types import PipelineNodeResult
from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.node_registry import get_registry
from sure_eval.evaluation.nodes.scoring import _audio_quality_dispatch as dispatch

_SCORING_NODE_IDS = (
    "scoring/si_sdr",
    "scoring/stoi",
    "scoring/pesq",
    "scoring/dnsmos",
    "scoring/wv_mos",
    "scoring/utmos",
    "scoring/wavlm_large_sim",
    "scoring/ecapa_tdnn_sim",
    "scoring/eres2net_sim",
)


def test_se_tse_scoring_nodes_expose_build() -> None:
    reg = get_registry()
    for node_id in _SCORING_NODE_IDS:
        assert reg.resolve(node_id).build is not None, node_id


def _fake_external_node_module(
    selector_key: str, selector_value: str, node_id: str | None = None
) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id or f"scoring/fake_{selector_value}"
    module.STAGE = "scoring"
    module.VERSION = "v1"
    module.MANIFEST = {"id": module.NODE_ID, "version": "v1", "stage": "scoring"}
    module.NODE_ENV = None
    module.SELECTORS = {selector_key: selector_value}

    def build(**config):
        def node(rows):
            per_sample = [
                {"score": 1.0, "sample_id": (row[0] if row else None)} for row in rows
            ]
            return PipelineNodeResult(
                stage="scoring",
                node_id=module.NODE_ID,
                version="v1",
                details={
                    "external": True,
                    "row_count": len(rows),
                    "result": {"score": 1.0, "per_sample": per_sample},
                },
            )

        return node

    module.build = build
    return module


@pytest.mark.parametrize(
    ("dispatch_fn", "selector_key", "selector_value", "metric_kwargs"),
    [
        (dispatch.score_full_reference_metric, "full_reference", "my_metric", {}),
        (dispatch.score_mos_metric, "mos", "my_metric", {}),
        (dispatch.score_speaker_metric, "speaker", "my_backend", {}),
    ],
)
def test_audio_dispatch_falls_back_to_registry(
    monkeypatch, dispatch_fn, selector_key, selector_value, metric_kwargs
) -> None:
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(lambda: [(f"scoring/fake_{selector_value}", f"fake_{selector_value}")]),
    )
    monkeypatch.setattr(
        nr, "_import_cached", lambda name: _fake_external_node_module(selector_key, selector_value)
    )

    if selector_key == "full_reference":
        result = dispatch_fn([], metric_name=selector_value, provider=object())
    elif selector_key == "mos":
        result = dispatch_fn([], metric_name=selector_value, provider=object())
    else:
        result = dispatch_fn([], backend_name=selector_value, provider=object())

    assert result.node_id == f"scoring/fake_{selector_value}"
    assert result.details["external"] is True


def test_audio_dispatch_raises_for_unknown_metric() -> None:
    with pytest.raises(ValueError, match="full-reference"):
        dispatch.score_full_reference_metric([], metric_name="no_such_metric", provider=object())
    with pytest.raises(ValueError, match="MOS"):
        dispatch.score_mos_metric([], metric_name="no_such_metric", provider=object())
    with pytest.raises(ValueError, match="speaker"):
        dispatch.score_speaker_metric([], backend_name="no_such_backend", provider=object())


def _install_external_node(monkeypatch, node_id: str, selector_key: str, selector_value: str) -> None:
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(lambda: [(node_id, f"fake_{selector_value}")]),
    )
    monkeypatch.setattr(
        nr,
        "_import_cached",
        lambda name: _fake_external_node_module(selector_key, selector_value, node_id=node_id),
    )


def test_se_metric_family_and_node_id_external(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.se.pipeline import _metric_family, _node_id_for_metric

    _install_external_node(monkeypatch, "scoring/fake_my_metric", "full_reference", "my-metric")

    assert _metric_family("my-metric") == "full_reference"
    assert _node_id_for_metric("my-metric") == "scoring/fake_my_metric"
    # Builtin classification is unchanged.
    assert _metric_family("si-sdr") == "full_reference"
    assert _metric_family("dnsmos") == "mos"


def test_tse_scoring_family_external(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.tse.pipeline import _scoring_family

    _install_external_node(monkeypatch, "scoring/fake_my_backend", "speaker", "my_backend")

    assert _scoring_family("sim/my_backend") == "speaker"
    assert _scoring_family("dnsmos") == "mos"
    assert _scoring_family("si_sdr") is None


def test_se_evaluate_external_full_reference_metric(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.se.pipeline import evaluate_se_samples
    from sure_eval.evaluation.tasks.se.types import SESample

    _install_external_node(monkeypatch, "scoring/fake_metric", "full_reference", "my-metric")

    samples = [SESample(enhanced_audio="e.wav", reference_audio="r.wav", sample_id="s1")]
    report = evaluate_se_samples(samples, metrics=["my-metric"])
    assert report.pipeline_id == "se.any.my_metric.fake_metric_v1"
    assert report.score == 1.0
    assert report.computation_node_ids == ("scoring/fake_metric",)
