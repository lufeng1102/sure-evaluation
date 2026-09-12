"""LID task pipeline using FireRedLID inference and classification accuracy."""

from __future__ import annotations

from collections.abc import Sequence

from sure_eval.evaluation.core.types import EvaluationFiles, EvaluationReport, MetricInputContract
from sure_eval.evaluation.nodes.inference.firered_lid import LanguageRunner, identify_languages
from sure_eval.evaluation.nodes.normalization.lid_label import (
    SUPPORTED_LANGUAGE_CODES,
    normalize_lid_rows,
)
from sure_eval.evaluation.nodes.scoring.classify import (
    load_label_spec,
    read_classification_rows,
    score_classification_rows_node,
)
from sure_eval.evaluation.pipeline_identity import (
    build_atomic_pipeline_id,
    component_trace_ids,
    node_component,
)
from sure_eval.evaluation.tasks.lid.types import LIDSample

_LID_LABEL_CONTRACT = MetricInputContract(
    metric_id="task/lid_key_labels",
    required_roles=("hyp", "ref"),
    row_format="key_label",
    alignment_key="key",
    aggregation="utterance_accuracy",
    purpose="spoken_language_identification_accuracy",
)

_FIRERED_LID_CONTRACT = MetricInputContract(
    metric_id="task/lid_samples_jsonl",
    required_roles=("samples_jsonl",),
    row_format="lid_audio_samples_jsonl",
    alignment_key="sample_id",
    aggregation="utterance_accuracy",
    purpose="spoken_language_identification",
    model="FireRedTeam/FireRedLID",
)


def evaluate_lid_files(ref_file: str, hyp_file: str) -> EvaluationReport:
    """Score labels produced by any LID system against reference labels."""

    input_files = EvaluationFiles.from_ref_hyp(ref_file, hyp_file)
    _LID_LABEL_CONTRACT.validate(input_files)
    references = read_classification_rows(ref_file)
    hypotheses = read_classification_rows(hyp_file)
    normalized_refs, normalized_hyps, normalization_result = normalize_lid_rows(
        references,
        hypotheses,
    )
    scoring_result = score_classification_rows_node(
        normalized_refs,
        normalized_hyps,
        label_spec=_lid_label_spec(normalized_refs, normalized_hyps),
    )
    result = scoring_result.details["result"]
    components = (
        node_component("normalization/lid_label", profile="canonical"),
        node_component("scoring/classify"),
    )
    return EvaluationReport(
        task="LID",
        language="n/a",
        metric="accuracy",
        score=float(result["score"]),
        pipeline_id=build_atomic_pipeline_id("lid", "any", "accuracy", components),
        pipeline_trace=(normalization_result, scoring_result),
        input_contract=_LID_LABEL_CONTRACT,
        input_files=input_files,
        computation_node_ids=component_trace_ids(components),
        details={
            "results": {"accuracy": result},
            "rows": [_label_report_row(row) for row in result["per_sample"]],
            "input_summary": {
                "num_references": len(references),
                "num_predictions": len(hypotheses),
                "reference_languages": sorted({label for _, label in normalized_refs if label}),
            },
            "input_contract": _LID_LABEL_CONTRACT.as_dict(),
            "input_files": input_files.as_dict(),
        },
    )


def evaluate_lid_samples(
    samples: Sequence[LIDSample],
    *,
    runner: LanguageRunner,
    input_manifest: str = "in_memory",
) -> EvaluationReport:
    """Identify and score spoken languages for an aligned sample collection."""

    if not samples:
        raise ValueError("at least one LID sample is required")
    sample_ids = [sample.sample_id for sample in samples]
    if len(set(sample_ids)) != len(sample_ids):
        raise ValueError("LID sample_id values must be unique")

    input_files = EvaluationFiles(roles={"samples_jsonl": input_manifest})
    _FIRERED_LID_CONTRACT.validate(input_files)
    predictions, inference_result = identify_languages(
        sample_ids,
        [sample.audio_path for sample in samples],
        runner=runner,
    )
    references = [(sample.sample_id, sample.reference_language) for sample in samples]
    hypotheses = [(row["sample_id"], row["language"]) for row in predictions]
    normalized_refs, normalized_hyps, normalization_result = normalize_lid_rows(
        references,
        hypotheses,
    )
    scoring_result = score_classification_rows_node(
        normalized_refs,
        normalized_hyps,
        label_spec=_lid_label_spec(
            normalized_refs,
            normalized_hyps,
            restrict_to_firered_inventory=True,
        ),
    )
    result = scoring_result.details["result"]
    components = (
        node_component("inference/firered_lid"),
        node_component("normalization/lid_label", profile="canonical"),
        node_component("scoring/classify"),
    )
    pipeline_id = build_atomic_pipeline_id("lid", "any", "accuracy", components)
    rows = []
    prediction_by_id = {row["sample_id"]: row for row in predictions}
    score_by_id = {row["key"]: row for row in result["per_sample"]}
    for sample in samples:
        prediction = prediction_by_id[sample.sample_id]
        scored = score_by_id[sample.sample_id]
        rows.append(
            {
                "sample_id": sample.sample_id,
                "audio_path": sample.audio_path,
                "reference_language": scored["reference"],
                "predicted_language": scored["prediction"],
                "correct": scored["correct"],
                "confidence": prediction["confidence"],
                "duration_seconds": prediction["duration_seconds"],
                "rtf": prediction["rtf"],
                "metadata": dict(sample.metadata),
            }
        )

    return EvaluationReport(
        task="LID",
        language="n/a",
        metric="accuracy",
        score=float(result["score"]),
        pipeline_id=pipeline_id,
        pipeline_trace=(inference_result, normalization_result, scoring_result),
        input_contract=_FIRERED_LID_CONTRACT,
        input_files=input_files,
        computation_node_ids=component_trace_ids(components),
        details={
            "results": {"accuracy": result},
            "rows": rows,
            "input_summary": {
                "num_samples": len(samples),
                "reference_languages": sorted({row[1] for row in normalized_refs}),
            },
            "input_contract": _FIRERED_LID_CONTRACT.as_dict(),
            "input_files": input_files.as_dict(),
        },
    )


def _lid_label_spec(
    references: list[tuple[str, str]],
    predictions: list[tuple[str, str]],
    *,
    restrict_to_firered_inventory: bool = False,
):
    labels = (
        SUPPORTED_LANGUAGE_CODES
        if restrict_to_firered_inventory
        else tuple(sorted({label for _, label in (*references, *predictions) if label}))
    )
    return load_label_spec(
        {
            "id": "lid_canonical_language_codes",
            "task": "LID",
            "labels": [{"id": code} for code in labels],
            "unknown_policy": "invalid",
            "normalization": {"case_sensitive": True, "strip_punctuation": False},
        }
    )


def _label_report_row(row: dict) -> dict:
    return {
        "sample_id": row["key"],
        "reference_language": row["reference"],
        "predicted_language": row["prediction"],
        "reference_valid": row["reference_valid"],
        "prediction_valid": row["prediction_valid"],
        "correct": row["correct"],
    }
