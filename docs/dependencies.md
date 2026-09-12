# 依赖与本地部署

| 环节 | 固定依赖 | 说明 |
|---|---|---|
| 需求规划 | flake 的 llama-cpp + Qwen3-4B-Instruct-2507 Q4_K_M | 非思考版；bartowski 发布的 GGUF 约 2.33 GiB，下载版本及 SHA256 已锁定 |
| 原画 | ComfyUI + Animagine XL 4.0 Opt | FP16，832×1216，单张 |
| 修图 | Qwen-Image-Edit-2511 Q4_K_M + ComfyUI-GGUF | 2080 Ti 使用 FP32 运算避免 FP16 溢出；包含量化文本编码器、匹配文件名的 mmproj 和 VAE |
| 拆分 | See-through LayerDiff 3D + Marigold + LaMa | 六个 BF16 cast 改为 FP16；阶段之间释放模型；仅安装推理依赖 |
| 口型提取 | SAM 2.1 Hiera Tiny | 约 149 MiB；复用 Comfy 容器的 Transformers，FP32 分割后再缩放 |
| 建模 | PSD2Live 的固定版本 | 复用其 Umamo 格式模块，增加无窗口生命周期入口 |

代码来自项目官方仓库；量化文件来自相应发布仓库。下载清单记录仓库 commit、远端文件名、目标文件名、字节数及可用的 LFS SHA256。下载进入共享 HF 缓存，目标路径使用软链接，不复制大权重。

软件许可证与权重许可证分别适用。PSD2Live/Umamo 为 GPL-3.0；See-through 代码为 Apache-2.0；Animagine 为 Open RAIL++-M；Qwen 模型上游为 Apache-2.0。See-through 微调 Marigold 的模型页许可信息不完整。本仓库不包含官方 Cubism 专有组件，也不推断代码开源即代表全部模型许可一致。

源码锁定入口：
- https://github.com/tsunehimatoi/psd2live
- https://github.com/shitagaki-lab/see-through
- https://github.com/Comfy-Org/ComfyUI
- https://github.com/city96/ComfyUI-GGUF

`prepare-sources` 添加本项目入口、Gradle 任务和依赖约束；对固定 See-through 源码修改已核对的六处精度转换、一处调度器初始化和 LaMa 本地权重加载，避免运行时隐式联网。模型运行不读取 `.agent` 内的任何指令。更新上游版本后必须重新核对这些适配点。

容器默认使用宿主的 0 号显卡，各阶段串行。需要时修改 `Config.inference_command` 的 GPU 设备选择；当前不是多卡调度器。该 2080 Ti 已完成一轮 FP16 拆层；详细观察和质量限制见运行记录。Qwen 编辑可能消耗较多主机内存；先用单张、较低分辨率，不能将 4-bit 文件大小当成显存峰值。
