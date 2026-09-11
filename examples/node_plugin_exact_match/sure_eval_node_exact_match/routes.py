"""外部节点注册的 route：内置 whisper_norm（归一化）+ exact_match（打分）。

框架通过 ``sure_eval.routes`` entry point 加载本模块的 ``ROUTES``，合并进
``tasks/asr/routes.yaml``，因此无需改动仓库源码即可 ``metric routes`` /
``metric describe`` / ``metric run``。
"""

ROUTES = [
    {
        "language": "en",
        "metric": "wer",
        "pipeline_id": "asr.en.wer.whisper_norm_english_v1.exact_match_v1",
        "nodes": [
            "normalization/whisper_norm",
            "scoring/exact_match",
        ],
        "input_contract": "scoring/wenet_wer",
        "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",
    },
]
