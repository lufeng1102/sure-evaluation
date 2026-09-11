"""External SA-ASR route: swap the builtin normalization node for the sample one."""

ROUTES = [
    {
        "language": "en",
        "metric": "cpwer",
        "pipeline_id": "sa_asr.en.cpwer.conversion_sa_asr_cpwer_v1.sa_asr_sample_norm_v1.meeteval_v1",
        "nodes": [
            "normalization/sa_asr_sample_norm",
            "scoring/meeteval",
        ],
        "input_contract": "scoring/meeteval",
        "executor": "sure_eval.evaluation.tasks.sa_asr.pipeline.evaluate_sa_asr_files",
        "params": {
            "collar": 0.5,
            "normalization_node": "normalization/sa_asr_sample_norm",
            "companion_metrics": ["der"],
        },
    },
]
