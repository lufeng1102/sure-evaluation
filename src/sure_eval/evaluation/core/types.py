"""Typed records for versioned evaluation pipelines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvaluationFiles:
    """Role-addressed evaluation input files."""

    roles: dict[str, str]

    @classmethod
    def from_ref_hyp(cls, ref_file: str, hyp_file: str) -> EvaluationFiles:
        return cls(roles={"ref": ref_file, "hyp": hyp_file})

    @classmethod
    def from_src_ref_hyp(cls, src_file: str, ref_file: str, hyp_file: str) -> EvaluationFiles:
        return cls(roles={"src": src_file, "ref": ref_file, "hyp": hyp_file})

    def require(self, *roles: str) -> None:
        missing = [role for role in roles if role not in self.roles or not self.roles[role]]
        if missing:
            raise ValueError(f"Missing required evaluation input role(s): {', '.join(missing)}")

    def as_dict(self) -> dict[str, str]:
        return dict(self.roles)


@dataclass(frozen=True)
class NodePayload:
    """Unified node payload: role-addressed input files plus inter-node artifacts.

    ``files`` carries every input file by role (ref/hyp/src/prediction_audio/...),
    while ``artifacts`` carries intermediate products passed between nodes
    (validated bundles, normalized bundles, transcripts, embeddings, ...).
    """

    files: EvaluationFiles
    artifacts: dict[str, Any] = field(default_factory=dict)

    def artifact(self, key: str, default: Any = None) -> Any:
        return self.artifacts.get(key, default)

    def with_artifact(self, key: str, value: Any) -> "NodePayload":
        new_payload = object.__new__(type(self))
        object.__setattr__(new_payload, "files", self.files)
        object.__setattr__(new_payload, "artifacts", {**self.artifacts, key: value})
        return new_payload


class KeyTextFiles(NodePayload):
    """Backward-compatible convenience alias for ref/hyp key-text files.

    ``KeyTextFiles(ref_file, hyp_file)`` is a :class:`NodePayload` whose ``files``
    carry the ``ref``/``hyp`` roles.  The ``ref_file``/``hyp_file`` attributes
    remain for legacy callers and external plugins written against the original
    two-file contract.
    """

    __slots__ = ()

    def __init__(self, ref_file: str, hyp_file: str) -> None:
        object.__setattr__(
            self, "files", EvaluationFiles.from_ref_hyp(ref_file=ref_file, hyp_file=hyp_file)
        )
        object.__setattr__(self, "artifacts", {})

    @property
    def ref_file(self) -> str:
        return self.files.roles["ref"]

    @property
    def hyp_file(self) -> str:
        return self.files.roles["hyp"]

    @classmethod
    def from_payload(cls, payload: "NodePayload") -> "KeyTextFiles":
        """Construct from a NodePayload carrying ref/hyp roles."""
        payload.files.require("ref", "hyp")
        return cls(ref_file=payload.files.roles["ref"], hyp_file=payload.files.roles["hyp"])


@dataclass(frozen=True)
class MetricInputContract:
    """Declared input requirements for one metric or scoring backend."""

    metric_id: str
    required_roles: tuple[str, ...]
    row_format: str = "key_text"
    alignment_key: str = "key"
    aggregation: str = "corpus_metric"
    optional_roles: tuple[str, ...] = ()
    main_report: bool = True
    purpose: str = ""
    model: str | None = None

    def validate(self, files: EvaluationFiles) -> None:
        files.require(*self.required_roles)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "metric_id": self.metric_id,
            "required_roles": list(self.required_roles),
            "optional_roles": list(self.optional_roles),
            "row_format": self.row_format,
            "alignment_key": self.alignment_key,
            "aggregation": self.aggregation,
            "main_report": self.main_report,
        }
        if self.purpose:
            payload["purpose"] = self.purpose
        if self.model:
            payload["model"] = self.model
        return payload


@dataclass(frozen=True)
class PipelineNodeResult:
    """Trace entry for one executed pipeline node."""

    stage: str
    node_id: str
    version: str
    details: dict[str, Any] = field(default_factory=dict)
    internal_stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineSpec:
    """Configured pipeline identity and executable nodes."""

    pipeline_id: str
    task: str
    language: str
    metric: str
    # Nodes accept and return the pipeline payload; both KeyTextFiles (ASR) and
    # NodePayload (role-addressed files + artifacts) are supported.
    nodes: tuple[Callable[[Any], tuple[Any, PipelineNodeResult]], ...]


@dataclass(frozen=True)
class EvaluationReport:
    """Metric result plus the exact pipeline that produced it."""

    task: str
    language: str
    metric: str
    score: float | None
    pipeline_id: str
    pipeline_trace: tuple[PipelineNodeResult, ...]
    input_contract: MetricInputContract | None = None
    input_files: EvaluationFiles | None = None
    details: dict[str, Any] = field(default_factory=dict)
    pipeline_kind: str = "atomic"
    member_pipeline_ids: tuple[str, ...] = ()
    computation_node_ids: tuple[str, ...] = ()
