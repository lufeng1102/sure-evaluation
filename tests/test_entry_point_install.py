"""Real ``pip install -e`` integration test for entry-point node discovery.

This test mutates the project virtualenv (install + uninstall the example
package) and shells out to the CLI, so it is marked ``integration`` and excluded
from the default pytest run.  Run it explicitly with ``-m integration``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE = "sure-eval-node-lowercase"


def _venv_bin(name: str) -> Path:
    return _ROOT / ".venv" / "bin" / name


@pytest.mark.integration
def test_entry_point_installed_package_cli_end_to_end(tmp_path: Path) -> None:
    python = _venv_bin("python")
    pip = _venv_bin("pip")
    sure_eval = _venv_bin("sure-eval")
    package_dir = _ROOT / "examples" / "node_plugin_lowercase"

    if not (python.exists() and pip.exists() and sure_eval.exists()):
        pytest.skip("requires the project virtualenv (`.venv`) with the sure-eval console script")

    cache_dir = tmp_path / "cache"
    env = {**os.environ, "SURE_EVAL_CACHE_DIR": str(cache_dir)}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(arg) for arg in args],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            env=env,
        )

    install = run(pip, "install", "-e", str(package_dir), "--no-build-isolation")
    assert install.returncode == 0, install.stderr

    try:
        # 1) routes: the installed package injects its route via entry point
        routes = run(
            sure_eval, "metric", "routes", "asr",
            "--language", "en", "--metric", "wer", "--json",
        )
        assert routes.returncode == 0, routes.stderr
        pipeline_ids = [route["pipeline_id"] for route in json.loads(routes.stdout)["routes"]]
        assert "asr.en.wer.lowercase_norm_v1.wenet_wer_v1" in pipeline_ids

        # 2) describe: resolves the external node via the installed entry point
        pipeline_path = tmp_path / "pipeline.json"
        describe = run(
            sure_eval, "metric", "describe", "asr",
            "--pipeline-id", "asr.en.wer.lowercase_norm_v1.wenet_wer_v1",
            "--output", str(pipeline_path), "--json",
        )
        assert describe.returncode == 0, describe.stderr

        # 3) run: the executor dispatches the external lowercase_norm node
        output_dir = tmp_path / "out"
        run_result = run(
            sure_eval, "metric", "run",
            "--pipeline", str(pipeline_path),
            "--ref-file", str(_ROOT / "examples" / "readme" / "asr_en_ref.txt"),
            "--hyp-file", str(_ROOT / "examples" / "readme" / "asr_en_hyp.txt"),
            "--output-dir", str(output_dir), "--json",
        )
        assert run_result.returncode == 0, run_result.stderr
        payload = json.loads(run_result.stdout)
        assert payload["pipeline_id"] == "asr.en.wer.lowercase_norm_v1.wenet_wer_v1"
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        assert [entry["node_id"] for entry in report["pipeline_trace"]] == [
            "normalization/lowercase_norm",
            "scoring/wenet_wer",
        ]
    finally:
        run(pip, "uninstall", "-y", _PACKAGE)
