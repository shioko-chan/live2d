# 2026-10-23 Core 固定依赖与实际制作验证

## 固定依赖

flake.nix 定义 cubism-core 包，SDK 5-r.5，固定下载地址与 sha256-f/Oku8GcCocollqlIqt36xGyUpFkU+aKinjTtxGIuxI=。包加入 devShell packages，并导出 CUBISM_CORE_LIBRARY。nixpkgs 沿用 flake.lock；未更新 lock。不引用个人 nixos-config 路径，也未把现有 store 路径当作源。

.envrc 继续 use flake。通过 nix develop --profile .direnv/live2d-devshell 创建本地 profile，并确认 Core 输出在其依赖闭包中。profile 存在期间保留闭包；移除后包仍可由固定定义重建。初次离线缺少 unzip，启用 Nix 缓存获取后构建成功。旧手动 Core 路径检查时已不存在。

验证：不手动指定环境变量或 --core 的公开 CLI，对旧交付的 18 秒/325 采样点检查通过。新包加载返回版本整数 100663297；库内容 hash 与前次相同。runtime_check 和 performance 测试共 58 passed。skill 校验与 git diff --check 通过。

## 制作实验（区域验证，不是从零生成评测）

基线：Mio 358614b7f88146f5；固定原画、材料、画布、头部姿态和驱动输入。制作前记录目标/范围/最多两轮预算，检查真实眼眉层级和全部关键形轴。通过正常计划、版本、库、导出和 Core 路线执行；候选 checkout=false，旧 head 和交付包保留。

资料目录：projects/mio-starlibrary-20261015/production/skill-validation-20261023/。

第一轮 f6417e4ddff749dc：在眼形父 Warp 的九个转头组合分别制作新轴；眉毛在原升降/情绪组合上制作局部曲率。检查旧新同尺度的中性、笑、笑+闭眼、笑+开口、担忧、认真、转头+半眨眼+开口。没有生成新原画，不将其计为重新拆层验证。

已观察结果：
- 中性 RGBA 逐像素差异为空。
- 担忧/认真眉头、眉尾区别更清晰；仍受原画对比度和刘海限制。
- 第一轮笑眼更像困倦的平眼形，视觉验收不通过。不能因为参数有效或没有破面就称为改善。
- 同一输入：旧 smile 使 t=1 嘴开合 .65 被覆盖为0、t=1.25 眼闭合0被覆盖为1。新表情保留 .65 和0，驱动检查通过。
- 第一轮最终导出经固定 Core 检查73采样点通过；3秒/72帧实际CPU视频生成。

第二轮 eeab75a07433487c 从基线眼 Warp 格点重写，固定眼角，让上下睑中央共同上拱、下睑抬升更强。已查看完整新旧对照：第一轮明显平眼/困倦感有所改善，但笑眼识别度仍不足，标为 weak_after_two_rounds，不升级正式 head。中性像素仍一致、组合图未见明显虹膜漏出；这不能代替目标应用检查。眉形区分改善，嘴部原画没有变化。

实验发现回写到 facial-production.md：共享眼部父 Warp 会连带改变虹膜、上睑和闭眼轮廓；修复要对照基线，超出预算后应规划独立眼睑/虹膜控制，不以眼变窄替代笑眼验收。detailed-production.md 增加实际区域对照验证程序。

第二轮导出模型/源文件/视频位于 deliveries/mio-skill-validation-20261023-round2；production/skill-validation-20261023/round2/ 保留对照图、驱动轨迹、Core 报告和判断。该交付是实验候选，旧正式 ZIP 和项目 head 未修改。

未覆盖：新角色图像生成、嘴内/眼睑独立素材重新拆层、侧发多段、转头立体感、目标应用和 SDK 物理。实际增量制作与局部改善不能证明 skill 已具备普遍成品质量保证。

补充：`--inputs-from .` 让 Nix 辅助 nixpkgs 查找沿用项目锁定输入。第二轮比较与最终检查使用它进入离线环境，无之前查询全局 registry 交互 bash 的构建警告；已更新 skill 的推荐命令。

最终检查：第二轮动态轨迹加上7个实际渲染姿态，共80采样点通过官方 Core。单独检查表情 end=2 的释放轨迹，t=2.5 EyeSmile 和 MouthForm 回到0；说话与闭眼驱动仍保留。基线文件哈希与 head 再次确认不变。最终 skill 格式检查与 diff 空白检查通过。
