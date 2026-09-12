# SURE-EVAL Plugin Management

本文是 SURE-EVAL 插件管理的架构基线。它定义项目级插件引用、正式 Python
分发、路径加载优先级、版本锁定和远程代码信任边界。

当前状态：第一阶段本地路径版已实现：`sure-eval plugin add/list/check/remove/sync`
可用；Open-Bench 下载、远程 revision 拉取和远程 cache 仍属于第二阶段。现有
`--extra-node-path` 和 Python entry point 行为保持不变。第一阶段的路径插件形态
限制为一个目录最多包含一个 `node.py` 和一个 `routes.py`；多 node 声明属于后续扩展。

## 1. 两条独立通道

SURE-EVAL 有两种不同的插件使用方式，不能混为同一套安装机制：

| 用途 | 入口 | 可见范围 | 运行时发现 |
|---|---|---|---|
| 正式分发 | `pip install` + `sure_eval.nodes/routes` entry point | 当前 Python 环境 | `importlib.metadata` |
| 项目引入 | `sure-eval plugin add` | 当前项目 | `.sure-eval/plugins.yaml` 注入路径 |

正式分发适合发布给其他用户。项目引入适合把一个本地目录或 Open-Bench 快照固定到
某个项目中，不污染主 Python 环境。

`--extra-node-path` 是项目引入通道的临时调试路径注入，仍然保留，不写入项目配置。

## 2. 三种插件内容

插件内容由 `routes` 和 `nodes` 两个声明集合决定，`effective_kind` 由这两个集合
派生，真值以两个集合为准：

这里的「route」就是一条 pipeline 的声明（`pipeline_id` + `metric` + `nodes` +
`input_contract` + `executor`）；「仅 pipeline」场景即 `effective_kind = route`。

| `effective_kind` | 提供内容 | 说明 |
|---|---|---|
| `node` | `nodes` 非空，`routes` 为空 | 只能被发现或经 `manifest.profiles.default_for` 进入 describe choices，不能单独评分；要真正参与评分，需被某个 route（内置、其他插件或 `default_for`）引用 |
| `route` | `routes` 非空，`nodes` 为空 | route-only；route 的 `nodes` 可以引用内置节点或其他插件提供的 node |
| `node-and-route` | `nodes`、`routes` 均非空 | 同时提供节点和 pipeline route |

`kind` 是可选声明字段，仅作为一致性断言：未声明时自动推导；已声明但与
`routes`/`nodes` 不一致时，`plugin add` 硬报错。`plugin list` 显示
`effective_kind`。

这里的 `nodes` 表示插件提供的 node 入口文件（每个文件暴露一个 `NODE_ID`；第一
阶段每个路径目录只支持一个 `node.py`），
不表示 route 引用的全部 node。route-only 插件的 `nodes = []` 明确表示“不提供
node”。

## 3. 项目配置和路径基准

项目配置位于项目根目录：

```text
project/
├── .sure-eval/
│   ├── plugins.yaml
│   ├── plugins.lock.json
│   └── plugins/              # SURE-EVAL 管理的下载内容
└── plugins/                  # 用户维护的本地插件，可选
```

`project root` 定义为 `.sure-eval/` 所在目录。配置中的：

```yaml
path: "plugins/my_pipeline"
path_mode: "project_relative"
```

必须相对于项目根，而不是相对于 `.sure-eval/`。这样用户维护的
`plugins/` 和 SURE-EVAL 管理的 `.sure-eval/plugins/` 不会产生歧义。

项目根默认是当前工作目录；后续可通过顶层 flag `--project-dir` 显式指定。不得
根据多个父目录隐式猜测项目，避免同一命令因工作目录不同而加载不同插件。

由于不做向上遍历，在项目子目录运行 `plugin add` 会在子目录下新建 `.sure-eval/`。
建议在项目根运行，或显式 `--project-dir` 指向项目根。

## 4. 运行时加载顺序

节点解析保留现有顺序：

```text
builtin
  > installed entry point
  > project plugins.yaml paths
  > --extra-node-path paths
```

其中最后两项都属于 local path 来源。配置路径在命令行临时路径之前，并且两者
不是覆盖关系，而是合并关系。

同一 local 层出现相同 `node_id` 时必须显式处理，禁止静默依赖 tuple 顺序：

- `plugins.yaml` 与 `--extra-node-path` 冲突：默认报错并列出两个来源；
- 不通过临时路径覆盖项目插件，想改变身份应升级 node ID/version；
- builtin 与外部同名应产生 warning，并提示最终使用 builtin 实现（保持 builtin
  优先）；
- installed entry point 与项目/临时 local path 同名：默认硬报错，避免 local 插件被
  静默遮蔽；
- 除 builtin 优先外，任意两个外部来源出现相同 `node_id`：默认硬报错并列出全部来源；
- 任意来源出现重复的 route `pipeline_id`：默认硬报错并列出全部来源，不能静默选择。

所有 route loader、`NodeRegistry.resolve/manifest/build` 和 `env` 命令都必须在
执行自身逻辑前完成项目插件配置加载。固定加载顺序为：

```text
解析 project_dir
  -> 读取 plugins.yaml
  -> 校验 lock/hash 并构造 local_paths
  -> 合并 --extra-node-path
  -> 设置 NodeRegistry
  -> 加载 routes / 执行 node、env、describe、run
```

`NodeRegistry.resolve(local_paths=...)` 的显式 `local_paths` 参数供调用方自管路径
使用，会绕过项目插件合并；CLI 场景走 session-scoped 的 `--extra-node-path`，不走
该参数。同一进程内 `plugin add`/`remove` 改动磁盘声明后，必须失效缓存的
`project_local_paths`，让下一次 resolve/route 加载重读配置。

## 5. 声明文件

本地路径为了保持现有 `--extra-node-path` 的零声明调试体验，可以没有声明文件：

```text
routes.py      # 可选，暴露 ROUTES
node.py        # 可选，暴露 NODE_ID/STAGE/VERSION/MANIFEST/build
```

一个目录可以只含 `node.py`（`effective_kind = node`）、只含 `routes.py`
（`effective_kind = route`），或两者都有（`effective_kind = node-and-route`）；三者
都用同一个 `plugin add <dir>`，`effective_kind` 由目录内容自动推导。

三种场景的最小可验收链路如下。命令中的 `--project-dir` 可省略，省略时使用当前
工作目录；示例假设已经准备好对应的本地插件目录。

### 5.1 仅新增 node

目录只提供 `node.py`。如果希望它出现在已有 pipeline 的可选节点中，节点的
`MANIFEST.profiles.*.default_for` 必须声明匹配的 `task/language/metric`；仅有
`NODE_ID` 的 node 仍会出现在 `node list`，但不会自动进入 describe choices。

```bash
sure-eval --project-dir . plugin add ./plugins/my_node
sure-eval --project-dir . node list --json
sure-eval --project-dir . metric describe asr \
  --pipeline-id asr.en.wer.aispeech_norm_en_v1.wenet_wer_v1 --json
```

该场景的插件 `effective_kind` 为 `node`，不能单独产生评分；它必须被已有 route、
其他插件的 route 或用户编辑后的 pipeline 选择使用。

### 5.2 仅新增 pipeline（route-only）

目录只提供 `routes.py`，其中的 route 可以引用内置 node，也可以引用另一个已添加
的 node 插件。添加后按标准的 `routes -> describe -> run` 链路执行：

```bash
sure-eval --project-dir . plugin add ./plugins/my_pipeline
sure-eval --project-dir . metric routes asr --language en --metric cer --json
sure-eval --project-dir . metric describe asr \
  --pipeline-id asr.en.cer.my_norm_v1.wenet_cer_v1 \
  --output pipeline.json --json
sure-eval --project-dir . metric run --pipeline pipeline.json \
  --ref-file ref.txt --hyp-file hyp.txt --output-dir out --json
```

该场景的插件 `effective_kind` 为 `route`，`nodes` 为空只表示插件自身不提供 node，
不表示 route 的 `nodes` 为空。

### 5.3 同时新增 node 与 pipeline

目录同时提供 `node.py` 和 `routes.py`，且 route 的 `nodes` 包含该 node 的
`NODE_ID`。添加一次即可完成注册，describe/run 的 pipeline JSON 和最终报告 trace
必须保留该外部 node：

```bash
sure-eval --project-dir . plugin add ./plugins/my_node_and_pipeline
sure-eval --project-dir . metric describe asr \
  --pipeline-id asr.en.cer.my_norm_v1.wenet_cer_v1 \
  --output pipeline.json --json
sure-eval --project-dir . metric run --pipeline pipeline.json \
  --ref-file ref.txt --hyp-file hyp.txt --output-dir out --json
```

该场景的插件 `effective_kind` 为 `node-and-route`；验收时同时检查
`plugin list` 的 kind、pipeline ID 和 `report.json` 的 `pipeline_trace`。

如果存在 `sure_eval_plugin.yaml`，则在加载前校验其内容。Open-Bench 来源必须有该文件，
否则 `plugin add` 在 import 前拒绝。第一阶段无声明文件的本地目录仍按现有
`node.py`/`routes.py` 规则加载。

建议的声明字段：

```yaml
name: "my-asr-plugin"
plugin_api: "sure-eval.plugin.v1"
kind: "node-and-route"
routes: "routes.py"
nodes:
  - "node.py"
requires_sure_eval: ">=0.1"
hash_include: []
resource_manifest: "node_env.yaml"
```

`kind` 必须与 `routes`/`nodes` 是否为空一致。`plugin_api` 不兼容、kind 矛盾或
SURE-EVAL 版本不满足时，`plugin add` 硬报错，不写入配置和 lock。

`routes` 是单值（一个插件通常只有一个 `routes.py`，其 `ROUTES` 列表内含多条
route），`nodes` 是数组（目标格式允许一个插件有多个 node 文件，每个文件暴露一个
`NODE_ID`；第一阶段路径加载只接受一个 `node.py`）。

`resource_manifest` 是可选字段，仅用于声明插件运行时所需的外部资源清单；路径必须
相对于插件根目录。该文件声明运行时依赖与可寻址资源，`resource_hashes` 只校验其中
的可寻址资源（模型/checkpoint）的 hash，不校验 pip 依赖。未声明该字段表示插件不由
插件管理器固定额外运行时资源，不得在运行时隐式依赖未记录的模型、权重或缓存。

没有声明文件的本地插件，名称默认由目录名推导；CLI 应支持 `--name NAME` 覆盖该
推导值。声明文件存在时，声明中的 `name` 是规范名称，不能与项目中其他插件重复。

信任远程代码只能来自用户命令行或项目 lock，不来自 Open-Bench manifest。manifest 不
声明 `trust_remote_code`；lock 示例中的 `"trust_remote_code": true` 表示用户已经
作出的项目级信任选择，而不是仓库 manifest 授予的权限。

## 6. 配置和 lock

项目配置描述“使用哪些插件”，lock 描述“具体使用哪一份内容”。两者分开保存。
本地路径的 `plugins.yaml`：

```yaml
# .sure-eval/plugins.yaml
plugins:
  - name: "my-pipeline"
    source: "path"
    path: "plugins/my_pipeline"
    path_mode: "project_relative"
```

对应的 `plugins.lock.json`：

```json
{
  "format": "sure-eval.plugins.lock.v1",
  "plugins": [
    {
      "name": "my-pipeline",
      "source": "path",
      "resolved_path": "plugins/my_pipeline",
      "hash_format": "sure-eval.plugin-tree.v1",
      "content_hash": "sha256:...",
      "node_ids": [],
      "pipeline_ids": ["asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1"],
      "tasks": ["asr"],
      "effective_kind": "route",
      "resource_manifest": "node_env.yaml",
      "resource_hashes": {}
    }
  ]
}
```

lock 必须保存解析得到的 `node_ids`、`pipeline_ids`、`tasks` 和
`effective_kind`，即使后续内容漂移导致插件不能 import，也能据此诊断它原本提供
的能力。`resolved_path` 对 `project_relative` 插件保存规范化的项目相对路径；对
`absolute` 插件保存绝对路径，并标记 `portable = false`。

项目外路径必须标记为不可移植：

```yaml
# .sure-eval/plugins.yaml
plugins:
  - name: "external-pipeline"
    source: "path"
    path: "/absolute/path/to/plugin"
    path_mode: "absolute"
    portable: false
```

项目外路径的 lock 也应保留同一 `name/source/resolved_path/content_hash` 字段，
并明确 `portable = false`。

`.sure-eval/plugins.yaml` 和 `.sure-eval/plugins.lock.json` 是项目声明和可复现性的组成
部分，应提交 git。`.sure-eval/plugins/` 仅用于 SURE-EVAL 管理的下载内容，连同其中
的环境、checkpoint 和缓存必须加入 `.gitignore`，不应提交到仓库。

本地路径的 hash 以 `hash_format`（`sure-eval.plugin-tree.v1`）固定规则计算，采用
“先排除、后收纳”的两步规则，避免环境和缓存目录中的源码被误收：

1. 先排除固定目录（基于插件根目录内的相对路径判断，不作用于插件根目录之上的
   `.sure-eval`）：`.git`、`.venv`、`.mypy_cache`、`.pytest_cache`、`.ruff_cache`、
   `.tox`、`.sure-eval`、`__pycache__`、`__pypackages__`，以及 basename 恰为
   `checkpoint`、`checkpoints`、`cache` 或 `caches` 的目录；所有以 `.` 开头的隐藏
   目录也排除；
2. 再在剩余文件中按扩展名白名单收纳：`.py`、`.yaml`、`.yml`、`.toml`、
   `.json`、`.lock`、`.txt`；
3. 文件名和相对路径固定排序后计算 hash；
4. 不允许通过 symlink 把 hash 范围带出插件根目录。

插件如需纳入其他源码或声明文件，必须通过声明文件中的 `hash_include` 显式列出，
且每一项必须是插件根目录内的规范相对路径；不能扩大默认扫描范围或突破排除目录
边界。`hash_include` 显式列出即视为有意纳入（不限制扩展名），但排除目录仍是硬
边界——位于 `checkpoint`/`cache` 等排除目录下的文件即使被列出也不纳入。
对于本地 `path` 插件，这些运行时资源必须由声明的 `resource_manifest`（例如
`node_env.yaml`）或环境 lock 提供版本、来源和 hash，lock 中以 `resource_hashes`
保存校验结果（键是 `resource_manifest` 中声明的资源标识，值是 `sha256:...`）；
不能用远程 `revision`/etag 的规则替代。远程插件下载内容则使用 lock 中的 revision
与 hash 固定。
hash 输入还应固定文件编码、相对路径表示和换行规范，确保不同平台计算结果一致。

Open-Bench 插件 lock 还必须记录规范化的 `provider`、`repo`、不可变 `revision` 和
下载内容 hash；`source` 固定为 `open_bench`，`provider` 固定为 `open-bench`，
后续如支持 Open-Bench 镜像，再增加独立的镜像标识字段，不改变原始仓库身份：

以下是 `plugins.lock.json` 中的单个插件 entry：

```json
{
  "name": "my-asr-plugin",
  "source": "open_bench",
  "provider": "open-bench",
  "repo": "org/sure-eval-plugin",
  "revision": "<commit>",
  "hash_format": "sure-eval.plugin-tree.v1",
  "content_hash": "sha256:...",
  "node_ids": [],
  "pipeline_ids": ["asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1"],
  "tasks": ["asr"],
  "effective_kind": "route",
  "trust_remote_code": true
}
```

只使用 `main`、`master` 等可变分支不能满足可复现要求。

## 7. 命令语义

目标 CLI：

```bash
sure-eval plugin add ./plugins/my_pipeline
sure-eval plugin add ./plugins/my_pipeline --replace
sure-eval plugin add open-bench://org/repo@<revision> --trust-remote-code
sure-eval plugin list
sure-eval plugin check my-pipeline
sure-eval plugin remove my-pipeline
sure-eval plugin sync
```

第一阶段只实现本地路径：

- `add`：校验路径，写入 `.sure-eval/plugins.yaml`，并在 `.sure-eval/plugins.lock.json`
  记录解析路径和内容 hash；不复制或修改用户目录；
- `add` 对相同来源和 hash 幂等成功；同名但来源或内容不同默认硬报错，`--replace` 才能替换；
- `list`：列出 node、route、`effective_kind`、来源和 lock 状态；环境状态默认显示为 `unknown`；
- `remove`：删除项目声明和对应 lock；不删除用户本地目录；
- `sync`：校验路径存在和 hash 是否匹配；路径缺失或 hash 漂移时失败并指出原因。

`add` 和 `remove` 应保持幂等：重复添加同一来源且内容 hash 未变时成功但不重复写入，
删除不存在的名称时返回明确的“未找到”结果。名称冲突或 `--replace` 时，两个文件
必须作为一次事务更新，避免只更新一个文件：实现应先写入并 `fsync` 两个临时文件，
再通过事务标记/日志记录替换阶段，依次替换目标文件并 `fsync` 所在目录；进程中断后
启动时按事务标记恢复到上一份完整版本或提交新版本。普通的两个独立 rename 不能
宣称跨文件原子性。

第二阶段再实现 Open-Bench 下载：

- 下载内容放 `.sure-eval/plugins/`，不与现有 `get_cache_dir()` 的模型/工具缓存混用；
- `remove` 可以删除 SURE-EVAL 管理的下载目录；
- `sync` 按 lock 中的 Open-Bench `revision` 重新下载并校验 hash；
- `add` 解析 `open-bench://<owner>/<repo>@<revision>`，保存规范化 repo、revision 和
  Open-Bench 快照内容 hash；不接受浮动分支作为可复现 lock。

推荐用两个独立字段描述插件状态，避免把 lock 维度和环境维度混进一个枚举：

```text
lock_status: ready / missing / drifted / invalid / untrusted
env_status:  unknown / ready / missing
```

其中 `env_status = unknown` 表示尚未执行环境检查。`plugin list` 默认显示
`lock_status` 且 `env_status` 恒为 `unknown`；可通过 `plugin list --check-env`
或 `plugin check <name>` 显式检查后填充 `env_status`。

route 加载前必须校验每条 route 暴露 `pipeline_id`、`metric`、`nodes`、
`input_contract` 和 `executor`。task 可以从
`sure_eval.evaluation.tasks.<task>.pipeline...` executor 推断；pipeline ID
仍须通过现有的 computation node 版本链校验。route-only 插件的插件声明
`nodes = []` 只表示“不提供 node”，route 中的 `nodes` 仍可以引用内置节点。

lock 与实际内容不一致时，运行时标记为 `drifted` 并警告跳过该插件，不阻断其他
插件和内置 route；`run` 命中被跳过插件的 pipeline 时应升级为明确错误。

`missing`、`invalid` 和 `untrusted` 也必须在 import 前 warning + skip，并在状态中
保留原因和 lock 快照；只有 `lock_status = ready` 的插件允许进入 registry。`run`
命中任一被跳过插件的 pipeline 时，应报告插件名、状态、原因和对应的
`pipeline_ids`，而不是退化为“找不到 route”。

`lock_status` 与 `env_status` 必须独立展示：lock 漂移不应被误报为环境缺失，环境
满足也不能掩盖 lock 无效。lock 中保存的能力快照用于在不 import 漂移插件时说明被
跳过的 `node_ids`、`pipeline_ids` 和 `tasks`。

`env_status = missing` 不改变 `lock_status`：插件可以进入 registry 供 `describe`
读取 route/schema，但不能开始实际执行。`run` 必须在调用 executor 前完成环境检查，
并以明确的环境缺失错误终止；不能把它误报成 route 不存在或 lock 漂移。

## 8. 安全边界

当前节点是同进程 Python import。无论来源是 entry point 还是路径插件，代码都
拥有 SURE-EVAL 主进程权限；项目插件不是沙箱，也不是独立 venv。

远程插件采用以下安全模型：

```text
显式信任 + 固定 revision + 内容 hash 校验
```

没有在项目 lock（`plugins.lock.json`）中记录 `"trust_remote_code": true` 的远程
插件不得 import。该标记只表示用户明确接受执行远程代码，不表示代码经过安全审查
或获得权限隔离。

`--trust-remote-code` 只能用于 Open-Bench 来源；执行 `plugin add` 时由用户选项写入
项目 lock。后续运行时只接受项目 lock 中的信任结果，不重新采信 Open-Bench manifest
的任何同名字段。

## 9. 实现边界

项目配置与 lock 采用 YAML（`plugins.yaml`）+ JSON（`plugins.lock.json`）。Python 3.10
不提供标准库 `tomllib` 且标准库没有 TOML writer，改用 YAML/JSON 可复用已有的
`PyYAML` 依赖（`routes.yaml`/`manifest.yaml` 均为 YAML），避免为 TOML 新增
`tomli`/`tomli-w` 两个依赖。

项目配置解析必须在确定 `project_dir` 后进行，并按“读取配置、校验 lock/hash、构造
local paths、合并 CLI 临时路径”的顺序执行。不能在 import 某个插件后才补读配置，
否则同一进程中的 routes、nodes 和 env 检查会得到不一致的 registry。

## 10. 与 Open-Bench 开源社区的关系

Open-Bench 作为插件的开源分发社区，提供仓库、版本 revision、快照下载和社区协作
入口。SURE-EVAL 不把 Open-Bench 仓库自动视为可执行插件：只有用户通过
`plugin add open-bench://...` 明确引入，并通过 manifest、revision、hash 和信任标记
校验后，插件才进入项目 registry。

SURE-EVAL 与 Open-Bench 的对接采用“社区仓库 + 固定 revision + 本地插件缓存 +
显式信任”模型。插件声明、route 合并、node registry 和 pipeline 版本校验仍由
SURE-EVAL 自己负责；Open-Bench 只负责插件源码和版本快照的分发。

这里替换的是插件管理的远程分发通道，不改变现有节点在 `node_env.yaml`、运行时
provider 或模型代码中对其他模型社区/下载工具的既有依赖。

## 11. 验收范围

最小落地必须覆盖：

1. 仅 node：`plugin add` 后 `node list` 和 describe choices 可见；
2. 仅 route：route-only 插件在没有 `node.py` 时可完成 `routes → describe → run`；
3. node-and-route：外部 node trace、pipeline ID 和报告一致；
4. 配置路径与 `--extra-node-path` 合并、冲突显式报错；
5. 本地 hash 漂移能被 `sync` 发现；
6. entry point 通道行为不因项目插件管理而改变；
7. manifest 缺失、格式非法、`kind` 不一致或版本不满足时，`add` 拒绝且不改动已有配置；
8. 重复 `node_id` 或 `pipeline_id` 时列出全部来源并硬报错；
9. `missing/invalid/untrusted/drifted` 插件在 import 前跳过，命中其 pipeline 的
   `run` 给出插件状态和原因；
10. `add --replace` 或删除过程中发生中断时，下一次启动能够恢复一份完整的
    `plugins.yaml` 与 `plugins.lock.json`。
