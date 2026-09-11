"""External VAD route: swap the builtin validation node for the sample one."""

ROUTES = [
    {
        "metric": "f1",
        "pipeline_id": "vad.any.f1.sample_vad_contract_v1.vad_timebase_strict_v1.vad_detection_duration_v1",
        "nodes": [
            "validation/sample_vad_contract",
            "normalization/vad_timebase",
            "scoring/vad_detection_duration",
        ],
        "computation_nodes": [
            "validation/sample_vad_contract",
            "normalization/vad_timebase",
            "scoring/vad_detection_duration",
        ],
        "input_contract": "vad_jsonl",
        "frame_shift_sec": 0.01,
        "profile": "strict",
        "collar_sec": 0.0,
        "boundary_exclusion_sec": 0.0,
        "executor": "sure_eval.evaluation.tasks.vad.pipeline.evaluate_vad_files",
    },
]
