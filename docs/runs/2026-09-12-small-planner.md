# 4B 规划模型切换与实测

默认规划器改为 llama.cpp + Qwen3-4B-Instruct-2507 Q4_K_M（非思考版）。模型由 [Qwen 发布](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)，[bartowski 提供 GGUF](https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF)。固定 revision `ae44f08e1392f39c0e474af10c3ff8355c8b6688`，文件 2,497,280,736 字节（2.326 GiB），下载后 SHA256 校验通过；清单在 `models.lock.json`。旧的 27B 配置和软链接已移除，用户共享缓存中的原文件未删除。

## 适配与范围

直接换权重的首轮评估仅通过 1/7 项：生图属性遗漏、素材 ID 与网格 ID 混用，以及对缺失信息猜测操作。更新中文规划指令、正负提示词格式示例，并将当前素材 ID 注入 JSON Schema 枚举后通过全部用例。执行器仍校验实际绑定工具、参数和对象。

本次只运行规划，不执行产生的图像或绑定修改。规划后仍退出 llama-server，释放显存给图像模型，不增加常驻服务。未更换图像模型。

## 实测

RTX 2080 Ti 22 GiB / 32 GB 内存，llama.cpp b10809，上下文 16384、单请求、全 GPU 层。读取本机最终角色版本 `0f13ac1c741e45fc` 的真实素材、网格和工具 schema。

| 场景 | 含加载、推理、清理的耗时 | 语义检查 |
|---|---:|---|
| 新角色：颜色、长发、半身站姿、饰品排除 | 3.081 秒 | 通过 |
| 同时修改前发、后发颜色 | 8.063 秒 | 通过 |
| 删除鼻子图层 | 7.278 秒 | 通过 |
| 修改 ParamAngleX 范围与默认值 | 8.063 秒 | 通过 |
| 左眼虹膜实际 ArtMesh 的透明度关键帧 | 8.509 秒 | 通过 |
| 未指明目标时澄清 | 7.288 秒 | 通过 |
| 缺少圈选和几何信息时澄清 | 8.070 秒 | 通过 |

每 200 ms 采样 NVIDIA 进程显存，观察到 llama-server 峰值 **4,960 MiB（4.844 GiB）**，不是整卡占用。启动日志显示加载约 0.8–0.94 秒；未人为清空操作系统文件缓存，这不是磁盘冷缓存基准。用完退出的加载开销已较小，且能让图像编辑使用更多显存。

原始输出在 `projects/diagnostics/small-planner/acceptance.json`。补充站姿、精确标签及正向提示词不含否定指令的检查后，直接复核保存的响应，结果为 `projects/diagnostics/small-planner/verified.json`，未再次推理。工程元数据和上下文哈希保持不变。`baseline.json` 保留未适配时的失败结果。

这些是 7 个固定场景的检查，不代表所有需求都能正确处理，也不包含重新生图后的美观评估。小 LLM 减少规划开销，不改变 Qwen 图像编辑模型自身的推理速度。

复现（会使用 GPU，但不执行产生的计划）：

```sh
nix develop path:.
uv run python scripts/evaluate_planner.py \
  --config configs/local.toml \
  --project projects/final-acceptance \
  --revision 0f13ac1c741e45fc \
  --output projects/diagnostics/small-planner/recheck.json
```
