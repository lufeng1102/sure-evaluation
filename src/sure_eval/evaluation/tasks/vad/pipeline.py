"""VAD task routes built from validation, normalization, and scoring nodes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sure_eval.evaluation.core.pipeline import run_pipeline
from sure_eval.evaluation.core.types import (
    EvaluationFiles,
    EvaluationReport,
    MetricInputContract,
    NodePayload,
    PipelineSpec,
)
from sure_eval.evaluation.node_registry import get_registry
from sure_eval.evaluation.nodes.validation.vad_contract import AUC_METRICS, DETECTION_METRICS
from sure_eval.evaluation.pipeline_identity import (
    build_atomic_pipeline_id,
    canonical_metric,
    component_trace_ids,
    node_component,
)

_VAD_JSONL_CONTRACT = MetricInputContract(
    metric_id="task/vad_jsonl",
    required_roles=("reference_jsonl", "sample_output"),
    row_format="vad_segments_jsonl",
    alignment_key="key",
    aggregation="time_duration_micro_average_or_frame_auc",
    purpose="voice_activity_detection",
)

_SUPPORTED_PRIMARY_METRICS = set(DETECTION_METRICS + AUC_METRICS)
_DETECTION_SCORING_NODE = "scoring/vad_detection_duration"
_AUC_SCORING_NODE = "scoring/vad_auc_roc"


def _default_nodes(metric: str) -> tuple[str, ...]:
    scoring = _AUC_SCORING_NODE if metric == "auc_roc" else _DETECTION_SCORING_NODE
    return ("validation/vad_contract", "normalization/vad_timebase", scoring)


def _components_from_nodes(node_ids: tuple[str, ...], *, profile: str):
    components = []
    for node_id in node_ids:
        stage = node_id.split("/", 1)[0]
        components.append(
            node_component(node_id, profile=profile if stage == "normalization" else None)
        )
    return tuple(components)


def evaluate_vad_files(
    *,
    reference_jsonl: str | Path,
    sample_output: str | Path,
    metric: str = "f1",
    nodes: tuple[str, ...] | list[str] | None = None,
    frame_shift_sec: float = 0.01,
    profile: str = "strict",
    collar_sec: float = 0.0,
    boundary_exclusion_sec: float = 0.0,
) -> EvaluationReport:
    """Evaluate VAD predictions against strict seconds-timebase references.

    The node chain is assembled dynamically from ``nodes`` (builtin nodes are
    loaded through the node registry), so external validation/normalization/
    scoring nodes dispatch without editing this module.
    """

    normalized_metric = canonical_metric(metric)
    if normalized_metric not in _SUPPORTED_PRIMARY_METRICS:
        raise ValueError(f"Unsupported VAD metric: {metric}")

    node_ids = tuple(nodes) if nodes else _default_nodes(normalized_metric)

    input_files = EvaluationFiles(
        roles={
            "reference_jsonl": str(reference_jsonl),
            "sample_output": str(sample_output),
        }
    )
    _VAD_JSONL_CONTRACT.validate(input_files)
    payload = NodePayload(files=input_files)

    registry = get_registry()
    config = {
        "metric": normalized_metric,
        "frame_shift_sec": frame_shift_sec,
        "profile": profile,
        "collar_sec": collar_sec,
        "boundary_exclusion_sec": boundary_exclusion_sec,
    }
    built_nodes = tuple(registry.build(node_id, **config) for node_id in node_ids)

    components = _components_from_nodes(node_ids, profile=profile)
    pipeline_id = build_atomic_pipeline_id("vad", "any", normalized_metric, components)
    spec = PipelineSpec(
        pipeline_id=pipeline_id,
        task="VAD",
        language="n/a",
        metric=normalized_metric,
        nodes=built_nodes,
    )
    final_payload, trace = run_pipeline(spec, payload)

    validation_result, normalization_result, scoring_result = trace[0], trace[1], trace[-1]
    validated_bundle = final_payload.artifact("validated_bundle")
    normalized_bundle = final_payload.artifact("normalized_bundle")

    if normalized_metric == "auc_roc":
        primary_scores = {"auc_roc": scoring_result.details["auc_roc"]}
        auxiliary = {
            "num_auc_samples": scoring_result.details["num_auc_samples"],
            "positive_frames": scoring_result.details["positive_frames"],
            "negative_frames": scoring_result.details["negative_frames"],
        }
        rows = scoring_result.details["per_sample"]
    else:
        primary_scores = dict(scoring_result.details["primary_scores"])
        auxiliary = dict(scoring_result.details["auxiliary"])
        rows = scoring_result.details["per_sample"]

    selected_score = primary_scores.get(normalized_metric)
    return EvaluationReport(
        task="VAD",
        language="n/a",
        metric=normalized_metric,
        score=_report_score(selected_score),
        pipeline_id=pipeline_id,
        pipeline_trace=(validation_result, normalization_result, scoring_result),
        input_contract=_VAD_JSONL_CONTRACT,
        input_files=input_files,
        computation_node_ids=component_trace_ids(components),
        details={
            "results": scoring_result.details["results"],
            "primary_scores": primary_scores,
            "auxiliary": {
                **auxiliary,
                "skipped_metrics": scoring_result.details.get("skipped", []),
            },
            "rows": rows,
            "skipped_metrics": scoring_result.details.get("skipped", []),
            "input_summary": validated_bundle.input_summary,
            "timebase": normalized_bundle.input_summary,
            "timebase_config": {
                "frame_shift_sec": frame_shift_sec,
                "profile": profile,
                "collar_sec": collar_sec,
                "boundary_exclusion_sec": boundary_exclusion_sec,
            },
            "input_contract": _VAD_JSONL_CONTRACT.as_dict(),
            "input_files": input_files.as_dict(),
        },
    )


def pipeline_id_for_metric(metric: str, *, profile: str = "strict") -> str:
    """Return the configured VAD atomic pipeline ID for a canonical metric."""

    normalized_metric = canonical_metric(metric)
    if normalized_metric not in _SUPPORTED_PRIMARY_METRICS:
        raise ValueError(f"Unsupported VAD metric: {metric}")
    return build_atomic_pipeline_id(
        "vad",
        "any",
        normalized_metric,
        _components_from_nodes(_default_nodes(normalized_metric), profile=profile),
    )


def _report_score(score: Any) -> float | None:
    if score is None:
        return None
    return float(score)
