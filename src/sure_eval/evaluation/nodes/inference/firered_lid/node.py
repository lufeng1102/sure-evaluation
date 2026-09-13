"""FireRedLID spoken-language inference with a node-local runtime."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol, Sequence

from sure_eval.evaluation.core.types import PipelineNodeResult
from sure_eval.evaluation.nodes.common.node_local_python import (
    build_node_local_env,
    resolve_node_local_python,
)

NODE_ID = "inference/firered_lid"
NODE_VERSION = "v1"
MODEL_ID = "FireRedTeam/FireRedLID"
MODEL_REVISION = "fedf637f03d1d62b499df647cb0da0ad004e6642"
SOURCE_REVISION = "4e7d9aaf4482a47cec1724807026b9b151926eb5"
NODE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = NODE_DIR / "checkpoints" / "modelscope" / "FireRedTeam" / "FireRedLID"
DEFAULT_BATCH_SIZE = 1
REQUIRED_MODEL_FILES = ("cmvn.ark", "dict.txt", "model.pth.tar")


class LanguageRunner(Protocol):
    """Runtime protocol consumed by the task pipeline."""

    def predict_batch(
        self, sample_ids: Sequence[str], audio_paths: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Return one FireRedLID-style result per input sample."""
        ...


class FireRedLIDRunner:
    """In-process adapter around the pinned upstream FireRedLID implementation."""

    def __init__(
        self,
        *,
        device: str = "cuda",
        model_dir: str | Path | None = None,
        batch_size: int | None = None,
        use_half: bool = False,
    ) -> None:
        self.device = _normalize_device(device)
        self.model_dir = _resolve_model_dir(model_dir)
        self.batch_size = _resolve_batch_size(batch_size)
        self.use_half = bool(use_half)
        self._model: Any | None = None

    def predict_batch(
        self, sample_ids: Sequence[str], audio_paths: Sequence[str]
    ) -> list[dict[str, Any]]:
        _validate_batch_inputs(sample_ids, audio_paths)
        model = self._load_model()
        results: list[dict[str, Any]] = []
        for start in range(0, len(audio_paths), self.batch_size):
            chunk_ids = list(sample_ids[start : start + self.batch_size])
            chunk_paths = list(audio_paths[start : start + self.batch_size])
            raw_results = model.process(chunk_ids, chunk_paths)
            by_id = {str(item.get("uttid") or ""): dict(item) for item in raw_results}
            if len(by_id) != len(raw_results):
                raise RuntimeError("FireRedLID returned duplicate or empty utterance ids")
            unexpected = sorted(set(by_id) - set(chunk_ids))
            if unexpected:
                raise RuntimeError(f"FireRedLID returned unexpected utterance ids: {unexpected}")
            for sample_id, audio_path in zip(chunk_ids, chunk_paths, strict=True):
                results.append(
                    by_id.get(
                        sample_id,
                        {"uttid": sample_id, "wav": audio_path, "lang": ""},
                    )
                )
        return results

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        _require_model_files(self.model_dir)
        runtime_dir = NODE_DIR / "runtime"
        if not (runtime_dir / "fireredlid" / "__init__.py").is_file():
            raise RuntimeError(
                "FireRedLID source runtime is missing; run "
                "`sure-eval env setup --node inference/firered_lid`"
            )
        if str(runtime_dir) not in sys.path:
            sys.path.insert(0, str(runtime_dir))

        import torch
        from fireredlid import FireRedLid, FireRedLidConfig

        use_gpu = self.device.startswith("cuda")
        if use_gpu:
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "FireRedLID requested CUDA but torch.cuda.is_available() is false"
                )
            if ":" in self.device:
                torch.cuda.set_device(int(self.device.split(":", 1)[1]))
        config = FireRedLidConfig(use_gpu=use_gpu, use_half=self.use_half)
        self._model = FireRedLid.from_pretrained(str(self.model_dir), config)
        return self._model


class NodeLocalFireRedLIDRunner:
    """Call the FireRedLID node through its isolated virtual environment."""

    def __init__(
        self,
        *,
        device: str = "cuda",
        model_dir: str | Path | None = None,
        node_dir: str | Path = NODE_DIR,
    ) -> None:
        self.device = _normalize_device(device)
        self.model_dir = Path(model_dir).expanduser() if model_dir else None
        self.node_dir = Path(node_dir)

    def predict_batch(
        self, sample_ids: Sequence[str], audio_paths: Sequence[str]
    ) -> list[dict[str, Any]]:
        _validate_batch_inputs(sample_ids, audio_paths)
        if not audio_paths:
            return []
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as handle:
            input_path = Path(handle.name)
            for sample_id, audio_path in zip(sample_ids, audio_paths, strict=True):
                handle.write(
                    json.dumps(
                        {"sample_id": str(sample_id), "audio_path": str(audio_path)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        try:
            args = [
                "--input-jsonl",
                str(input_path),
                "--device",
                self.device,
                "--json",
            ]
            if self.model_dir is not None:
                args.extend(("--model-dir", str(self.model_dir)))
            completed = self._run_node(args)
        finally:
            input_path.unlink(missing_ok=True)

        results: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{NODE_ID} returned invalid JSONL: {line[:500]}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"{NODE_ID} returned a non-object JSONL row")
            results.append(payload)
        if len(results) != len(audio_paths):
            raise RuntimeError(
                f"{NODE_ID} returned {len(results)} result(s) for {len(audio_paths)} input(s)"
            )
        return results

    def _run_node(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        python_runtime = resolve_node_local_python(self.node_dir, NODE_ID)
        command = [
            *python_runtime.command_prefix,
            "-m",
            "sure_eval.evaluation.nodes.inference.firered_lid.node",
            *args,
        ]
        repo_root = self.node_dir.parents[5]
        env = build_node_local_env(
            repo_src=repo_root / "src",
            extra_pythonpath=python_runtime.extra_pythonpath,
            inherit_pythonpath=python_runtime.inherit_pythonpath,
        )
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"{NODE_ID} inference failed with exit code {completed.returncode}: {message}"
            )
        return completed


def identify_languages(
    sample_ids: Sequence[str],
    audio_paths: Sequence[str],
    *,
    runner: LanguageRunner,
) -> tuple[list[dict[str, Any]], PipelineNodeResult]:
    """Run a LID backend and return stable inference trace metadata."""

    _validate_batch_inputs(sample_ids, audio_paths)
    raw_results = runner.predict_batch(sample_ids, audio_paths)
    if len(raw_results) != len(sample_ids):
        raise RuntimeError(
            f"FireRedLID runner returned {len(raw_results)} result(s) for {len(sample_ids)} input(s)"
        )

    results: list[dict[str, Any]] = []
    for expected_id, audio_path, raw in zip(sample_ids, audio_paths, raw_results, strict=True):
        sample_id = str(raw.get("sample_id") or raw.get("uttid") or expected_id)
        if sample_id != str(expected_id):
            raise RuntimeError(
                f"FireRedLID result order mismatch: expected {expected_id!r}, got {sample_id!r}"
            )
        results.append(
            {
                "sample_id": sample_id,
                "audio_path": str(raw.get("audio_path") or raw.get("wav") or audio_path),
                "language": str(raw.get("language") or raw.get("lang") or ""),
                "confidence": _optional_float(raw.get("confidence")),
                "duration_seconds": _optional_float(raw.get("duration_seconds", raw.get("dur_s"))),
                "rtf": _optional_float(raw.get("rtf")),
            }
        )

    details = {
        "backend": "fireredlid-aed",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "source_revision": SOURCE_REVISION,
        "device": getattr(runner, "device", None),
        "num_samples": len(results),
        "audio_contract": {
            "sample_rate_hz": 16000,
            "channels": 1,
            "encoding": "pcm_s16le",
        },
        "per_sample": results,
    }
    return (
        results,
        PipelineNodeResult(
            stage="inference",
            node_id=NODE_ID,
            version=NODE_VERSION,
            details=details,
            internal_stages=(
                "audio_decode",
                "fbank_feature_extraction",
                "encoder_decoder_inference",
                "language_token_decode",
            ),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--model-dir")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--use-half", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    rows = _read_input_rows(Path(args.input_jsonl))
    runner = FireRedLIDRunner(
        device=args.device,
        model_dir=args.model_dir,
        batch_size=args.batch_size,
        use_half=args.use_half,
    )
    if args.json_output:
        with redirect_stdout(sys.stderr):
            raw_results = runner.predict_batch(
                [row["sample_id"] for row in rows],
                [row["audio_path"] for row in rows],
            )
    else:
        raw_results = runner.predict_batch(
            [row["sample_id"] for row in rows],
            [row["audio_path"] for row in rows],
        )
    for expected, raw in zip(rows, raw_results, strict=True):
        payload = {
            "sample_id": str(raw.get("uttid") or expected["sample_id"]),
            "audio_path": str(raw.get("wav") or expected["audio_path"]),
            "language": str(raw.get("lang") or ""),
            "confidence": raw.get("confidence"),
            "duration_seconds": raw.get("dur_s"),
            "rtf": raw.get("rtf"),
        }
        if args.json_output:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        else:
            print(f"{payload['sample_id']}\t{payload['language']}")
    return 0


def _read_input_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"line {line_no}: row must be a JSON object")
            sample_id = str(payload.get("sample_id") or "")
            audio_path = str(payload.get("audio_path") or "")
            if not sample_id or not audio_path:
                raise ValueError(f"line {line_no}: sample_id and audio_path are required")
            rows.append({"sample_id": sample_id, "audio_path": audio_path})
    if not rows:
        raise ValueError(f"input_jsonl contains no rows: {path}")
    return rows


def _resolve_model_dir(model_dir: str | Path | None) -> Path:
    raw = model_dir or os.environ.get("FIRERED_LID_CHECKPOINT") or DEFAULT_MODEL_DIR
    path = Path(raw).expanduser()
    return path.parent if path.name == "model.pth.tar" or path.is_file() else path


def _require_model_files(model_dir: Path) -> None:
    missing = [
        str(model_dir / name) for name in REQUIRED_MODEL_FILES if not (model_dir / name).is_file()
    ]
    if missing:
        raise RuntimeError(
            f"FireRedLID checkpoint is incomplete; missing: {', '.join(missing)}. "
            "Run `sure-eval env download --node inference/firered_lid`."
        )


def _resolve_batch_size(batch_size: int | None) -> int:
    raw = (
        batch_size
        if batch_size is not None
        else os.environ.get("SURE_EVAL_FIRERED_LID_BATCH_SIZE", DEFAULT_BATCH_SIZE)
    )
    try:
        resolved = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"FireRedLID batch size must be an integer, got {raw!r}") from exc
    if resolved <= 0:
        raise ValueError("FireRedLID batch size must be positive")
    return resolved


def _normalize_device(device: str) -> str:
    normalized = str(device).strip().lower()
    if normalized == "cpu" or normalized == "cuda" or normalized.startswith("cuda:"):
        return normalized
    raise ValueError(f"FireRedLID device must be cpu, cuda, or cuda:N, got {device!r}")


def _validate_batch_inputs(sample_ids: Sequence[str], audio_paths: Sequence[str]) -> None:
    if len(sample_ids) != len(audio_paths):
        raise ValueError("sample_ids and audio_paths must have the same length")
    if len(set(str(item) for item in sample_ids)) != len(sample_ids):
        raise ValueError("sample_ids must be unique")


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
