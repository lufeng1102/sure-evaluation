"""KWS scoring-node registry fallback dispatch tests."""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.core.types import PipelineNodeResult


def _fake_kws_node(node_id: str, selector_value: str, score: float) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id
    module.STAGE = "scoring"
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": "scoring"}
    module.NODE_ENV = None
    module.SELECTORS = {"scorer": selector_value}

    def build(**config):
        def node(samples, **kwargs):
            return PipelineNodeResult(
                stage="scoring",
                node_id=node_id,
                version="v1",
                details={
                    "external": True,
                    "config": config,
                    "results": {"accuracy": {"score": score}},
                    "rows": [],
                    "summary": {},
                },
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
        nr, "_import_cached", lambda name: _fake_kws_node(node_id, selector_value, score)
    )


def test_scoring_callable_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.kws.pipeline import _scoring_callable

    _install_external(monkeypatch, "scoring/fake_kws", "my_kws", 0.8)

    callable_, node_id = _scoring_callable("my_kws")
    assert node_id == "scoring/fake_kws"
    assert callable_ is not None


def test_scoring_callable_builtin_aliases() -> None:
    from sure_eval.evaluation.tasks.kws.pipeline import _scoring_callable

    from sure_eval.evaluation.nodes.scoring.wekws_det import score_wekws_det

    for scorer in (None, "", "wekws_det", "scoring/wekws_det"):
        callable_, node_id = _scoring_callable(scorer)
        assert callable_ is score_wekws_det
        assert node_id == "scoring/wekws_det"


def test_scoring_callable_unknown_raises(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.kws.pipeline import _scoring_callable

    monkeypatch.setattr(nr.NodeRegistry, "iter_entry_point_specs", staticmethod(lambda: []))
    monkeypatch.setattr(nr, "_import_cached", lambda name: (_ for _ in ()).throw(ImportError(name)))

    with pytest.raises(ValueError, match="scorer"):
        _scoring_callable("no_such_kws")


def test_evaluate_kws_samples_external(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.kws.pipeline import evaluate_kws_samples

    _install_external(monkeypatch, "scoring/fake_kws", "my_kws", 0.8)

    report = evaluate_kws_samples([], scorer="my_kws")

    assert report.score == 0.8
    assert report.pipeline_id.endswith("fake_kws_v1")
    assert [entry.node_id for entry in report.pipeline_trace] == ["scoring/fake_kws"]
    assert report.pipeline_trace[0].details["external"] is True
