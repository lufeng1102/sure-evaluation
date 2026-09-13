"""LID task sample types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LIDSample:
    """One audio utterance and its expected spoken-language code."""

    audio_path: str
    reference_language: str
    sample_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["LIDSample"]
