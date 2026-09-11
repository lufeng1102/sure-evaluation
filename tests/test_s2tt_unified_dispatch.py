"""S2TT scoring-node registry fallback dispatch tests."""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.core.types import PipelineNodeResult


def _fake_scorer(node_id: str, selector_value: str, score: float) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id
    module.STAGE = "scoring"
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": "scoring"}
    module.NODE_ENV = None
    module.SELECTORS = {"scorer": selector_value}

    def build(**config):
        def node(files, *, language, src_file=None):
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
        nr, "_import_cached", lambda name: _fake_scorer(node_id, selector_value, score)
    )


def test_external_scoring_callable_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.s2tt.pipeline import _external_scoring_callable

    _install_external(monkeypatch, "scoring/fake_s2tt", "my_s2tt", 0.42)

    callable_, node_id = _external_scoring_callable("my_s2tt")
    assert node_id == "scoring/fake_s2tt"
    assert callable_ is not None


def test_external_scoring_callable_unknown_raises(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.s2tt.pipeline import _external_scoring_callable

    monkeypatch.setattr(nr.NodeRegistry, "iter_entry_point_specs", staticmethod(lambda: []))
    monkeypatch.setattr(nr, "_import_cached", lambda name: (_ for _ in ()).throw(ImportError(name)))

    with pytest.raises(ValueError, match="scorer"):
        _external_scoring_callable("no_such_scorer")


def test_evaluate_s2tt_files_external(tmp_path, monkeypatch) -> None:
    from sure_eval.evaluation.tasks.s2tt.pipeline import evaluate_s2tt_files

    _install_external(monkeypatch, "scoring/fake_s2tt", "my_s2tt", 0.42)

    ref = tmp_path / "ref.txt"
    hyp = tmp_path / "hyp.txt"
    ref.write_text("k1\thello world\n")
    hyp.write_text("k1\tbonjour monde\n")

    report = evaluate_s2tt_files(
        str(ref), str(hyp), language="zh", metric="my_metric", scorer="my_s2tt"
    )

    assert report.score == 0.42
    assert report.pipeline_id.endswith("fake_s2tt_v1")
    assert [entry.node_id for entry in report.pipeline_trace] == ["scoring/fake_s2tt"]
    assert report.pipeline_trace[0].details["external"] is True
