"""外部节点注册的 route：lowercase_norm（归一化）+ 内置 wenet_wer（打分）。

框架通过 ``sure_eval.routes`` entry point 加载本模块的 ``ROUTES``，合并进
``tasks/asr/routes.yaml``，因此无需改动仓库源码即可 ``metric routes`` /
``metric describe`` / ``metric run``。
"""

ROUTES = [
    {
        "language": "en",
        "metric": "wer",
        "pipeline_id": "asr.en.wer.lowercase_norm_v1.wenet_wer_v1",
        "nodes": [
            "normalization/lowercase_norm",
            "scoring/wenet_wer",
        ],
        "input_contract": "scoring/wenet_wer",
        "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",
    },
]
