"""SD scoring-node registry fallback dispatch tests."""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult


def _fake_sd_node(node_id: str, selector_value: str, der: float) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id
    module.STAGE = "scoring"
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": "scoring"}
    module.NODE_ENV = None
    module.SELECTORS = {"scorer": selector_value}

    def build(**config):
        def node(*, ref_file, hyp_file, metric="der", collar=0.25):
            return KeyTextFiles(ref_file=ref_file, hyp_file=hyp_file), PipelineNodeResult(
                stage="scoring",
                node_id=node_id,
                version="v1",
                details={"external": True, "config": config, "result": {"der": der}},
            )

        return node

    module.build = build
    return module


def _install_external(monkeypatch, node_id: str, selector_value: str, der: float) -> None:
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(lambda: [(node_id, f"fake_{selector_value}")]),
    )
    monkeypatch.setattr(
        nr, "_import_cached", lambda name: _fake_sd_node(node_id, selector_value, der)
    )


def test_scoring_callable_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.sd.pipeline import _scoring_callable

    _install_external(monkeypatch, "scoring/fake_sd", "my_sd", 0.12)

    callable_, node_id = _scoring_callable("my_sd")
    assert node_id == "scoring/fake_sd"
    assert callable_ is not None


def test_scoring_callable_unknown_raises(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.sd.pipeline import _scoring_callable

    monkeypatch.setattr(nr.NodeRegistry, "iter_entry_point_specs", staticmethod(lambda: []))
    monkeypatch.setattr(nr, "_import_cached", lambda name: (_ for _ in ()).throw(ImportError(name)))

    with pytest.raises(ValueError, match="scorer"):
        _scoring_callable("no_such_sd")


def test_evaluate_sd_files_external(tmp_path, monkeypatch) -> None:
    from sure_eval.evaluation.tasks.sd.pipeline import evaluate_sd_files

    _install_external(monkeypatch, "scoring/fake_sd", "my_sd", 0.12)

    ref = tmp_path / "ref.rttm"
    hyp = tmp_path / "hyp.rttm"
    ref.write_text("SPEAKER rec1 1 0.00 1.00 <NA> <NA> spk1 <NA> <NA>\n")
    hyp.write_text("SPEAKER rec1 1 0.00 1.00 <NA> <NA> hyp1 <NA> <NA>\n")

    report = evaluate_sd_files(str(ref), str(hyp), scorer="my_sd")

    assert report.score == 0.12
    assert report.pipeline_id.endswith("fake_sd_v1")
    assert [entry.node_id for entry in report.pipeline_trace] == ["scoring/fake_sd"]
    assert report.pipeline_trace[0].details["external"] is True
