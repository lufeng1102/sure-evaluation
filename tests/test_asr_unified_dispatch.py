"""Tests for ASR normalizer/scorer registry fallback dispatch.

The ASR executor resolves builtin normalizers/scorers through if-elif first and
then falls back to the registry for external nodes.  These tests cover that
fallback path (selectors, node factory dispatch, and component identity), which
is the ASR half of the unified dispatch rollout.
"""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult


def _fake_external_module(
    node_id: str, stage: str, selector_key: str, selector_value: str, score: float
) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id
    module.STAGE = stage
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": stage}
    module.NODE_ENV = None
    module.SELECTORS = {selector_key: selector_value}

    def build(**config):
        def node(files):
            details: dict = {"external": True, "config": config}
            if stage == "scoring":
                details["result"] = {"score": score, "per_sample": []}
            return files, PipelineNodeResult(
                stage=stage, node_id=node_id, version="v1", details=details
            )

        return node

    module.build = build
    return module


def _install_external_node(
    monkeypatch, node_id: str, stage: str, selector_key: str, selector_value: str, score: float
) -> None:
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(lambda: [(node_id, f"fake_{selector_value}")]),
    )
    monkeypatch.setattr(
        nr,
        "_import_cached",
        lambda name: _fake_external_module(node_id, stage, selector_key, selector_value, score),
    )


def test_normalization_node_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.asr.pipeline import _normalization_node

    _install_external_node(
        monkeypatch, "normalization/fake_norm", "normalization", "normalizer", "my_norm", 0.0
    )

    node, label = _normalization_node(language="en", normalizer="my_norm")
    assert label == "fake_norm"
    out, result = node(KeyTextFiles("ref.txt", "hyp.txt"))
    assert result.node_id == "normalization/fake_norm"
    assert result.details["external"] is True


def test_scoring_node_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.asr.pipeline import _scoring_node

    _install_external_node(
        monkeypatch, "scoring/fake_score", "scoring", "scorer", "my_score", 0.5
    )

    node, label = _scoring_node(metric="wer", scorer="my_score")
    assert label == "fake_score"
    out, result = node(KeyTextFiles("ref.txt", "hyp.txt"))
    assert result.node_id == "scoring/fake_score"
    assert result.details["result"]["score"] == 0.5


def test_normalize_normalizer_external_selector(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.asr.pipeline import _normalize_normalizer

    _install_external_node(
        monkeypatch, "normalization/fake_norm", "normalization", "normalizer", "my_norm", 0.0
    )

    assert _normalize_normalizer(language="en", metric="wer", normalizer="my_norm") == "my_norm"


def test_normalize_scorer_external_selector(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.asr.pipeline import _normalize_scorer

    _install_external_node(
        monkeypatch, "scoring/fake_score", "scoring", "scorer", "my_score", 0.0
    )

    assert _normalize_scorer(language="en", metric="wer", scorer="my_score") == "my_score"


def test_normalizer_component_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.asr.pipeline import _normalizer_component

    _install_external_node(
        monkeypatch, "normalization/fake_norm", "normalization", "normalizer", "my_norm", 0.0
    )

    component = _normalizer_component(language="en", normalizer_label="fake_norm")
    assert component.component_id == "normalization/fake_norm"


def test_scoring_node_id_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.asr.pipeline import _scoring_node_id

    _install_external_node(
        monkeypatch, "scoring/fake_score", "scoring", "scorer", "my_score", 0.0
    )

    assert _scoring_node_id("fake_score") == "scoring/fake_score"


def test_unknown_normalizer_and_scorer_raise(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.asr.pipeline import _normalize_normalizer, _normalize_scorer

    monkeypatch.setattr(nr.NodeRegistry, "iter_entry_point_specs", staticmethod(lambda: []))
    monkeypatch.setattr(nr, "_import_cached", lambda name: (_ for _ in ()).throw(ImportError(name)))

    with pytest.raises(ValueError, match="normalizer"):
        _normalize_normalizer(language="en", metric="wer", normalizer="no_such_norm")
    with pytest.raises(ValueError, match="scorer"):
        _normalize_scorer(language="en", metric="wer", scorer="no_such_score")


def test_evaluate_asr_files_external_chain(tmp_path, monkeypatch) -> None:
    """End-to-end: an external normalizer + scorer pair drive evaluate_asr_files."""
    from sure_eval.evaluation.tasks.asr.pipeline import evaluate_asr_files

    norm_id = "normalization/fake_norm"
    score_id = "scoring/fake_score"
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(
            lambda: [
                (norm_id, "fake_my_norm"),
                (score_id, "fake_my_score"),
            ]
        ),
    )

    def _import(name: str) -> types.ModuleType:
        if name.endswith("my_norm"):
            return _fake_external_module(norm_id, "normalization", "normalizer", "my_norm", 0.0)
        if name.endswith("my_score"):
            return _fake_external_module(score_id, "scoring", "scorer", "my_score", 0.5)
        raise ImportError(name)

    monkeypatch.setattr(nr, "_import_cached", _import)

    ref = tmp_path / "ref.txt"
    hyp = tmp_path / "hyp.txt"
    ref.write_text("k1\tHELLO\n")
    hyp.write_text("k1\tWORLD\n")

    report = evaluate_asr_files(
        str(ref),
        str(hyp),
        language="en",
        metric="wer",
        normalizer="my_norm",
        scorer="my_score",
    )

    assert report.score == 0.5
    assert report.pipeline_id.endswith("fake_norm_v1.fake_score_v1")
    trace_ids = [entry.node_id for entry in report.pipeline_trace]
    assert trace_ids == [norm_id, score_id]
    assert report.pipeline_trace[0].details["external"] is True
    assert report.pipeline_trace[1].details["external"] is True
