"""Spoken language identification evaluation task."""

from sure_eval.evaluation.tasks.lid.pipeline import evaluate_lid_files, evaluate_lid_samples
from sure_eval.evaluation.tasks.lid.types import LIDSample

__all__ = ["LIDSample", "evaluate_lid_files", "evaluate_lid_samples"]
