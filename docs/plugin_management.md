# SURE-EVAL Plugin Management

本文是 SURE-EVAL 插件管理的架构基线。它定义项目级插件引用、正式 Python
分发、路径加载优先级、版本锁定和远程代码信任边界。

当前状态：第一阶段本地路径版已实现：`sure-eval plugin add/list/check/remove/sync`
可用；Open-Bench 下载、远程 revision 拉取和远程 cache 仍属于第二阶段。现有
`--extra-node-path` 和 Python entry point 行为保持不变。第一阶段的路径插件形态
支持统一 `src/<package>/` 包布局及根目录兼容布局；v1 限制一个插件最多声明一个
node 模块和一个 route 模块，多 node 声明属于后续 plugin API 扩展。

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

### 5.4 统一插件包布局（`plugin add` 与 `pip install`）

为了让插件可以在“项目固定引入”和“正式 Python 分发”之间迁移，推荐两种通道共用
同一套 Python 包目录。两种通道的差异只在注册和依赖处理，不应复制一份节点实现：

```text
my_plugin/
├── pyproject.toml              # pip install 必需；plugin add 只在需要时读取
├── sure_eval_plugin.yaml       # 包布局的 SURE-EVAL manifest，建议两种通道都保留
├── README.md
├── src/
│   └── sure_eval_my_plugin/
│       ├── __init__.py
│       ├── node.py              # node-only 或 node-and-route 时存在
│       └── routes.py            # route-only 或 node-and-route 时存在
└── tests/
    └── test_plugin.py
```

包布局共用的 `pyproject.toml` 基础元数据可以写成：

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "sure-eval-my-plugin"
version = "0.1.0"
requires-python = ">=3.10"

[tool.setuptools.packages.find]
where = ["src"]
```

`tests/` 是插件作者的回归测试目录，不是 SURE-EVAL 运行时必需文件；真正决定插件
能力的是 `node.py`、`routes.py` 及其声明。entry point 表只需按下面场景追加到同一
份 `pyproject.toml`。

目录骨架保持一致，但文件要求由插件能力决定：

| 场景 | 实现文件 | `sure_eval_plugin.yaml` | `pyproject.toml` 的 entry point |
|---|---|---|---|
| `node` | `node.py`，不提供 `routes.py` | `kind: node`，声明 `node.module` | 只声明 `sure_eval.nodes` |
| `route`（pipeline-only） | `routes.py`，不提供 `node.py` | `kind: route`，声明 `route.module` | 只声明 `sure_eval.routes` |
| `node-and-route` | 同时提供 `node.py` 和 `routes.py` | `kind: node-and-route`，同时声明两个 module | 同时声明两个 entry point |

统一包布局的三种场景均必须声明 `package.module`；`node.module` 和
`route.module` 根据插件实际提供的能力分别声明，并且至少存在一个。

建议的 manifest 使用模块路径而不是固定文件路径，这样 `src/` 布局和已安装包使用
同一份声明。统一包布局的 v1 只允许一个 node 模块和一个 route 模块：

```yaml
name: "my-asr-plugin"
plugin_api: "sure-eval.plugin.v1"
kind: "node-and-route"
package:
  module: "sure_eval_my_plugin"
node:
  module: "sure_eval_my_plugin.node"
route:
  module: "sure_eval_my_plugin.routes"
requires_sure_eval: ">=0.1"
```

`kind` 仍然是派生能力的可选断言：`node`、`route`、`node-and-route` 必须分别与
实现文件和声明集合一致；route 中的 `nodes` 仍可引用内置节点或其他插件节点。

上面的 `package.module`、`node.module`、`route.module` 是统一包布局的目标字段；它们
与第一阶段根目录兼容布局使用的 `routes: "routes.py"`、`nodes: ["node.py"]` 二选一，
不能混用。

三个 module 字段的语义与约束：

- `package.module` 是可导入的包名前缀（通常对应 `src/` 下的目录；多级包使用点号表示，
  例如 `src/org/sure_eval_plugin` 对应 `org.sure_eval_plugin`），是 `node.module` /
  `route.module` 的命名空间前缀；模块必须等于该包或以 `package.module + "."` 开头，
  不能仅使用字符串 `startswith` 放宽边界。
- `node.module` / `route.module` 是包内模块的全限定名，等价于 `pyproject.toml`
  entry point 的 value。统一包布局 v1 每个插件只支持一个 node 模块，因此
  `node.module` 是单值；多 node 需要在新的 plugin API 版本中引入 `nodes` 数组，不能
  在 v1 中无版本地改变字段类型。
- node 的身份映射必须分别校验：`sure_eval.nodes` entry point **名**等于模块导出的
  `NODE_ID`，entry point **值**等于 manifest 的 `node.module`，且该模块确实承载该
  `NODE_ID`。这三项是相关但不同的字段，不能把模块路径与 node ID 直接比较。

#### node-only

只保留 `node.py`，例如：

```text
my_node/
├── pyproject.toml
├── sure_eval_plugin.yaml
└── src/sure_eval_my_node/
    ├── __init__.py
    └── node.py
```

`pyproject.toml` 的正式分发注册为：

```toml
[project.entry-points."sure_eval.nodes"]
"normalization/my_norm" = "sure_eval_my_node.node"
```

这个插件可以被 `node list` 发现；只有 route 引用它，或其 manifest 为匹配任务声明
`default_for`，它才会进入某条 pipeline 的可选节点。

#### route-only（新增 pipeline）

只保留 `routes.py`，例如：

```text
my_pipeline/
├── pyproject.toml
├── sure_eval_plugin.yaml
└── src/sure_eval_my_pipeline/
    ├── __init__.py
    └── routes.py
```

正式分发只注册 route：

```toml
[project.entry-points."sure_eval.routes"]
asr = "sure_eval_my_pipeline.routes"
```

route 的 `nodes` 可以全部引用内置节点，因此不需要 `node.py` 或
`sure_eval.nodes` entry point；安装或项目引入后可直接走
`metric routes -> metric describe -> metric run`。

#### node-and-route

同时提供 `node.py` 和 `routes.py`，route 的 `nodes` 包含该插件的 `NODE_ID`：

```text
my_node_pipeline/
├── pyproject.toml
├── sure_eval_plugin.yaml
└── src/sure_eval_my_plugin/
    ├── __init__.py
    ├── node.py
    └── routes.py
```

```toml
[project.entry-points."sure_eval.nodes"]
"normalization/my_norm" = "sure_eval_my_plugin.node"

[project.entry-points."sure_eval.routes"]
asr = "sure_eval_my_plugin.routes"
```

一次注册后，`describe` 生成的 pipeline JSON、运行报告中的 `pipeline_id` 和
`pipeline_trace` 应同时包含外部 node 和它所属的 route。

#### 注册与验证命令

正式分发通道直接安装同一目录：

```bash
python -m pip install -e ./my_plugin
sure-eval node list --json
sure-eval metric routes asr --language en --metric cer --json
```

项目固定引入通道使用同一目录：

```bash
sure-eval plugin add ./my_plugin
sure-eval plugin list --json
sure-eval metric routes asr --language en --metric cer --json
sure-eval metric describe asr --pipeline-id <pipeline-id> --output pipeline.json
sure-eval metric run --pipeline pipeline.json ...
```

两条通道都应覆盖插件作者自己的单元测试，以及 SURE-EVAL 的发现和端到端测试。对
`node-only` 检查 `node list`/节点选择；对 `route-only` 检查
`routes -> describe -> run`；对 `node-and-route` 额外检查报告中的外部节点
`pipeline_trace`。

#### 两种通道的文件与行为差异

| 项目 | `sure-eval plugin add ./my_plugin` | `pip install -e ./my_plugin` |
|---|---|---|
| 目录布局 | 与 pip 通道相同 | 与 plugin 通道相同 |
| `pyproject.toml` | 包布局方案中可作为元数据读取；不负责安装 | 必需，用于构建包、安装依赖和写入 entry point 元数据 |
| `sure_eval_plugin.yaml` | 包布局方案中用于定位 module、校验 kind/API；建议视为必需 | 当前及近期实现不读取；entry point 是发现真值。若未来启用校验，必须另行规定 manifest 的包内位置和 package-data 打包规则 |
| 注册范围 | 当前项目的 `.sure-eval/plugins.yaml` 和 lock | 当前 Python 环境的 `importlib.metadata` |
| 依赖处理 | 不自动安装，需用户先准备依赖 | 按包声明安装依赖 |
| 可复现信息 | `plugins.lock.json` 中的路径和内容 hash | 包版本/依赖 lock；entry point 本身不锁源码 hash |
| 移除方式 | `plugin remove` 只删除项目声明，不删除源码 | `pip uninstall` 删除环境中的已安装包 |

#### 两条通道等价的前提与冲突

共用同一目录不等于可以同时用两条通道注册到同一项目，须注意：

1. `__init__.py` 是本方案的插件包规范要求，用于避免不同构建后端对 namespace
   package 的处理差异；它不是 `import_module` 的 Python 绝对技术前提。
2. entry point 名必须等于 `NODE_ID`（`sure_eval.nodes`），`sure_eval.routes` 的
   entry point 名是 task 名（如 `asr`）。
3. 同一插件不要同时对同一个项目既 `pip install -e` 又 `plugin add`：包含 node 时
   两个来源会触发 `Duplicate external node_id`；route-only 插件则会触发
   `Duplicate route pipeline_id`。开发期二选一，发布后由使用者二选一。
4. 版本与依赖声明分流：pip 通道用 `pyproject.toml` 的 `[project].dependencies`
   声明可安装的 Python 依赖（若发布环境能解析 `sure-evaluation`，也可声明其版本）；
   plugin add 通道只用 manifest 的 `requires_sure_eval` 做宿主版本断言，且不安装
   Python 依赖。节点运行时依赖应继续通过 `NODE_ENV`/`node_env.yaml` 或资源清单声明，
   由用户按环境流程准备。两条通道的兼容版本要求应保持一致。

#### 实现状态与迁移规则

`plugin add` 和 `--extra-node-path` 已按 `package.module`、`node.module`、
`route.module` 解析统一 `src/<package>/` 布局；根目录直接放置
`node.py`/`routes.py` 的形态继续作为兼容布局。新插件应只使用统一布局，不要在项目根
复制第二份实现，否则会造成两套代码、hash 漂移和身份冲突。

对于 pip 分发，项目根的 `sure_eval_plugin.yaml` 默认不会自动进入 wheel。除非未来
明确规定将 manifest 放入 `src/<package>/` 并通过 package data 打包、再用
`importlib.resources` 读取，否则 pip 通道不得依赖该文件完成发现或校验。

从根目录兼容布局迁移到统一包布局的步骤：

1. 新建 `src/<package>/`，把 `node.py`/`routes.py` 移入，并新增 `__init__.py`；
2. 新增 `pyproject.toml`，声明 `[tool.setuptools.packages.find] where = ["src"]`
   与对应 entry point；
3. 把 `sure_eval_plugin.yaml` 的 `routes: "routes.py"` / `nodes: ["node.py"]`
   替换为 `package.module` / `node.module` / `route.module`；
4. 删除插件根目录下的旧 `node.py`/`routes.py`，避免两套实现；
5. 分别用 `pip install -e .` 与 `plugin add .` 验证等价；若
   使用多 node，先升级到支持 `nodes` 数组的 plugin API 版本。

包布局验收必须覆盖本节的 `node`、`route`、`node-and-route` 三种目录，
并分别验证 `plugin add` 与 `pip install` 的发现、route 注入、describe/run、依赖失败
提示和 lock/hash 行为。

仓库中的可运行统一布局示例：

- node-only：[`examples/node_only_plugin`](../examples/node_only_plugin)；
- route-only：[`examples/pipeline_plugin_cer`](../examples/pipeline_plugin_cer)；
- node-and-route：[`examples/node_pipeline_plugin_wer`](../examples/node_pipeline_plugin_wer)。

#### 兼容根目录布局的声明字段（第一阶段）

对于当前已实现的根目录 `node.py`/`routes.py` 形态，声明字段如下：

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
`NODE_ID`；v1 路径加载只接受一个 `node.py`）。

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
7. 对要求 manifest 的统一包布局/Open-Bench 插件，manifest 缺失、格式非法、`kind`
   不一致或版本不满足时，`add` 拒绝且不改动已有配置；第一阶段本地根目录兼容布局
   仍允许无 manifest；
8. 重复 `node_id` 或 `pipeline_id` 时列出全部来源并硬报错；
9. `missing/invalid/untrusted/drifted` 插件在 import 前跳过，命中其 pipeline 的
   `run` 给出插件状态和原因；
10. `add --replace` 或删除过程中发生中断时，下一次启动能够恢复一份完整的
    `plugins.yaml` 与 `plugins.lock.json`；
11. 统一包布局：`plugin add` 按 `package.module`/`node.module`/`route.module`
    解析 `src/<package>/`，`node`、`route`、`node-and-route` 三种目录的发现、
    route 注入、describe/run 与根目录布局等价；
12. 统一包布局下，node entry point name 与导出的 `NODE_ID` 不一致，或 entry point
    value 与 manifest `node.module` 不一致时，`plugin add` 明确报错；route entry
    point name 必须与 route 所属 task 一致；
13. 同一插件经 `pip install -e` 与 `plugin add` 同时注册时，含 node 的场景报告
    `Duplicate external node_id`，route-only 场景报告 `Duplicate route pipeline_id`。
