"""Tests for the ASR payload convergence (KeyTextFiles -> NodePayload alias)."""

from __future__ import annotations

from sure_eval.evaluation.core.types import (
    EvaluationFiles,
    KeyTextFiles,
    NodePayload,
    PipelineNodeResult,
)


def test_key_text_files_is_node_payload() -> None:
    payload = KeyTextFiles("ref.txt", "hyp.txt")
    assert isinstance(payload, NodePayload)
    assert payload.files.roles == {"ref": "ref.txt", "hyp": "hyp.txt"}
    assert payload.artifacts == {}


def test_key_text_files_legacy_attributes_remain() -> None:
    payload = KeyTextFiles("ref.txt", "hyp.txt")
    assert payload.ref_file == "ref.txt"
    assert payload.hyp_file == "hyp.txt"


def test_key_text_files_inherits_artifact_helpers() -> None:
    payload = KeyTextFiles("ref.txt", "hyp.txt")
    assert payload.artifact("missing", "dflt") == "dflt"
    updated = payload.with_artifact("normalized_bundle", "bundle")
    assert updated.artifacts == {"normalized_bundle": "bundle"}
    # with_artifact preserves the KeyTextFiles subtype.
    assert isinstance(updated, KeyTextFiles)
    assert updated.ref_file == "ref.txt"
    assert updated.hyp_file == "hyp.txt"


def test_key_text_files_from_payload() -> None:
    payload = NodePayload(files=EvaluationFiles.from_ref_hyp("r.txt", "h.txt"))
    legacy = KeyTextFiles.from_payload(payload)
    assert legacy.ref_file == "r.txt"
    assert legacy.hyp_file == "h.txt"
    assert isinstance(legacy, NodePayload)


def test_plain_node_payload_is_not_key_text_files() -> None:
    payload = NodePayload(files=EvaluationFiles(roles={"reference_jsonl": "a"}))
    assert isinstance(payload, NodePayload)
    assert not isinstance(payload, KeyTextFiles)


def test_asr_pipeline_payload_is_node_payload() -> None:
    """The ASR executor passes a KeyTextFiles, which is now a NodePayload subtype."""

    from sure_eval.evaluation.core.pipeline import run_pipeline
    from sure_eval.evaluation.core.types import PipelineSpec

    seen: list[str] = []

    def node(payload: NodePayload) -> tuple[NodePayload, PipelineNodeResult]:
        seen.append(type(payload).__name__)
        return payload, PipelineNodeResult(stage="scoring", node_id="scoring/x", version="v1")

    spec = PipelineSpec(
        pipeline_id="asr.en.wer.x_v1",
        task="ASR",
        language="en",
        metric="wer",
        nodes=(node,),
    )
    run_pipeline(spec, KeyTextFiles("ref.txt", "hyp.txt"))
    # KeyTextFiles flows through as a NodePayload (its superclass).
    assert seen == ["KeyTextFiles"]
