# 本地 AIGC → Live2D

[English](README.md) | [简体中文](README.zh-CN.md)

Live2D 制作 skill、本地网页工作台与命令行工具：需求 → 生图 → 拆分补画 → 绑定 → 检查与修正 → `.moc3` 模型包。支持自然语言修改、分支版本、断点恢复和导出。

已在本机完成真实规划、生图、修图、拆层、口型提取、无窗口绑定和模型包导出，并验证过中文删除/参数修改、改色重建及断点恢复。银发候选的闭嘴、张嘴、眨眼和小幅转头样例已检查；自动视觉质量仍需复核，官方 Core/目标应用兼容性尚未确认。详见 [真实运行记录](docs/runs/2026-09-12.md)。

## Codex 制作 skill

入口为 [`skills/live2d-production/SKILL.md`](skills/live2d-production/SKILL.md)。Codex 负责制作计划、查看实际图片和选择修复步骤，Python 工具负责执行、版本管理与校验。Skill 的源文件随仓库管理；将该目录链接到本机 Codex 的 skills 目录后，可用 `$live2d-production` 调用。它依赖本仓库与已准备的运行环境，权重和角色资产不包含在 skill 中。

例如：**“使用 $live2d-production，把这张立绘制作成可以眨眼、张嘴和小幅转头的 Live2D，检查中间结果并修复可见问题后交付。”**

新增的工具接口（在 `nix develop` 中执行）：

```sh
# 离线检查：返回图片绝对路径、部件、参数、素材与哈希检查
uv run --no-sync l2d inspect projects/my-character
uv run --no-sync l2d schema plan
uv run --no-sync l2d schema repair-edit
# 使用本地图片创建补层草稿，不需要网页上传
uv run --no-sync l2d repair create projects/my-character --mode add --source /absolute/mouth.png
uv run --no-sync l2d repair --help
# pose.json 形如 {"parameters":{"ParamAngleX":15},"long_edge":768}
uv run --no-sync l2d render projects/my-character /absolute/pose.json
uv run --no-sync l2d preview-video projects/my-character
```

`repair` 支持 create/show/list/update/run/evidence/record-review/apply/discard。修正复用网页的草稿、版本锁和像素/几何校验；本地图片被复制进草稿。`evidence` 准备带哈希的检查图片，Codex 看图后用 `record-review` 保存有来源的判断。CLI 应用要求当前视觉检查记录；过期或变化的证据不能应用，agent 检查也不会标记人工验收。接口和坐标说明见 [工具参考](skills/live2d-production/references/tools.md)。

默认使用 **agent 模式**：Codex 负责规划、看图和候选比较，**生图、修图与补画优先使用 agent 自带的图像工具**，只有用户明确指定时才使用其他工具或服务；不预设具体的底层模型名称。实际结果通过 `--plan-file`、分层 PSD 或 repair add/replace 导入。**只有用户明确要求全本地运行时才使用本地 AI 模型**；工具缺失或出错时说明具体限制，不自动切换服务或回退到本地模型。

`configs/local.toml` 中的 `[execution] mode = "agent"` 是共享默认；旧配置未写 mode 时也默认 agent。全本地任务应复制此文件到任务目录，设置 `mode = "local"`，并通过 `--config /absolute/full-local.toml` 传入；不要为一次任务修改共享默认。`inference = "container"/"native"` 只选择本地后端的运行方式，不启用本地模型。`doctor` 会显示当前模式。

模式限制覆盖本地规划/VLM、Animagine、Qwen Image Edit、See-through、SAM2 和 LaMa。PSD 处理、像素校验、透明岛拆分、绑定、渲染和导出继续使用本地程序。当前自动 PNG 拆层只有 See-through 实现，尚无云端拆层适配器；agent 模式使用已有分层 PSD，或在可行时将所选图像工具产生的图层组装并检查后导入。无法完成时明确说明缺失能力，不能静默加载本地模型。

Skill 分阶段检查和修复。缺层时保留素材、返回 `paused`，由明确步骤补齐。结构检查、视觉检查、官方运行时验证和用户接受保持分别记录；本次接口改造本身不证明成品质量已提升。

精细制作扩展覆盖 [参考目标与任务拆解](skills/live2d-production/references/detailed-production.md)、[组件与原画保真](skills/live2d-production/references/components.md)、[参数与变形](skills/live2d-production/references/parameters.md)、[表情、动作与物理](skills/live2d-production/references/performance.md)。Skill 会按需求选择这些扩展，将制作目标映射到实际组件、控制和验收证据。

已接通的制作工具：

- 制作计划支持 `parameter_create`、`warp_create`、`object_edit`，能按顺序新建控制、层级和关键形；`rig-inspect` 读取完整对象及分页几何。
- `materials-batch` 同时捕获多张原尺寸 PNG，检查位置、遮挡顺序、语义和绑定，整体预览与审查后一次应用；失败不改变项目 head。
- `performance` 管理表情、动作和物理配置的不可变快照。新模型版本固定继承当前配置，普通 `export` 自动登记和打包；支持动作时间轴、表情叠加、循环与实际 MP4 预览。
- `physics compose/put` 编写完整多节 physics3 链；`performance preview --physics` 运行连续 CPU 模拟；`verify-playback` 可将参数 trace 交给用户提供的官方 Core 逐帧检查。

```sh
uv run --no-sync l2d materials-batch schema
uv run --no-sync l2d rig-inspect projects/my-character --object OBJECT_ID --kind warp --detail object
uv run --no-sync l2d performance schema playback
uv run --no-sync l2d physics schema authoring
uv run --no-sync l2d performance preview projects/my-character /absolute/playback.json --physics
uv run --no-sync l2d verify-playback /absolute/delivery/model /absolute/parameters.json --core /absolute/libLive2DCubismCore.so
```

完整参数与版本流程见 [工具能力表](skills/live2d-production/references/tools.md#authoring-capabilities)。动作目前支持参数曲线，尚未支持 Model/PartOpacity 曲线或事件音频。CPU 物理为独立求解器，不承诺官方 SDK 数值一致；Core 检查仅验证连续参数更新及有限几何，目标应用的视觉和交互效果仍需实测。

接口回归与真实成品渲染记录见 [2026-10-15 制作 skill 验证](docs/runs/2026-10-15-production-skill.md)。
扩展工具及联合播放验证见 [2026-10-15 制作工具扩展](docs/runs/2026-10-15-authoring-tools.md)。

## 网页界面

本机环境准备好后，在项目目录运行；需要网页自主规划与推理的全本地任务另加 `--config /absolute/full-local.toml`：

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

`plan` 没有 `--plan-file` 时需要全本地模式，agent 模式会拒绝启动本地规划器。全本地模式中的 `run`、`resume`、`revise` 可能占用 GPU。导入代码、进入 Nix shell、`doctor`、`init`、`history`、`checkout` 和打包现有产物不会启动模型。

## 准备全本地推理环境

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

## 使用全本地模式

以下示例只用于明确要求全本地运行的任务。先复制配置并设置 `[execution] mode = "local"`，再传入该任务配置。默认 agent 工作流见上面的 skill 部分。

```sh
# 从自然语言创建计划；这里只运行规划模型
uv run l2d plan projects/demo '银发蓝眼，白色外套，正面半身二次元角色' --config /absolute/full-local.toml
# 先生图并暂停，以便检查构图
uv run l2d run projects/demo --until image --config /absolute/full-local.toml
# 查看原画，满意后继续；可用 --until materials 在绑定前检查拆层
uv run l2d resume projects/demo --config /absolute/full-local.toml

# 也可以从已有素材开始，PNG 会拆图，PSD 会直接进入素材规范化
uv run l2d init projects/imported
uv run l2d plan projects/imported '保留角色，生成基础动作' --source /absolute/character.psd --config /absolute/full-local.toml
uv run l2d run projects/imported --config /absolute/full-local.toml

# 创建修改分支；默认只规划，不执行图像修改
uv run l2d revise projects/demo '把前发改成浅蓝色' --config /absolute/full-local.toml
uv run l2d run projects/demo --config /absolute/full-local.toml
# 或一次规划并执行
uv run l2d revise projects/demo '减小头发摆动幅度' --execute --config /absolute/full-local.toml

uv run l2d history projects/demo
uv run l2d checkout projects/demo REVISION_ID
uv run l2d resume projects/demo --config /absolute/full-local.toml
# 看过预览后接受；接受的版本不允许重新运行覆盖
uv run l2d accept projects/demo REVISION_ID
uv run l2d export projects/demo /absolute/delivery
```

可用 `--config /absolute/config.toml` 指定运行配置。`plan --reference` 接受本地参考图，在初始生图后用 Qwen 编辑按参考约束外观；并非身份一致性保证。

## 修改与失败行为

- 支持改色、固定轮廓局部重绘、增删部件、允许的绑定参数/关键形操作，以及整图重生成。规划器只产生 Pydantic 校验后的操作，不执行 shell。
- 规划器使用 16384 token 上下文，只有文本和工程元数据，不假装拥有视觉理解；不知道对象/位置时输出 `clarification`。复杂动作、无法安全定位的部件可能需要更具体描述。
- 改色保留 alpha 和尺寸，重绘用原 alpha 作为范围并保留原透明度；轮廓变化需要重新生成/重新绑定。新部件采用显式绿幕生成与去底，适合非绿色素材，绿色部件或不干净边缘需要人工校正。
- 新增口型使用 SAM 2.1 Tiny 的框/点提示提取中央嘴部，再保持比例放入指定位置。未得到可信的独立口型时停止并保留原图、候选掩膜和分割报告，避免把下半张脸缩成嘴部贴片。其他部件先去绿底再缩放。
- 修改素材会重新绑定，**目前不承诺保留手工精修的网格**。只重放部件、层序、绑定和父级几何前提仍匹配的修正，其余列入待重新检查。仅修改姿态绑定时复制父版本完整工作区，通过 MCP 编辑。
- 缺少必要口眼图层时保留素材并暂停，使用网页或 `l2d repair` 显式补齐；`run` 不启动隐藏的规划补画任务。Skill 根据检查图片安排有限次数的修复。
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

## 精细部件与视觉检查

网页先选择操作：「生成或修改外观」「细化部件与动作」「只检查，不修改」，再用中文描述需求。细化会生成可继续编辑的修正草稿，查看候选后点击“应用”才产生正式版本。检查不会修改模型。已有方案有独立的阶段选择，可依次停在立绘、素材或模型。检查意见和独立图层均可进入部件修正工作区。「标记人工验收通过」不改变创作起点；切换起点使用版本记录中的独立按钮。任务与下载均对应正在查看的版本。

```sh
# 生成拆分/绑定草稿 → 规则校验 → 视觉复查；在网页应用
uv run l2d refine projects/my-character '把领结独立拆出来，根部固定，尾部小幅摆动' --config /absolute/full-local.toml
# 只检查，不修改工程；可附上自然语言反馈
uv run l2d review projects/my-character '检查转头和闭眼时有没有穿帮' --config /absolute/full-local.toml
```

视觉模型使用 **Qwen3.5-9B Q4_K_M + F16 视觉投影**，与 4B 文字规划器分工，按需启动并释放。权重在主 Hugging Face 缓存中，项目的 `models/vision/` 为软链接。`l2d download-models` 会下载锁定版本；可用 `configs/local.toml` 的 `[vision]` 段配置本地 llama-server。单图定位采用 0–1000 归一化坐标，再由程序转换为源图层像素，避免混淆整幅角色和局部图层坐标。

全本地拆分执行器复用 SAM2 提取源像素，先用 VLM 核验提取的是目标部件，再用已有 anime LaMa 补画下面被遮挡的区域。拆出的部件保留来源记录及原来的语义绑定；默认合成必须逐像素一致。新增绑定使用 PSD2Live 的独立子变形器、根部固定的摆动关键帧和两节点物理组，可给已有部件增加显示开关。支持衣服、头发、耳饰、头饰和其他配件。进一步的层级编辑、多节物理链使用上述制作工具；跨父坐标系编辑需检查外观，自动换装设计尚未实现。

预览覆盖现有转头、俯仰、视线、眉毛、嘴、头发和新部件参数端点及部分组合。程序检查文件完整性、素材、中性合成及新增形变的三角形翻转/塌陷；VLM 检查图像中的语义和轮廓，不以置信度代替像素证据。修正建议局限于当前类型允许的操作；明确定位到单个部件后，可在有限候选中复查和比较，定位有歧义时由用户在图上选择部件，无法判断则保留证据。原版本保持不变。

### 部件修正工作区

1. 选部件和修正类型，创建草稿。支持提取独立部件、修掩膜、PNG/JPEG 替换或新增、局部重绘、连接与摆动、指定姿态修正。
2. 掩膜用保留点、排除点、框选和画笔/橡皮擦调整；可切换 SAM2 候选、缩放平移、撤销重做。手绘区域最后覆盖分割结果，重新分割不会丢弃它们。独立透明岛直接拆开。提取轮廓与底层清理范围分别编辑，后者可扩展并独立画笔/擦除，可选 LaMa 补全或带文字要求的局部语义重绘。默认要求合成像素一致；只有明确打开“允许重绘原画中可见的区域”，才允许清理范围内的原画变化，范围外仍逐像素不变。
3. 用“检查保留/排除区域”建立命名清单；程序核对覆盖与污染，VLM 提供独立语义意见。模型建议的区域须勾选“区域已核对”。导入图片后选择明确类别（包括开口/闭口口型），设置画布 X/Y/宽/高，再查看合成预览。
4. 姿态修正先载入姿态，可圈出问题、固定连接点和拖动网格点，选择影响范围。后端使用上游形变求值器逆算父级局部坐标；不可逆、越界或映射误差超过 0.25 像素时拒绝。执行后再检查移动点实际到达误差，超过 0.5 像素不能应用；部件边缘的控制点也纳入验证。候选检查默认姿态、问题姿态、参数端点及中间值，记录翻折、塌缩和固定点漂移。
5. “预览绑定效果”构建完整候选；“应用”创建新版本，并接回姿态、视频和导出。缺少必要口型等素材时仍能保存修正，暂停绑定并禁止导出，继续补齐即可。视觉检查失败或待确认时，应修正复查，或看过证据后明确确认候选；程序硬性规则失败不能通过人工勾选绕过。最终模型仍由用户验收。

局部重绘先在空掩膜上涂出要修改的区域。程序裁剪该区域及周围上下文，以原始局部图作为画风参考，采样最长边 768，使用现有 Qwen Image Edit 模型，并将掩膜接入 ComfyUI 的 `SetLatentNoiseMask`；生成后恢复原尺寸并保护原 alpha 与掩膜外像素。它不负责扩大素材剪影。生成输入、掩膜和裁剪坐标随结果保存，便于核对。部件重绘默认不额外编码整个人物图；普通全图编辑与可选第二参考图接口保持原有行为。

拆分补全、局部重绘和绑定修正支持“自动尝试并比较候选”，最多三个，仍使用串行模型任务。材料按当前设置、扩大清理上下文、语义补画依次尝试；重绘比较不同种子；绑定只搜索有限幅度或固定范围。规则失败的候选不进入视觉选优。VLM 查看整体、底层放大图和素材露出探针；露出探针是平移合成，不冒充真实绑定或连续物理验证。绑定则查看实际采样姿态的整体及局部前后对比。

每个候选是独立草稿，卡片分别显示程序结果、VLM 判断和耗时，打开后可继续修改并返回比较。搜索预算默认每次 600 秒，达到后不启动新步骤；已启动步骤仍可能超出预算，可用任务停止按钮取消。再次搜索复用完成结果、重试临时失败；被人工编辑、应用或丢弃的候选标记为过期，不用旧证据推荐。无明确改善或缺少基线证据时不推荐，也不改变当前模型版本。

草稿保存于 `projects/<项目>/repairs/<id>/`，包含源图、基础素材、标注、候选与检查证据；刷新后可重新打开。任务绑定草稿版本和输入哈希，过期结果不发布；基础版本冲突须先切回原起点。单部件步骤有哈希缓存，失败重试复用已完成步骤，SAM2 图像编码存于磁盘，网页编辑不常驻模型显存。接口为 `/api/projects/<项目>/repairs` 的创建/查询和单草稿读取/更新/丢弃，执行统一使用 `/jobs` 的 `repair` 动作。

真实修正验收记录见 [2026-09-18 修正闭环](docs/runs/2026-09-18-repair-loop.md) 和 [2026-09-19 局部重绘与候选改进](docs/runs/2026-09-19-repair-improvements.md)。

固定回归清单位于 `docs/repair-regression-cases.json`。`nix develop --command uv run python scripts/repair_regression.py` 默认只核对本机保存的输入，不启动模型。显式增加 `--execute` 才在独立草稿中串行执行；不会应用正式版本。报告分别记录程序检查、VLM 判断、候选数量、耗时和源草稿保存次数（不是鼠标手势数），并区分真实缺陷、操作回归和缺少基线的项目。

这仍是有限能力的自动化增量：VLM/SAM 的细发束定位可能失败，LaMa 的遮挡补画需要在运动姿态下检查，静态姿态图不能检验物理的时间响应。检查通过不等于用户认可，也不代表已通过官方 Live2D Core 或目标软件验证。

模型构建完成后，点击预览区的 **生成预览视频**，生成 8 秒、24 fps、最长边 512 像素的 MP4；完成后可直接播放或下载。视频展示转头、眨眼、张嘴、身体和已绑定部件的参数动画，不模拟连续物理效果，也不验证官方运行时兼容性。渲染复用 PSD2Live 的 CPU 渲染器和 FFmpeg，不加载生图模型。进度可查看、任务可停止；结果按版本保存在 `projects/<项目>/previews/<版本>/preview.mp4`，重新生成成功后替换，不改变原模型版本。
