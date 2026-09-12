# 本地 AIGC → Live2D

本地网页工作台与命令行流水线：中文需求 → 本地规划 → 生图 → 拆分补画 → 绑定 → `.moc3` 模型包。支持自然语言修改、分支版本、断点恢复和导出。

已在本机完成真实规划、生图、修图、拆层、口型提取、无窗口绑定和模型包导出，并验证过中文删除/参数修改、改色重建及断点恢复。银发候选的闭嘴、张嘴、眨眼和小幅转头样例已检查；自动视觉质量仍需复核，官方 Core/目标应用兼容性尚未确认。详见 [真实运行记录](docs/runs/2026-09-12.md)。

## 网页界面

本机环境和权重准备好后，在项目目录运行：

```sh
cd /home/kurage/Projects/live2d
nix develop
uv sync --locked
uv run l2d web
```

打开 **http://127.0.0.1:7860**。可用 `uv run l2d web --port 7861` 更换端口，`--config` 指定本地配置。只监听本机，不需要 Node.js、前端编译或在线服务。退出服务用终端的 Ctrl+C。Nix 环境文件位于仓库根目录。使用 `nix develop`（不要使用 `path:.`），Nix 将使用 Git 跟踪的文件作为输入；模型、运行环境和产物由 `.gitignore` 排除。新建或修改 flake 文件后应先 `git add`。

- 自动读取 `projects/` 下的已有项目；也可在网页新建项目、上传 PNG/JPEG 立绘、分层 PSD 或参考图（每个文件最多 500 MB）。
- 输入中文需求，选择只规划、先生图、做到素材或生成完整模型。未完成版本可以载入当前需求，编辑为**完整的新需求**后重新规划；新方案与旧方案有相同的基础版本，旧产物会保留。
- 查看原画、拆分合成图、独立图层和绑定输出的姿态样例。预览为实际 CPU 渲染图片，并非浏览器里的实时 Live2D 播放器。
- 检查立绘或素材后继续生成完整模型；失败后可重试已完成阶段之后的工作。现有阶段缓存与校验规则仍适用，失败的整个阶段可能重跑。
- 对已完成版本提交修改、查看历史、切换创作起点、接受版本，并校验和下载含模型与源素材的 ZIP。
- 任务与日志保存在 `.runtime/web/`，刷新或关闭浏览器不终止任务。网页同一时间执行一个任务，并与 CLI 共用任务锁；停止按钮和退出网页服务会终止其拥有的模型进程，保留已完成阶段。服务异常中断的任务会显示中断状态，不自动重启推理。

启动网页、上传素材和查看预览不会加载模型。提交规划/生成操作才可能占用 GPU。网页复用现有 Starlette、Uvicorn、Pydantic 和本项目管线；不安装额外的生成后端，也不会将界面修改计入模型阶段的缓存指纹。

## 本地环境与检查

已按 RTX 2080 Ti 22GB / RAM 32GB 配置。规划使用本地 `llama-cpp` 与 Qwen3-4B-Instruct-2507 Q4_K_M（非思考版，权重约 2.33 GiB），模型路径为：

```
models/planner/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
```

下面是**不加载模型、不启动服务**的操作：

```sh
nix develop
uv sync --locked
uv run l2d doctor
uv run pytest -q
uv run ruff check .
uv run l2d init projects/demo
uv run l2d plan projects/demo '测试角色' --plan-file configs/example-plan.json
uv run l2d status projects/demo
```

`plan` **没有 `--plan-file` 时会启动语言模型**。`run`、`resume`、`revise` 也可能占用 GPU，请确保显卡有足够空闲资源。导入代码、进入 Nix shell、`doctor`、`init`、`history`、`checkout` 和打包现有产物不会启动模型。

## 准备环境

```sh
uv run l2d prepare-sources
uv run l2d install-environments
uv run l2d download-models
```

三个命令分别准备固定版本的源码、软件环境、模型权重，互不隐式调用。不会安装 Cubism Editor。`upstreams.lock.json`、`models.lock.json`、`uv.lock`、`flake.lock` 记录来源与版本。模型权重（含 4B 规划模型）合计约 39.8 GiB，此外还需要容器、编译缓存和产物空间；可在 `configs/local.toml` 的 `paths.models` / `paths.runtime` 指向其他本地磁盘，移动模型时同步更新 `planner.model`。

默认使用独立的 ComfyUI / See-through 容器，避免 NixOS 的动态库差异。需要 Docker 和已配置的 NVIDIA Container Toolkit CDI（只在未来执行推理时需要 GPU）。Java 21 建模和 `llama-server` 使用 flake 环境。`execution.inference = "native"` 可使用分别隔离的 Python 环境；此时同一安装命令创建 `.runtime/envs/`，仍不加载模型。

本地模型、依赖准备好后，运行时设置 Hugging Face 离线模式。`llama-server` 仅使用配置的本地文件，不下载替代模型。规划结束终止语言模型，再启动图像服务；每个图像任务完成即退出其服务。4B 模型降低加载和规划开销，完成规划后仍把显存留给图像模型。只终止本管线启动的进程或带独立随机名称的容器，不释放或终止其他 GPU 任务。

2080 Ti 上生图和拆层使用 FP16；Qwen 编辑使用量化权重加 FP32 运算。实测 FP16 编辑会在首个图像 MLP 溢出并输出黑图；FP32 完整 28 步编辑正常，单图背景修正约 18 分钟，明显慢于生图。详细结果见运行记录。容器的实际依赖版本记录在 `containers/*-requirements.lock.txt`。

4B 规划器已通过 7 项真实中文场景检查，进程显存峰值约 4.84 GiB；新建规划约 3 秒，带完整工程上下文的修改约 7–8.5 秒，包含加载与退出。已修正素材 ID 混用和提示词格式问题，详见 [小模型评估记录](docs/runs/2026-09-12-small-planner.md)。

## 使用（以下命令会运行模型）

```sh
# 从自然语言创建计划；这里只运行规划模型
uv run l2d plan projects/demo '银发蓝眼，白色外套，正面半身二次元角色'
# 先生图并暂停，以便检查构图
uv run l2d run projects/demo --until image
# 查看原画，满意后继续；可用 --until materials 在绑定前检查拆层
uv run l2d resume projects/demo

# 也可以从已有素材开始，PNG 会拆图，PSD 会直接进入素材规范化
uv run l2d init projects/imported
uv run l2d plan projects/imported '保留角色，生成基础动作' --source /absolute/character.psd
uv run l2d run projects/imported

# 创建修改分支；默认只规划，不执行图像修改
uv run l2d revise projects/demo '把前发改成浅蓝色'
uv run l2d run projects/demo
# 或一次规划并执行
uv run l2d revise projects/demo '减小头发摆动幅度' --execute

uv run l2d history projects/demo
uv run l2d checkout projects/demo REVISION_ID
uv run l2d resume projects/demo
# 看过预览后接受；接受的版本不允许重新运行覆盖
uv run l2d checkout projects/demo REVISION_ID --accept
uv run l2d export projects/demo /absolute/delivery
```

可用 `--config /absolute/config.toml` 指定运行配置。`plan --reference` 接受本地参考图，在初始生图后用 Qwen 编辑按参考约束外观；并非身份一致性保证。

## 修改与失败行为

- 支持改色、固定轮廓局部重绘、增删部件、允许的绑定参数/关键形操作，以及整图重生成。规划器只产生 Pydantic 校验后的操作，不执行 shell。
- 规划器使用 16384 token 上下文，只有文本和工程元数据，不假装拥有视觉理解；不知道对象/位置时输出 `clarification`。复杂动作、无法安全定位的部件可能需要更具体描述。
- 改色保留 alpha 和尺寸，重绘用原 alpha 作为范围并保留原透明度；轮廓变化需要重新生成/重新绑定。新部件采用显式绿幕生成与去底，适合非绿色素材，绿色部件或不干净边缘需要人工校正。
- 新增口型使用 SAM 2.1 Tiny 的框/点提示提取中央嘴部，再保持比例放入指定位置。未得到可信的独立口型时停止并保留原图、候选掩膜和分割报告，避免把下半张脸缩成嘴部贴片。其他部件先去绿底再缩放。
- 修改素材会重新绑定并重放历史绑定操作，**目前不承诺保留手工精修的网格**。仅修改绑定时复制父版本完整工作区，通过 MCP 编辑，保留其源数据。旧绑定对象无法对应新模型时直接失败，不套用到其他部件。
- 缺少必要口眼图层时尝试一次有界的规划补画，失败保留日志和中间结果；没有无限重试。不会把“理论能做”的精确变形或审美验收描述成可靠自动能力。
- 白底 PNG 拆层会对照上游保存的对齐原图，过滤几乎完全落在人物轮廓外的幻觉部件，记录 `source-support.json` 并保留原始 PSD。此检查无法发现所有语义错误，非白底或已有 PSD 不套用它。
- 每个版本保存源输入副本、计划、依赖哈希和阶段产物；恢复时校验产物和指纹。已接受版本保持不变，失败修改不会改变它。
- 单任务锁防止本管线同时运行多个大模型任务；同一工作区也有独占锁。外部 GPU 使用情况不由本工具管理。

## 产物

```
projects/demo/
  project.json                 # 当前分支与已接受版本
  revisions/<id>/
    revision.json              # 需求、父版本、状态、阶段摘要
    image/                     # 原图或导入素材
    decompose/                 # See-through 中间结果（PNG 路径）
    materials/                 # 稳定部件 ID、PNG、composite.png、检查报告
    rig/
      source.psd
      source.psd2live           # 可恢复的 PSD2Live 工作区
      rig-context.json          # 实际对象、参数、允许的 MCP 工具定义
      pose-*.png                # CPU 渲染姿态样例
      bundle/                   # MOC3、CMO3、纹理、JSON
      validation.json           # 结构检查；不等同运行验收
```

生产依赖并不随本仓库分发；许可见 [依赖说明](docs/dependencies.md)。真实模型验收见 [验收步骤](docs/acceptance.md)。
