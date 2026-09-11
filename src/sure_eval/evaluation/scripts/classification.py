"""Classification configured script route descriptors."""

from __future__ import annotations

from pathlib import Path

from sure_eval.evaluation.scripts.contracts import (
    call_route_executor,
    contract_from_manifest,
    describe_from_contracts,
    find_pipeline_route,
    find_task_route,
    load_task_manifest,
    load_task_routes,
    write_route_run_outputs,
)


def describe_pipeline(
    *, task: str = "classification", metric: str = "accuracy", pipeline_id: str | None = None
):
    if metric.lower() != "accuracy":
        raise ValueError(f"Unsupported classification metric: {metric}")
    manifest, manifest_path, routes_path, route, normalized_task = _select_route(
        task=task, pipeline_id=pipeline_id
    )
    return describe_from_contracts(
        task=normalized_task,
        pipeline_id=route["pipeline_id"],
        metric="accuracy",
        language="n/a",
        node_ids=tuple(route["nodes"]),
        contracts=(contract_from_manifest(manifest, route["input_contract"]),),
        task_config_path=manifest_path,
        route_config_path=routes_path,
        computation_node_ids=tuple(route["nodes"]),
        execution_metrics=("accuracy",),
        script_module=__name__,
        executor=str(route.get("executor") or ""),
    )


def run(
    ref_file: str,
    hyp_file: str,
    *,
    output_dir: str,
    task: str = "classification",
    pipeline_id: str | None = None,
    label_spec: str | Path | dict | None = None,
):
    if not output_dir:
        raise ValueError("output_dir is required")
    _, _, _, route, normalized_task = _select_route(task=task, pipeline_id=pipeline_id)
    description = describe_pipeline(task=normalized_task, metric="accuracy", pipeline_id=pipeline_id)
    selectors = _executor_selectors_from_route(route)
    report = call_route_executor(
        route,
        ref_file=ref_file,
        hyp_file=hyp_file,
        task=normalized_task,
        label_spec=label_spec,
        scorer=selectors.get("scorer"),
    )
    return write_route_run_outputs(report=report, description=description, output_dir=output_dir)


def _executor_selectors_from_route(route: dict) -> dict[str, str]:
    selectors: dict[str, str] = {}
    for node_id in route.get("nodes") or ():
        if node_id == "scoring/classify":
            selectors["scorer"] = "classify"
        else:
            _apply_external_selectors(selectors, node_id)
    return selectors


def _apply_external_selectors(selectors: dict[str, str], node_id: str) -> None:
    """Fallback: read selector hints from an external (plugin) node registration."""

    from sure_eval.evaluation.node_registry import get_registry

    try:
        registration = get_registry().resolve(node_id)
    except KeyError:
        return
    selectors.update({key: str(value) for key, value in registration.selectors.items()})


def _select_route(*, task: str = "classification", pipeline_id: str | None = None):
    manifest, manifest_path = load_task_manifest("classification")
    normalized_task = task.upper() if task.upper() in {"SER", "GR"} else task
    routes, routes_path = load_task_routes("classification")
    if pipeline_id:
        route = find_pipeline_route(routes, pipeline_id=pipeline_id)
        normalized_task = route.get("task_alias") or normalized_task
    else:
        route = find_task_route(routes, metric="accuracy", task_alias=normalized_task)
    return manifest, manifest_path, routes_path, route, normalized_task
