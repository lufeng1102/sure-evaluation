"""Tests for unified registry dispatch on the SA-ASR task."""

from __future__ import annotations

import pytest

from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult
from sure_eval.evaluation.node_registry import get_registry
from sure_eval.evaluation.pipeline_identity import component_instance_id
from sure_eval.evaluation.tasks.sa_asr.pipeline import (
    _normalization_component,
    _resolve_normalization_node,
    evaluate_sa_asr_files,
)


def test_sa_asr_builtin_nodes_expose_build() -> None:
    reg = get_registry()
    for node_id in (
        "normalization/gstar_norm",
        "normalization/whisper_norm",
        "scoring/meeteval",
    ):
        assert reg.resolve(node_id).build is not None, node_id


def test_resolve_normalization_node_language_defaults() -> None:
    assert _resolve_normalization_node(language="zh", normalization_node=None) == "normalization/gstar_norm"
    assert _resolve_normalization_node(language="en", normalization_node=None) == "normalization/whisper_norm"


def test_resolve_normalization_node_passes_external_id() -> None:
    assert (
        _resolve_normalization_node(language="en", normalization_node="normalization/my_norm")
        == "normalization/my_norm"
    )


def test_resolve_normalization_node_language_constraint_builtin() -> None:
    with pytest.raises(ValueError):
        _resolve_normalization_node(language="zh", normalization_node="normalization/whisper_norm")


def test_normalization_component_profiles() -> None:
    assert component_instance_id(_normalization_component("normalization/gstar_norm")) == "gstar_norm_v1"
    assert (
        component_instance_id(_normalization_component("normalization/whisper_norm"))
        == "whisper_norm_english_v1"
    )
    # External nodes carry no builtin profile suffix.
    assert (
        component_instance_id(_normalization_component("normalization/sa_asr_sample_norm"))
        == "sa_asr_sample_norm_v1"
    )


def test_evaluate_sa_asr_files_dispatches_via_registry(tmp_path, monkeypatch) -> None:
    ref_stm = tmp_path / "ref.stm"
    hyp_stm = tmp_path / "hyp.stm"
    stm_line = "session1 A spk1 0.0 1.0 <o,f0,m> hello world\n"
    ref_stm.write_text(stm_line, encoding="utf-8")
    hyp_stm.write_text(stm_line, encoding="utf-8")

    from sure_eval.evaluation.nodes.scoring.meeteval import node as meeteval_node

    def fake_score(*, ref_file, hyp_file, metric, collar, companion_metrics):
        return (
            KeyTextFiles(ref_file=ref_file, hyp_file=hyp_file),
            PipelineNodeResult(
                stage="scoring",
                node_id="scoring/meeteval",
                version="v1",
                details={"result": {"cpwer": 0.0, "der": {"error_rate": 0.0}}},
            ),
        )

    monkeypatch.setattr(meeteval_node, "score_meeteval", fake_score)

    report = evaluate_sa_asr_files(
        ref_file=str(ref_stm),
        hyp_file=str(hyp_stm),
        language="en",
        metric="cpwer",
    )
    assert report.metric == "cpwer"
    assert report.score == 0.0
    assert (
        report.pipeline_id
        == "sa_asr.en.cpwer.conversion_sa_asr_cpwer_v1.whisper_norm_english_v1.meeteval_v1"
    )
    assert len(report.pipeline_trace) == 2


def test_evaluate_sa_asr_files_builds_expected_node_ids(tmp_path, monkeypatch) -> None:
    ref_stm = tmp_path / "ref.stm"
    hyp_stm = tmp_path / "hyp.stm"
    stm_line = "session1 A spk1 0.0 1.0 <o,f0,m> hello world\n"
    ref_stm.write_text(stm_line, encoding="utf-8")
    hyp_stm.write_text(stm_line, encoding="utf-8")

    from sure_eval.evaluation import node_registry as nr
    from sure_eval.evaluation.nodes.scoring.meeteval import node as meeteval_node

    def fake_score(*, ref_file, hyp_file, metric, collar, companion_metrics):
        return (
            KeyTextFiles(ref_file=ref_file, hyp_file=hyp_file),
            PipelineNodeResult(
                stage="scoring",
                node_id="scoring/meeteval",
                version="v1",
                details={"result": {"cpwer": 0.0}},
            ),
        )

    monkeypatch.setattr(meeteval_node, "score_meeteval", fake_score)

    built = []
    original_build = nr.NodeRegistry.build

    def recording_build(self, node_id, **config):
        built.append(node_id)
        return original_build(self, node_id, **config)

    monkeypatch.setattr(nr.NodeRegistry, "build", recording_build)

    evaluate_sa_asr_files(
        ref_file=str(ref_stm),
        hyp_file=str(hyp_stm),
        language="zh",
        metric="cpwer",
    )
    assert built == ["normalization/gstar_norm", "scoring/meeteval"]
