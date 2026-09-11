"""SV metric-node registry fallback dispatch tests."""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.core.types import PipelineNodeResult


def _fake_metric_node(node_id: str, selector_value: str, score: float) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id
    module.STAGE = "scoring"
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": "scoring"}
    module.NODE_ENV = None
    module.SELECTORS = {"scorer": selector_value}

    def build(**config):
        def node(scores, labels):
            return PipelineNodeResult(
                stage="scoring",
                node_id=node_id,
                version="v1",
                details={"external": True, "config": config, "result": {"score": score}},
            )

        return node

    module.build = build
    return module


def _install_external(monkeypatch, node_id: str, selector_value: str, score: float) -> None:
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(lambda: [(node_id, f"fake_{selector_value}")]),
    )
    monkeypatch.setattr(
        nr, "_import_cached", lambda name: _fake_metric_node(node_id, selector_value, score)
    )


def test_metric_scoring_callable_builtin() -> None:
    from sure_eval.evaluation.tasks.sv.pipeline import _metric_scoring_callable

    _, eer_node = _metric_scoring_callable("eer", None)
    _, dcf_node = _metric_scoring_callable("min_dcf", None)
    assert eer_node == "scoring/det_eer"
    assert dcf_node == "scoring/min_dcf_p005"


def test_metric_scoring_callable_external(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.sv.pipeline import _metric_scoring_callable

    _install_external(monkeypatch, "scoring/fake_eer", "my_eer", 0.05)

    callable_, node_id = _metric_scoring_callable("eer", "my_eer")
    assert node_id == "scoring/fake_eer"
    assert callable_ is not None


def test_metric_scoring_callable_unknown_raises(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.sv.pipeline import _metric_scoring_callable

    monkeypatch.setattr(nr.NodeRegistry, "iter_entry_point_specs", staticmethod(lambda: []))
    monkeypatch.setattr(nr, "_import_cached", lambda name: (_ for _ in ()).throw(ImportError(name)))

    with pytest.raises(ValueError, match="scorer"):
        _metric_scoring_callable("eer", "no_such_scorer")


def test_executor_scorers_from_routes_builtin_and_external(monkeypatch) -> None:
    from sure_eval.evaluation.scripts.sv import _executor_scorers_from_routes

    _install_external(monkeypatch, "scoring/fake_eer", "my_eer", 0.05)

    routes = (
        {
            "metric": "eer",
            "nodes": ["scoring/cosine_trial_scores", "scoring/fake_eer"],
        },
        {
            "metric": "min_dcf",
            "nodes": ["scoring/cosine_trial_scores", "scoring/min_dcf_p005"],
        },
    )
    scorers = _executor_scorers_from_routes(routes)
    assert scorers == {"eer": "my_eer", "min_dcf": "min_dcf_p005"}


def test_evaluate_sv_files_external_metric_node(tmp_path, monkeypatch) -> None:
    from sure_eval.evaluation.tasks.sv.pipeline import evaluate_sv_files

    _install_external(monkeypatch, "scoring/fake_eer", "my_eer", 0.05)

    import json

    from tests.test_sv_pipeline import _write_sv_fixture

    sample_output, manifest = _write_sv_fixture(tmp_path)

    report = evaluate_sv_files(
        str(sample_output),
        str(manifest),
        metrics=("eer",),
        work_dir=tmp_path / "work",
        scorers={"eer": "my_eer"},
    )

    assert report.score == 0.05
    assert report.pipeline_id == "sv.any.eer.cosine_trial_scores_v1.fake_eer_v1"
    trace_ids = [entry.node_id for entry in report.pipeline_trace]
    assert trace_ids == ["scoring/cosine_trial_scores", "scoring/fake_eer"]
    assert report.pipeline_trace[1].details["external"] is True
