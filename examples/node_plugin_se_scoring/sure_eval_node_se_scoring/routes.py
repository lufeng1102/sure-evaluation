"""External SE route: register the sample full-reference scoring metric."""

ROUTES = [
    {
        "metric": "my-se-metric",
        "family": "full_reference_quality",
        "pipeline_id": "se.any.my_se_metric.sample_se_metric_v1",
        "nodes": ["scoring/sample_se_metric"],
        # 复用内置 full-reference 契约（enhanced_audio + reference_audio）。
        "input_contract": "scoring/si_sdr",
        "executor": "sure_eval.evaluation.tasks.se.pipeline.evaluate_se_samples",
    },
]
