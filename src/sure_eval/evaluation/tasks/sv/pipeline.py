"""Testset-level speaker-verification evaluation pipeline."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path

from sure_eval.evaluation.core.types import EvaluationFiles, EvaluationReport, MetricInputContract
from sure_eval.evaluation.nodes.scoring.cosine_trial_scores import score_cosine_trials
from sure_eval.evaluation.nodes.scoring.det_eer import score_eer
from sure_eval.evaluation.nodes.scoring.min_dcf_p005 import score_min_dcf
from sure_eval.evaluation.pipeline_identity import (
    build_atomic_pipeline_id,
    build_bundle_pipeline_id,
    component_trace_ids,
    node_component,
)

DEFAULT_METRICS = ("eer", "min_dcf")

_SV_CONTRACT = MetricInputContract(
    metric_id="scoring/sv_trials",
    required_roles=("sample_output", "trial_manifest"),
    row_format="embedding_jsonl_plus_trial_manifest",
    alignment_key="sample_id",
    aggregation="testset_trial_metric",
    purpose="testset_level_speaker_verification",
)


def evaluate_sv_files(
    sample_output: str,
    trial_manifest: str,
    *,
    metrics: Iterable[str] | None = None,
    work_dir: str | Path | None = None,
    scorers: dict[str, str] | None = None,
) -> EvaluationReport:
    """Evaluate one OpenSVBench testset protocol from speaker embeddings."""

    requested_metrics = tuple(_normalize_metric(metric) for metric in (metrics or DEFAULT_METRICS))
    if not requested_metrics:
        raise ValueError("At least one SV metric is required")
    unsupported = [metric for metric in requested_metrics if metric not in DEFAULT_METRICS]
    if unsupported:
        raise ValueError(f"Unsupported SV metric(s): {', '.join(unsupported)}")
    if len(set(requested_metrics)) != len(requested_metrics):
        raise ValueError("SV metrics must not be repeated")

    input_files = EvaluationFiles(
        roles={"sample_output": sample_output, "trial_manifest": trial_manifest}
    )
    _SV_CONTRACT.validate(input_files)
    temp_parent = Path(work_dir) if work_dir is not None else Path(sample_output).resolve().parent
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sure-eval-sv-", dir=temp_parent) as temp_dir:
        artifacts, cosine_result = score_cosine_trials(
            sample_output=sample_output,
            trial_manifest=trial_manifest,
            work_dir=temp_dir,
        )
        scores = artifacts.scores()
        labels = artifacts.labels()
        results: dict[str, dict[str, object]] = {}
        trace = [cosine_result]
        metric_node_ids: dict[str, str] = {}
        for metric in requested_metrics:
            scoring_callable, metric_node_id = _metric_scoring_callable(
                metric, (scorers or {}).get(metric)
            )
            result = scoring_callable(scores, labels)
            trace.append(result)
            results[metric] = dict(result.details["result"])
            metric_node_ids[metric] = metric_node_id

    member_pipeline_ids = tuple(
        _atomic_pipeline_id(metric, metric_node_ids[metric]) for metric in requested_metrics
    )
    if len(requested_metrics) == 1:
        pipeline_id = member_pipeline_ids[0]
        pipeline_kind = "atomic"
        report_member_pipeline_ids: tuple[str, ...] = ()
        metric_name = requested_metrics[0]
    else:
        pipeline_id = build_bundle_pipeline_id("sv", "any", member_pipeline_ids)
        pipeline_kind = "bundle"
        report_member_pipeline_ids = member_pipeline_ids
        metric_name = "multi"

    computation_node_ids = _computation_node_ids(requested_metrics, metric_node_ids)
    return EvaluationReport(
        task="SV",
        language="n/a",
        metric=metric_name,
        score=float(results[requested_metrics[0]]["score"]),
        pipeline_id=pipeline_id,
        pipeline_trace=tuple(trace),
        input_contract=_SV_CONTRACT,
        input_files=input_files,
        pipeline_kind=pipeline_kind,
        member_pipeline_ids=report_member_pipeline_ids,
        computation_node_ids=computation_node_ids,
        details={
            "results": results,
            "dataset": {
                "dataset_id": artifacts.dataset_id,
                "testset_id": artifacts.testset_id,
                "source_dataset_id": artifacts.source_dataset_id,
                "trial_count": artifacts.trial_count,
                "target_count": artifacts.target_count,
                "nontarget_count": artifacts.nontarget_count,
                "embedding_count": artifacts.embedding_count,
                "extra_embedding_count": artifacts.extra_embedding_count,
                "embedding_dimension": artifacts.embedding_dimension,
            },
            "input_contract": _SV_CONTRACT.as_dict(),
            "input_files": input_files.as_dict(),
        },
    )


def _normalize_metric(metric: str) -> str:
    normalized = str(metric).strip().lower().replace("-", "_")
    aliases = {"mindcf": "min_dcf", "min_dcf": "min_dcf", "eer": "eer"}
    return aliases.get(normalized, normalized)


def _metric_scoring_callable(metric: str, scorer: str | None):
    """Resolve the SV metric node (builtin det_eer / min_dcf_p005 or external)."""

    normalized = (scorer or "").lower().strip()
    if metric == "eer":
        if normalized in {"", "det_eer", "scoring/det_eer"}:
            return score_eer, "scoring/det_eer"
    elif metric == "min_dcf":
        if normalized in {"", "min_dcf_p005", "scoring/min_dcf_p005"}:
            return score_min_dcf, "scoring/min_dcf_p005"
    if normalized:
        from sure_eval.evaluation.node_registry import get_registry

        node_id = get_registry().find_by_selector("scoring", "scorer", normalized)
        if node_id is not None:
            return get_registry().build(node_id), node_id
    raise ValueError(f"Unsupported SV scorer for metric {metric!r}: {scorer!r}")


def _atomic_pipeline_id(metric: str, metric_node_id: str) -> str:
    return build_atomic_pipeline_id("sv", "any", metric, _components_for_metric(metric, metric_node_id))


def _components_for_metric(metric: str, metric_node_id: str):
    return (
        node_component("scoring/cosine_trial_scores"),
        node_component(metric_node_id),
    )


def _computation_node_ids(
    metrics: tuple[str, ...], metric_node_ids: dict[str, str]
) -> tuple[str, ...]:
    node_ids: list[str] = []
    for metric in metrics:
        for node_id in component_trace_ids(_components_for_metric(metric, metric_node_ids[metric])):
            if node_id not in node_ids:
                node_ids.append(node_id)
    return tuple(node_ids)
