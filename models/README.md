模型文件不入版本管理。所有模型（含规划模型）由显式命令下载到 Hugging Face 主缓存（默认 `~/.cache/huggingface/hub`，遵循 `HF_HOME` / `HF_HUB_CACHE`），校验大小与 SHA256 后链接到使用路径：

```sh
uv run l2d download-models
```

默认规划模型为 `planner/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`，约 2.33 GiB，非思考版；来源和固定版本见 `models.lock.json`。运行规划时只加载本地文件。

项目 `models/` 仅保留指向主缓存的软链接。离线推理容器以只读方式挂载主缓存，不挂载其中的账户凭据。

配置 `paths.models` 可改变软链接入口位置；更换权重存储磁盘应设置 `HF_HUB_CACHE`，并重新运行下载命令建立链接。`planner.model` 独立指定语言模型路径，移动模型目录时需要同步更新。原有 27B 缓存不再被本项目引用。

视觉检查与部件定位使用 `vision/Qwen3.5-9B-Q4_K_M.gguf`（约 5.29 GiB）和 `vision/mmproj-F16.gguf`（约 0.86 GiB）。两者也使用主缓存软链接；来源与校验值在 `models.lock.json`。`[vision]` 的模型配置独立于文字规划模型，不会因启动网页而加载。
