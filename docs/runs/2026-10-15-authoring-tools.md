# 2026-10-15 精细制作工具扩展

## 目标与实现

按“控制与层级 → 多图层组装 → 表情/动作管理和播放 → 复杂物理与连续检查”的顺序接通工具。使用三个并行 agent 实现各模块，主 agent 集成、复核和执行联合验证。默认 agent 图像工具政策保持生效；本次未运行本地 AI 模型。

| 能力 | 实现 |
| --- | --- |
| 控制与层级 | Plan 开放 parameter_create、warp_create、object_edit；按操作顺序刷新对象缓存；rig-inspect 可查询完整关键形、层级及分页几何 |
| 多图层组装 | materials-batch 支持 add/replace/update/remove、原尺寸位置与顺序、语义、左右侧、binding、输入快照、整体证据及版本审查；成功后一次提交，失败不改变 head |
| 表情和动作 | performance 保存不可变快照，固定父版本继承；表情混合、淡入淡出、四种参数曲线、时间轴与循环；sample 和 preview 播放实际内容 |
| 复杂物理 | physics compose/put 管理多粒子、多输入/输出的标准 physics3 文档；连续固定步长 CPU 预览 |
| 连续 Core 检查 | verify-playback 在隔离子进程中驱动实际参数 trace，逐帧检查全部 drawable 顶点和真实参数范围 |
| 导出与检查 | 常规 export 自动打包表情、动作、物理和快照；重新验证导出副本；inspect 显示库版本和条目 |

历史重放按依赖组筛选，只有有效旧操作进入新模型。跨版本折叠会排除之前已失效的操作，防止后续重建重新执行旧坐标。当前计划的新操作在历史筛选后执行。批量应用失败也清理新建的 performance 指针目录。

## 自动检查

执行：

```sh
nix develop --offline --command uv run --no-sync pytest -q
.venv/bin/ruff check .
.venv/bin/python /home/kurage/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/live2d-production
git diff --check
```

结果：279 项测试通过；Ruff、skill 校验、diff 空白检查通过。测试覆盖创建后立即绑定、层级与关键形保存、历史排除、批量操作原子性、输入和证据过期、库版本冲突、继承与导出、动作四类曲线及范围、表达式叠加、多节物理迟滞/稳定性、Core 隔离和失败定位。原有 Starlette 测试依赖有两条弃用提示。

Core 的成功几何测试使用明确的 mock ABI；真实子进程只验证了错误动态库被拒绝。没有使用这些测试声称官方 Core 兼容。

## 真实 PSD2Live 验证

使用现有铃乃 `3bd6e6a9c8af4b94` 的临时副本。原项目的 source.psd、source.psd2live 以及联合验证前后的整个 revision 文件哈希保持一致。

1. 新增 `ParamSkillStrand`，创建 `DeformSkillOuter → DeformSkillInner` 两级 Warp，新增关键形并做局部平移；执行 rename/bind/move，再保存和重开。
2. 内层 Warp 实际为 8×6 分段、63 点、2 个关键形；测试姿态翻折/塌缩为 0。请求的网格密度会按父网格对齐，必须读取实际结果。
3. `rig-inspect` 在 `ParamAngleX=15`、canvas 空间读取前发，返回 8 点及 nextOffset=8；原 workspace 哈希不变。

记录目录：`.runtime/skill-validation/rig-authoring/`，包含 `result.json`、`authoring-plan.json`、对象和几何数据、重开 context 及 `rig-query-result.json`。

随后通过公开 CLI `plan --plan-file` → `run --until rig` → `export` 执行了同一组操作。测试项目产生完整 built 版本 `0ad2e1b57faf4b3f`，参数数为 22；重新读取确认 `DeformSkillOuter → DeformSkillInner → ArtMeshFrontHair` 层级与新参数存在，原生产模型各 stage 文件哈希不变。该新版本固定继承下述 performance 快照，导出到 `.runtime/skill-validation/authoring-delivery`，模型结构和已登记的表情、Custom 动作及多节物理文件均通过检查。汇总见 `.runtime/skill-validation/authoring-pipeline-result.json`。

## 表情、动作、物理联合验证

在 `.runtime/skill-validation/performance-project` 中复制真实铃乃产物作为工具测试输入，创建 snapshot `f65a1ed5bdec5617c081803b9b15a067e9f5e743949c87123b711d983d57cb8d`：

- 自定义转头及口型动作 `Custom/turn`。
- 表情候选 `smile`，含嘴形与双眼乘法混合。
- 一条四粒子链，从头部角度输入，输出到前后发控制。
- 2 秒、12 fps、512 长边的真实 CPU 渲染，共 24 帧；trace 包含端点，共 25 个采样。
- 固定 60 Hz 物理累计 120 步，输出范围裁切计数 0。

已查看 0、0.5、1、1.5 秒的四帧图：角色完整可见，姿态和口型发生变化，所查帧未见大片空洞或丢层。此检查只验证联合工具可用，未将原模型重制为参考质量，也未评定动作自然度。

证据：

- `.runtime/skill-validation/performance-result.json`
- `.runtime/skill-validation/performance-frames.png`
- `.runtime/skill-validation/performance-project/previews/2e9def29acf14382/performances/c7f7b7b72e0c4faab16a597911586581/preview.mp4`
- 同目录的 `parameters.json`、`playback.json`
- `.runtime/skill-validation/performance-delivery/validation.json`

导出副本保留生成的动作组，并添加 Custom、smile、完整 physics3 及 library 快照；导出后的结构校验通过。

## 明确边界

- Warp 创建需要共同 Warp 父级；跨父级编辑要求显式 local 坐标，不保证外观不变。keyform_set 的 coordinate 必须非空。
- 动作只支持 Parameter 曲线；Model、PartOpacity、事件及音频调度会明确拒绝。
- CPU 动作、表情和物理为制作预览实现，不宣称与官方 SDK 的状态管理或物理数值一致。
- 本机缺少官方 Core 库，官方连续几何检查和目标应用播放尚未实测。Core 检查本身也不运行 SDK 物理或验证渲染像素。
- 新工具使精细组件与控制可以制作和迭代；最终精细程度仍需实际原画、拆层、关键形、动作调校和逐项视觉验收。
