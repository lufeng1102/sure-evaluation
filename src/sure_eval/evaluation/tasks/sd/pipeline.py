"""SD task route built on the generic MeetEval scoring node."""

from __future__ import annotations

from sure_eval.evaluation.core.types import EvaluationFiles, EvaluationReport, MetricInputContract
from sure_eval.evaluation.nodes.scoring.meeteval import score_meeteval
from sure_eval.evaluation.pipeline_identity import build_atomic_pipeline_id, component_trace_ids, node_component

_SD_CONTRACT = MetricInputContract(
    metric_id="scoring/meeteval",
    required_roles=("hyp", "ref"),
    row_format="meeteval_annotation",
    alignment_key="session_id",
    aggregation="session_mean_error_rate",
    purpose="speaker_diarization_error_rate",
)


def evaluate_sd_files(
    ref_file: str,
    hyp_file: str,
    *,
    metric: str = "der",
    collar: float = 0.25,
    scorer: str | None = None,
) -> EvaluationReport:
    """Evaluate speaker diarization annotations with MeetEval DER."""

    normalized_metric = metric.lower()
    if normalized_metric not in {"der", "der_dscore", "dscore"}:
        raise ValueError(f"Unsupported SD metric: {metric}")
    input_files = EvaluationFiles.from_ref_hyp(ref_file=ref_file, hyp_file=hyp_file)
    _SD_CONTRACT.validate(input_files)
    scoring_callable, _scoring_node_id = _scoring_callable(scorer)
    _, scoring_result = scoring_callable(
        ref_file=ref_file,
        hyp_file=hyp_file,
        metric="der",
        collar=collar,
    )
    result = scoring_result.details["result"]
    components = (node_component(scoring_result.node_id),)
    pipeline_id = build_atomic_pipeline_id("sd", "any", "der", components)
    return EvaluationReport(
        task="SD",
        language="n/a",
        metric="der",
        score=float(result["der"]),
        pipeline_id=pipeline_id,
        pipeline_trace=(scoring_result,),
        input_contract=_SD_CONTRACT,
        input_files=input_files,
        computation_node_ids=component_trace_ids(components),
        details={
            "scoring_result": result,
            "input_contract": _SD_CONTRACT.as_dict(),
            "input_files": input_files.as_dict(),
            "params": {"collar": collar},
        },
    )


def _scoring_callable(scorer: str | None):
    """Resolve the SD scoring node (builtin meeteval or external)."""

    normalized = (scorer or "meeteval").lower().strip()
    if normalized in {"", "meeteval", "scoring/meeteval"}:
        return score_meeteval, "scoring/meeteval"
    from sure_eval.evaluation.node_registry import get_registry

    node_id = get_registry().find_by_selector("scoring", "scorer", normalized)
    if node_id is not None:
        return get_registry().build(node_id), node_id
    raise ValueError(f"Unsupported SD scorer: {scorer}")
