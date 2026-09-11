"""SLU normalization + scoring-node registry fallback dispatch tests."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from sure_eval.evaluation import node_registry as nr
from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult


def _write_key_text(path: Path, rows: list[tuple[str, str]]) -> None:
    path.write_text("".join(f"{key}\t{text}\n" for key, text in rows), encoding="utf-8")


def _fake_norm_node(node_id: str, selector_value: str) -> types.ModuleType:
    module = types.ModuleType(f"fake_{selector_value}")
    module.NODE_ID = node_id
    module.STAGE = "normalization"
    module.VERSION = "v1"
    module.MANIFEST = {"id": node_id, "version": "v1", "stage": "normalization"}
    module.NODE_ENV = None
    module.SELECTORS = {"normalizer": selector_value}

    def build(**config):
        def node(files, *, prompt_jsonl, output_mode="choice_id"):
            return KeyTextFiles(ref_file=files.ref_file, hyp_file=files.hyp_file), PipelineNodeResult(
                stage="normalization",
                node_id=node_id,
                version="v1",
                details={"external": True, "config": config},
            )

        return node

    module.build = build
    return module


def _fake_scorer_node(node_id: str, selector_value: str, score: float) -> types.ModuleType:
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


def _install_external_nodes(
    monkeypatch,
    *,
    norm_id: str = "normalization/fake_norm",
    norm_selector: str = "my_norm",
    score_id: str = "scoring/fake_slu",
    score_selector: str = "my_slu",
    score: float = 0.75,
) -> None:
    modules = {
        f"fake_{norm_selector}": _fake_norm_node(norm_id, norm_selector),
        f"fake_{score_selector}": _fake_scorer_node(score_id, score_selector, score),
    }
    monkeypatch.setattr(
        nr.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(
            lambda: [
                (norm_id, f"fake_{norm_selector}"),
                (score_id, f"fake_{score_selector}"),
            ]
        ),
    )
    monkeypatch.setattr(nr, "_import_cached", lambda name: modules[name])


def _install_no_external(monkeypatch) -> None:
    monkeypatch.setattr(nr.NodeRegistry, "iter_entry_point_specs", staticmethod(lambda: []))
    monkeypatch.setattr(nr, "_import_cached", lambda name: (_ for _ in ()).throw(ImportError(name)))


def test_scoring_callable_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.slu.pipeline import _scoring_callable

    _install_external_nodes(monkeypatch)

    callable_, node_id = _scoring_callable("my_slu")
    assert node_id == "scoring/fake_slu"
    assert callable_ is not None


def test_normalization_callable_external_fallback(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.slu.pipeline import _normalization_callable

    _install_external_nodes(monkeypatch)

    callable_, node_id = _normalization_callable("my_norm")
    assert node_id == "normalization/fake_norm"
    assert callable_ is not None


def test_scoring_callable_unknown_raises(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.slu.pipeline import _scoring_callable

    _install_no_external(monkeypatch)

    with pytest.raises(ValueError, match="scorer"):
        _scoring_callable("no_such_slu_scorer")


def test_normalization_callable_unknown_raises(monkeypatch) -> None:
    from sure_eval.evaluation.tasks.slu.pipeline import _normalization_callable

    _install_no_external(monkeypatch)

    with pytest.raises(ValueError, match="normalizer"):
        _normalization_callable("no_such_slu_norm")


def test_evaluate_slu_files_external(tmp_path, monkeypatch) -> None:
    from sure_eval.evaluation.tasks.slu.pipeline import evaluate_slu_files

    _install_external_nodes(monkeypatch)

    prompt_file = tmp_path / "prompt.jsonl"
    prompt_file.write_text(
        json.dumps(
            {
                "key": "q1",
                "choices": [
                    {"id": "yes", "text": "是"},
                    {"id": "no", "text": "否"},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    ref_file = tmp_path / "ref.txt"
    hyp_file = tmp_path / "hyp.txt"
    _write_key_text(ref_file, [("q1", "yes")])
    _write_key_text(hyp_file, [("q1", "是")])

    report = evaluate_slu_files(
        str(ref_file),
        str(hyp_file),
        prompt_jsonl=str(prompt_file),
        normalizer="my_norm",
        scorer="my_slu",
    )

    assert report.score == 0.75
    assert report.pipeline_id == "slu.any.accuracy.fake_norm_choice_id_v1.fake_slu_v1"
    assert [entry.node_id for entry in report.pipeline_trace] == [
        "normalization/fake_norm",
        "scoring/fake_slu",
    ]
    assert report.pipeline_trace[0].details["external"] is True
    assert report.pipeline_trace[1].details["external"] is True
