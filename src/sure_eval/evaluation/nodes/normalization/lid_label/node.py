"""Normalize FireRedLID and reference language labels to stable codes."""

from __future__ import annotations

import re
from typing import Sequence

from sure_eval.evaluation.core.types import PipelineNodeResult

NODE_ID = "normalization/lid_label"
NODE_VERSION = "v1"

_INVALID_LABELS = {"", "<blank>", "<unk>", "<pad>", "<sos>", "<eos>"}
_BASE_LANGUAGE_CODES = """
ab af am ar as az ba be bg bn bo br bs ca ceb cs cy da de el en eo es et eu
fa fi fo fr gl gn gu gv ha haw hi hr ht hu hy ia id is it iw ja jw ka kk km kn
ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc other pa pl ps
pt ro ru sa sco sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur
uz vi war yi yo zh
""".split()
_CHINESE_REGION_CODES = (
    "zh-mandarin",
    "zh-yue",
    "zh-wu",
    "zh-min",
    "zh-north",
    "zh-xinan",
    "zh-xiang",
)
SUPPORTED_LANGUAGE_CODES = tuple(sorted(set(_BASE_LANGUAGE_CODES + list(_CHINESE_REGION_CODES))))


def normalize_lid_label(value: str) -> str:
    """Normalize ISO codes and Chinese regional labels to hyphenated lowercase."""

    normalized = str(value).strip().lower()
    if normalized in _INVALID_LABELS:
        return ""
    normalized = re.sub(r"[\s_/]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized


def normalize_lid_rows(
    references: Sequence[tuple[str, str]],
    predictions: Sequence[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], PipelineNodeResult]:
    """Normalize aligned reference and prediction rows for LID accuracy."""

    normalized_refs = [(key, normalize_lid_label(label)) for key, label in references]
    normalized_hyps = [(key, normalize_lid_label(label)) for key, label in predictions]
    return (
        normalized_refs,
        normalized_hyps,
        PipelineNodeResult(
            stage="normalization",
            node_id=NODE_ID,
            version=NODE_VERSION,
            details={
                "profile": "canonical",
                "separator": "hyphen",
                "case": "lower",
                "num_references": len(normalized_refs),
                "num_predictions": len(normalized_hyps),
            },
            internal_stages=("trim", "casefold", "separator_normalization"),
        ),
    )
