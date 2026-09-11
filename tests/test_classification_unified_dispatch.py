"""Classification scoring-node registry fallback dispatch tests."""

from __future__ import annotations

import types

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult


def _fake_classifier(node_id: str, selector_value: str, score: float) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id
    module.STAGE = "scoring"
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": "scoring"}
    module.NODE_ENV = None
    module.SELECTORS = {"scorer": selector_value}

    def build(**config):
        def node(*, ref_file, hyp_file, label_spec=None, task="classification"):
            return KeyTextFiles(ref_file=ref_file, hyp_file=hyp_file), PipelineNodeResult(
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
        nr, "_import_cached", lambda name: _fake_classifier(node_id, selector_value, score)
    )


def test_scoring_callable_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.classification.pipeline import _scoring_callable

    _install_external(monkeypatch, "scoring/fake_classifier", "my_classifier", 0.75)

    callable_, node_id = _scoring_callable("my_classifier")
    assert node_id == "scoring/fake_classifier"
    assert callable_ is not None


def test_scoring_callable_unknown_raises(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.classification.pipeline import _scoring_callable

    monkeypatch.setattr(nr.NodeRegistry, "iter_entry_point_specs", staticmethod(lambda: []))
    monkeypatch.setattr(nr, "_import_cached", lambda name: (_ for _ in ()).throw(ImportError(name)))

    with pytest.raises(ValueError, match="scorer"):
        _scoring_callable("no_such_classifier")


def test_evaluate_classification_files_external(tmp_path, monkeypatch) -> None:
    from sure_eval.evaluation.tasks.classification.pipeline import evaluate_classification_files

    _install_external(monkeypatch, "scoring/fake_classifier", "my_classifier", 0.75)

    ref = tmp_path / "ref.txt"
    hyp = tmp_path / "hyp.txt"
    ref.write_text("k1\tA\n")
    hyp.write_text("k1\tA\n")
    spec = tmp_path / "labels.yaml"
    spec.write_text("id: t\nlabels:\n  - id: A\n")

    report = evaluate_classification_files(
        str(ref), str(hyp), label_spec=str(spec), scorer="my_classifier"
    )

    assert report.score == 0.75
    assert report.pipeline_id.endswith("fake_classifier_v1")
    assert [entry.node_id for entry in report.pipeline_trace] == ["scoring/fake_classifier"]
    assert report.pipeline_trace[0].details["external"] is True
