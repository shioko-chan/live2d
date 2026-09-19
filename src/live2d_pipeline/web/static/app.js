const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const statusNames = {
  empty: "尚未开始",
  planned: "方案待执行",
  running: "正在生成",
  paused: "阶段完成，已暂停",
  failed: "需要处理",
  built: "构建完成，待人工验收",
  accepted: "人工验收通过",
  queued: "等待执行",
  cancelling: "正在停止",
  cancelled: "已停止",
  interrupted: "上次任务中断",
  needs_input: "需要补充需求",
  succeeded: "任务完成",
};
const steps = [
  ["plan", "需求方案"],
  ["image", "角色立绘"],
  ["decompose", "图层拆分"],
  ["materials", "素材补全"],
  ["rig", "模型绑定"],
];
const activeStates = ["queued", "running", "cancelling"];
let state = { projects: [], jobs: [] },
  selected = localStorage.getItem("l2d-project"),
  viewedRevision = null;
let detail = null,
  view = "poses",
  previewUrl = null,
  source = null,
  reference = null;
let pending = false,
  uploading = 0,
  currentJob = null,
  requestEpoch = 0,
  noticeTimer = null;
let detailSignature = "",
  sidebarSignature = "",
  logsSignature = "";

async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.method && options.method !== "GET")
    headers["X-L2D-Request"] = "1";
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, {
    ...options,
    headers,
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

function notice(message, error = false) {
  clearTimeout(noticeTimer);
  $("notice").textContent = message;
  $("notice").className = "banner" + (error ? " error" : "");
  $("notice").hidden = false;
  if (!error)
    noticeTimer = setTimeout(() => {
      $("notice").hidden = true;
    }, 7000);
}

function activeJob() {
  return state.jobs.find((job) => activeStates.includes(job.status));
}
function busy() {
  return pending || uploading > 0 || !!activeJob();
}
function endpoint() {
  return `/api/projects/${encodeURIComponent(selected)}`;
}

function renderProjects() {
  const signature = JSON.stringify([selected, state.projects]);
  if (signature === sidebarSignature) return;
  sidebarSignature = signature;
  $("project-count").textContent = state.projects.length;
  $("project-list").innerHTML =
    state.projects
      .map(
        (p) =>
          `<button class="project-item ${p.name === selected ? "active" : ""}" data-project="${esc(p.name)}" ${p.name === selected ? 'aria-current="page"' : ""}><span class="project-avatar">${p.thumbnail ? `<img src="${esc(p.thumbnail)}" alt="" loading="lazy">` : "✦"}</span><span class="project-copy"><strong>${esc(p.name)}</strong><small>${esc(statusNames[p.status] || p.status)}</small></span></button>`,
      )
      .join("") || '<p class="help">还没有项目，创建你的第一个角色。</p>';
}

const stageNames = {image: "立绘就绪后暂停", materials: "图层素材就绪后暂停", rig: "完成模型构建"};
const actionNames = {plan: "生成需求方案并执行", run: "执行已有方案", refine: "生成修正草稿并复查", repair: "修正部件", review: "只检查模型", export: "打包模型", video: "生成预览视频", accept: "标记人工验收", checkout: "切换创作起点"};

function actionLabel(job) {
  return job.spec.action === "plan" && job.spec.until === "plan" ? "仅生成需求方案" : actionNames[job.spec.action];
}

function refinementHTML(changes) {
  if (!changes.length) return "";
  return `<h3>部件细化记录</h3><ul>${changes.map(c => {
    const before = c.previous_binding;
    const motion = c.binding.motion === "sway" ? `独立摆幅 ${before?.motion === "sway" ? before.degrees + "° → " : ""}${c.binding.degrees}°` : "固定跟随，不独立摆动";
    return `<li><strong>${esc(c.name)} · ${c.completed ? "已执行" : "计划中"}</strong><p>${c.action === "split" ? "从原图层拆为独立部件，并绑定动作" : "调整已有部件绑定"}；${esc(motion)}；${c.binding.toggle ? "允许隐藏" : "保持显示"}。</p><p>${esc(c.reason)}</p></li>`;
  }).join("")}</ul>`;
}

function humanize(text) {
  for (const [id, name] of Object.entries(detail?.part_names || {})) text = text.split(id).join(name);
  for (const preview of detail?.previews || []) if (preview.view) text = text.split(preview.view).join(preview.label);
  return text;
}

function renderGallery() {
  const all = detail?.previews || [];
  const images = all.filter((image) => image.group === view);
  const current = images.find((image) => image.url === previewUrl) || images[0];
  previewUrl = current?.url || null;
  $("edit-preview").hidden = !current?.part;
  $("edit-preview").onclick = () => window.dispatchEvent(new CustomEvent("l2d:repair-part", {detail: {part: current.part, mode: "mask"}}));
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-selected", String(button.dataset.view === view));
  });
  const isVideo = view === "video";
  $("preview-stage").hidden = isVideo;
  $("thumbnails").hidden = isVideo;
  $("preview-footer").hidden = isVideo;
  $("video-panel").hidden = !isVideo;
  if (!isVideo) $("motion-video").pause();
  $("main-preview").hidden = !current;
  $("empty-preview").hidden = !!current;
  $("open-preview").hidden = isVideo || !current;
  $("canvas-label").hidden = !current;
  if (current) {
    $("main-preview").src = current.url;
    $("main-preview").alt = current.label;
    $("open-preview").href = current.url;
    $("canvas-label").textContent = current.label;
  } else {
    $("main-preview").removeAttribute("src");
    $("empty-preview").querySelector("h2").textContent = !detail?.revision
      ? "你的角色，将在这里出现"
      : {
          poses: "绑定完成后，查看动作样例",
          video: "模型构建完成后，生成动作视频",
          art: "立绘生成后，查看画面效果",
          layers: "拆分完成后，检查独立图层",
        }[view];
    $("empty-preview").querySelector("p").textContent = !detail?.revision
      ? "新建项目并描述外观，或者上传已有立绘和分层 PSD。"
      : "可以先检查已有产物，再继续下一步。";
  }
  $("thumbnails").innerHTML =
    images
      .map(
        (image, index) =>
          `<button class="thumb ${image.url === previewUrl ? "active" : ""}" data-image="${index}" aria-label="${esc(image.label)}" aria-pressed="${image.url === previewUrl}"><img src="${esc(image.url)}" alt="" loading="lazy"><span>${esc(image.label)}</span></button>`,
      )
      .join("") || '<p class="help">这个阶段还没有预览图片。</p>';
  $("preview-count").textContent = images.length
    ? `${images.length} 张${{ poses: "姿态样例", art: "原画与素材", layers: "独立图层" }[view]}`
    : "等待生成预览";
}

function renderDetail() {
  const video = $("motion-video");
  const url = detail?.video || null;
  if (video.getAttribute("src") !== url) {
    video.pause();
    if (url) video.src = url;
    else video.removeAttribute("src");
    video.load();
  }
  video.hidden = !url;
  $("download-video").hidden = !url;
  if (url) $("download-video").href = url + "&download=1";
  const rev = detail?.revision;
  $("breadcrumb").textContent = selected || "选择项目";
  $("project-title").textContent = selected || "从一个想法开始";
  $("project-subtitle").textContent = rev
    ? viewedRevision && viewedRevision !== detail.head
      ? "正在查看历史版本。设为创作起点后，可从这里继续修改。"
      : "每次修改都保留一个版本，逐步完善你的角色。"
    : "描述角色，检查立绘，再让它动起来。";
  $("version-status").hidden = !rev;
  $("version-status").textContent = rev
    ? `构建：${["built", "accepted"].includes(rev.status) ? "已完成" : "未完成"} · 视觉检查：${detail.review ? "已运行，意见待核对" : "未运行"} · 人工验收：${rev.status === "accepted" ? "通过" : "未验收"} · 官方运行兼容性：${rev.runtime_verified ? "已验证" : "未验证"}` : "";
  $("revision-badge").hidden = !rev;
  if (rev) {
    $("revision-badge").textContent =
      `${statusNames[rev.status]} · ${rev.id.slice(0, 6)}`;
    $("revision-badge").className =
      "badge" + (rev.status === "failed" ? " failed" : "");
  }
  $("history-count").textContent = detail?.history.length || 0;
  $("history-list").innerHTML =
    (detail?.history || [])
      .map(
        (r) =>
          `<button class="history-item ${r.id === rev?.id ? "selected" : ""}" data-revision="${esc(r.id)}"><div class="history-meta"><span class="history-id">${esc(r.id.slice(0, 8))}${r.id === detail.head ? " · 创作起点" : ""}</span><span>${esc(statusNames[r.status])}${r.id === detail.accepted ? " · 当前验收选定" : ""}</span></div><p>${esc(r.request)}</p></button>`,
      )
      .join("") || '<p class="help">生成第一份方案后，版本会显示在这里。</p>';
  $("plan-details").hidden = !rev;
  if (rev) {
    const names = {
      recolor: "改色",
      repaint: "局部重绘",
      add: "补充图层",
      remove: "移除图层",
      rig: "调整绑定",
      regenerate: "重新生成",
    };
    $("plan-content").innerHTML =
      `<p>${esc(rev.plan.request)}</p>${rev.plan.clarification ? `<p class="job-error">${esc(rev.plan.clarification)}</p>` : ""}${rev.plan.unsupported.length ? `<p class="job-error">暂不支持：${esc(rev.plan.unsupported.join("；"))}</p>` : ""}<ul>${rev.plan.operations.map((op) => `<li>${esc(names[op.kind])} ${esc(detail.part_names?.[op.target] || op.target || op.semantic || "")} ${esc(op.instruction || op.color || "")}</li>`).join("")}</ul><details><summary>图像提示词</summary><p class="plan-prompt">${esc(rev.plan.prompt)}</p></details>`;
  }
  if (rev) {
    $("plan-content").innerHTML += refinementHTML(detail.refinement_changes || []);
    if (!rev.plan.operations.length && !rev.plan.refinements.length)
      $("plan-content").innerHTML += `<p>${rev.source ? "直接使用导入原稿制作。" : rev.parent ? "本方案没有额外修改操作。" : "根据需求生成立绘，再按选择的阶段执行。"}</p>`;
  }
  const report = detail?.review;
  $("review-content").innerHTML = report
    ? `<p>已检查 ${report.reviewed_views.length} 个静态姿态；视觉意见待人工核对，尚未确认问题已解决。</p>
       ${refinementHTML(detail.refinement_changes || [])}
       <h3>本次复查仍提出的意见（未自动修改）</h3>
       <ul>${report.findings.map((f, index) => {
         const pose = detail.previews.find(p => p.view === f.view);
         return `<li><strong>${esc({error: "疑似明显问题", warning: "待核对", info: "提示"}[f.severity] || "待核对")} · ${esc(detail.part_names?.[f.part] || "部件")}</strong>
           ${pose ? `<button type="button" class="text-link" data-review-view="${esc(f.view)}">查看：${esc(pose.label)}</button>` : "对应姿态图片不可用"}
           <p>${esc(humanize(f.issue))}</p><p>${esc(humanize(f.suggestion))}</p><button class="text-link" data-repair-finding="${index}">修正这个问题</button></li>`;
       }).join("") || "<li>本次未报告明显问题，仍需你检查画面。</li>"}</ul>
       <details><summary>检查说明与不确定之处</summary><p>${esc(humanize(report.summaries.join(" ")))}</p><p>${esc(humanize([...new Set(report.uncertainties)].join("；")))}</p></details>
       <p>静态姿态检查不包含物理时间响应；验收不会自动修复这些意见。</p>`
    : "尚未运行视觉检查。选择“只检查，不修改”可生成检查报告。";
  if (detail?.rig_replay?.needs_review?.length)
    $("review-content").innerHTML += `<h3>素材变化后需要重新检查的旧修正</h3><ul>${detail.rig_replay.needs_review.map(r => `<li>${esc(r.operation.instruction || r.operation.tool)}：${esc(r.reason)}</li>`).join("")}</ul>`;
  renderGallery();
  document.querySelectorAll("[data-repair-finding]").forEach(button => button.onclick = () => {
    const finding = report.findings[Number(button.dataset.repairFinding)];
    const pose = detail.previews.find(p => p.view === finding.view)?.parameters || {};
    window.dispatchEvent(new CustomEvent("l2d:repair-part", {detail: {part: finding.part, mode: finding.repair === "reduce_sway" ? "bind" : "rig", request: finding.issue, pose}}));
  });
}

function renderProgress() {
  const rev = detail?.revision;
  const job = state.jobs.find(
    (j) => j.project === selected && activeStates.includes(j.status),
  );
  $("pipeline").innerHTML = steps
    .map(([key, label], index) => {
      let status =
        key === "plan" && rev ? "complete" : rev?.stages[key]?.state || "";
      if (key === "plan" && job?.phase === "解析需求") status = "running";
      if (
        status === "running" &&
        (!job || job.status === "cancelling") &&
        rev?.status !== "running"
      )
        status = "failed";
      if (!status && rev && ["built", "accepted"].includes(rev.status))
        status = "complete";
      return `<li class="${status}"><span class="step-dot">${status === "complete" ? "✓" : status === "failed" ? "!" : index + 1}</span><span>${label}</span></li>`;
    })
    .join("");
}

function renderControls() {
  const rev = detail?.revision;
  const historical = rev && rev.id !== detail.head;
  const finished = rev && ["built", "accepted"].includes(rev.status);
  const unfinished = rev && !finished;
  for (const option of $("operation-mode").options)
    option.disabled = option.value !== "plan" && !finished;
  if (!finished) $("operation-mode").value = "plan";
  const mode = $("operation-mode").value;
  const editing = mode !== "review";
  const reuse = unfinished && $("reuse-inputs").checked;
  const effectiveSource = source || (reuse ? rev.source : null);
  const effectiveReference = reference || (reuse ? rev.reference : null);
  const hasBase = finished || (unfinished && rev.parent);
  const referenceBlocked = !!effectiveSource || !!hasBase;
  const inputConflict = mode === "plan" && effectiveReference && referenceBlocked;
  $("composer-title").textContent = mode === "refine" ? "细化部件与动作" : mode === "review" ? "检查这个版本" : unfinished ? "重新整理需求方案" : rev ? "修改角色外观" : "描述你的角色";
  $("composer-help").textContent = mode === "plan"
    ? unfinished ? "修改需求会创建新方案；未导入原稿时可能重新生成立绘。要保留已生成的图片继续制作，请使用下方“执行已有方案”。" : "直接用中文描述，不需要编写生图标签。"
    : "用日常语言描述希望改善或检查的地方。";
  $("operation-help").textContent = {
    plan: "用于生成角色、改色、重绘等外观修改；部件拆分和动作细化请选择“细化部件与动作”。",
    refine: "会拆分或调整已有部件、重建绑定并复查，必要时进行一轮摆幅修正。生成新版本，最后由你验收。",
    review: "只运行规则和视觉检查，不修改模型。可留空检查全部，也可描述重点。",
  }[mode];
  $("prompt").placeholder = mode === "refine" ? "例如：把领结拆成独立部件，让尾部轻微摆动，保持原来的画风。" : mode === "review" ? "可选：例如转头时头发是否穿帮，闭眼时是否有虹膜残留。" : unfinished ? "完整描述新的需求。例如：成年女性，银色长发、蓝眼、白色外套，正面半身，睁眼微笑。" : rev ? "例如：把前发改成浅蓝色，保留其他部件。" : "例如：成年女性，银色长发、蓝眼、白色外套，正面半身，睁眼、闭嘴微笑。";
  $("example-prompt").textContent = unfinished ? "载入当前需求" : "填入示例";
  $("submit-prompt").disabled = !selected || !detail || busy() || (historical && editing) || (editing && !$("prompt").value.trim()) || !!inputConflict;
  const label = mode === "review" ? "运行检查，不修改模型" : mode === "refine" ? "生成修正草稿并复查" : unfinished ? "生成新方案" : rev ? "提交外观修改" : "开始创作";
  $("submit-prompt").textContent = pending ? "正在提交…" : label;
  $("plan-options").hidden = mode !== "plan";
  $("reuse-inputs-field").hidden = !unfinished || !(rev.source || rev.reference);
  $("reuse-inputs").disabled = busy() || !!source || !!reference;
  $("saved-inputs").textContent = rev ? [rev.source && `原稿：${rev.source}`, rev.reference && `参考图：${rev.reference}`].filter(Boolean).join("；") : "";
  $("source").disabled = !selected || busy() || historical || !!effectiveReference;
  $("reference").disabled = !selected || busy() || historical || referenceBlocked;
  $("clear-source").hidden = !source;
  $("clear-reference").hidden = !reference;
  $("clear-source").disabled = $("clear-reference").disabled = busy();
  $("reference-help").textContent = inputConflict ? "沿用的参考图不适用于此次操作。取消“沿用当前方案的图片输入”或移除新上传的参考图后再提交。" : effectiveSource ? "本次直接使用原稿，参考图不可用。导入 PSD 时直接读取现有图层。" : hasBase ? "已有模型的普通修改不使用参考图；参考图上传仅在新角色生成时开放。" : effectiveReference ? "本次使用角色参考图约束新立绘，不能同时导入制作原稿。" : "参考图用于新角色的外观生成；不会作为原稿直接拆层。";
  $("composer-limit").hidden = !historical;
  $("composer-limit").textContent = "正在查看历史版本。可检查、验收和导出；修改前请在版本记录中将它设为创作起点。";
  const imageOption = $("until").querySelector('[value="image"]');
  imageOption.disabled = (hasBase && !effectiveSource) || (effectiveSource || "").endsWith(".psd");
  if (imageOption.disabled && $("until").value === "image") $("until").value = "materials";
  $("plan-stage-help").textContent = $("until").value === "plan" ? "只把需求整理成操作方案，不生成立绘或模型；之后可分阶段执行。" : imageOption.disabled ? "已有模型的修改、分层 PSD 从素材阶段处理，可在绑定前暂停检查。" : "在所选阶段暂停；模型构建完成后仍需检查和人工验收。";
  $("revision-actions").hidden = !rev;
  $("resume-panel").hidden = !unfinished;
  const targets = rev?.resume_targets || [];
  const previous = $("resume-until").value;
  const options = targets.map(t => `<option value="${t}">${stageNames[t]}</option>`).join("");
  if ($("resume-until").innerHTML !== options) {
    $("resume-until").innerHTML = options;
    if (targets.includes(previous)) $("resume-until").value = previous;
  }
  $("resume").disabled = busy() || !targets.length;
  $("resume-until").disabled = busy() || !targets.length;
  $("resume").textContent = `执行已有方案 → ${stageNames[$("resume-until").value] || "请先解决方案问题"}`;
  $("resume-help").textContent = targets.length ? "已完成阶段会复用，不会重新解析上方输入。" : "方案需要补充需求或处理错误后才能执行。";
  $("accept").disabled = busy() || !finished || rev?.status === "accepted";
  $("accept").textContent = rev?.status === "accepted" ? "已人工验收通过 ✓" : "标记人工验收通过";
  $("export").disabled = busy() || !finished;
  $("generate-video").disabled = busy() || !finished;
  $("generate-video").textContent = detail?.video ? "重新生成预览视频" : "生成预览视频";
  $("video-help").textContent = !finished ? "先完成模型构建，才能生成动作视频。" : detail?.video ? "这是当前查看版本的预览视频，可播放或下载。" : "点击按钮为当前查看版本生成动作视频。";
  $("quality-panel").hidden = !finished;
  $("checkout").hidden = !historical;
  $("checkout").disabled = busy();
  const active = activeJob();
  $("global-task").hidden = !active;
  $("stop-active-task").disabled = pending || active?.status === "cancelling";
  $("global-task-text").textContent = active ? `正在执行：${active.project} · ${actionLabel(active)} · ${active.phase}。执行期间暂不能提交其他任务。` : "";
}

function renderJob() {
  currentJob =
    state.jobs.find(j => j.project === selected && j.revision === detail?.revision?.id) || null;
  const exported = state.jobs.find(j => j.project === selected && j.revision === detail?.revision?.id && j.spec.action === "export" && j.status === "succeeded" && j.download);
  $("download").hidden = !exported;
  if (exported) {
    $("download").href = exported.download;
    $("download").textContent = `下载此版本模型包 · ${exported.revision.slice(0, 8)}`;
  }
  $("job-panel").hidden = !currentJob;
  if (!currentJob) return;
  const running = activeStates.includes(currentJob.status);
  const stage =
    currentJob.project === selected && currentJob.revision === detail?.revision?.id
      ? Object.entries(detail?.revision?.stages || {}).find(
          ([, s]) => s.state === "running",
        )
      : null;
  const phase =
    running && stage && currentJob.phase === "执行流水线"
      ? `${steps.find(([key]) => key === stage[0])?.[1] || stage[0]}进行中`
      : currentJob.phase;
  $("job-title").textContent =
    `${actionLabel(currentJob)} · ${currentJob.status === "cancelling" ? "正在停止…" : phase}`;
  const elapsed = Math.max(
    0,
    Math.floor((currentJob.ended || Date.now() / 1000) - currentJob.created),
  );
  const duration =
    elapsed < 60
      ? `${elapsed} 秒`
      : `${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`;
  $("job-subtitle").textContent =
    `${currentJob.project} · 版本 ${currentJob.revision?.slice(0, 8) || "尚未建立"} · ${statusNames[currentJob.status] || currentJob.status} · ${duration}${running ? " · 可以离开或刷新页面" : ""}`;
  $("job-icon").textContent = running
    ? "◷"
    : currentJob.status === "succeeded"
      ? "✓"
      : "!";
  $("job-icon").className = "job-icon" + (running ? " working" : "");
  $("cancel").hidden = !running;
  $("cancel").disabled = currentJob.status === "cancelling" || pending;
  $("job-error").hidden = !currentJob.error;
  $("job-error").textContent = currentJob.error || "";
}

async function refresh() {
  const epoch = requestEpoch;
  try {
    const nextState = await api("/api/state");
    if (epoch !== requestEpoch) return;
    state = nextState;
    if (!state.projects.some((p) => p.name === selected)) {
      selected = state.projects[0]?.name || null;
      viewedRevision = null;
    }
    const nextDetail = selected
      ? await api(
          endpoint() +
            (viewedRevision
              ? `?revision=${encodeURIComponent(viewedRevision)}`
              : ""),
        )
      : null;
    if (epoch !== requestEpoch) return;
    detail = nextDetail;
    renderProjects();
    const signature = JSON.stringify(detail);
    if (signature !== detailSignature) {
      detailSignature = signature;
      if (
        view !== "video" && !(detail?.previews || []).some((p) => p.group === view) &&
        detail?.previews.length
      )
        view = detail.previews[0].group;
      renderDetail();
    }
    renderProgress();
    renderControls();
    renderJob();
    window.dispatchEvent(new CustomEvent("l2d:state", {detail: {project: selected, detail, state}}));
    $("connection").className = "connection";
    $("connection").innerHTML = "<i></i> 已连接";
    $("connection-error").hidden = true;
    if (state.warnings.length) notice(state.warnings.join("；"), true);
    await refreshLogs();
  } catch (error) {
    $("connection").className = "connection offline";
    $("connection").innerHTML = "<i></i> 连接中断";
    $("connection-error").textContent =
      `无法读取工作台状态：${error.message}。保留此页面，连接恢复后会自动更新。`;
    $("connection-error").hidden = false;
  }
}

async function refreshLogs() {
  if (!$("logs-details").open || !currentJob) return;
  const jobId = currentJob.id;
  const logs = await api(`/api/jobs/${jobId}/logs`);
  if (currentJob?.id !== jobId) return;
  const signature = JSON.stringify(logs);
  if (signature === logsSignature) return;
  logsSignature = signature;
  $("logs-content").innerHTML =
    logs
      .map((log) => `<h3>${esc(log.name)}</h3><pre>${esc(log.text)}</pre>`)
      .join("") || '<p class="help">任务尚未产生日志。</p>';
}

async function submitJob(action, extra = {}) {
  if (!selected || !detail || busy()) return;
  const submittedProject = selected;
  pending = true;
  renderControls();
  try {
    await api(endpoint() + "/jobs", {
      method: "POST",
      body: {
        action,
        expected_head: detail.head,
        revision: detail.revision?.id || null,
        ...extra,
      },
    });
    if (selected === submittedProject) {
      if (action === "plan" || action === "checkout") viewedRevision = null;
      if (action === "plan") {
        $("reuse-inputs").checked = true;
        source = reference = null;
        $("source").value = $("reference").value = "";
        $("source-status").textContent = "PNG / JPEG / PSD";
        $("reference-status").textContent = "仅新角色生成使用，保留参考角色特征";
      }
    }
    notice(
      action === "export"
        ? "正在校验并打包，完成后可在下方下载。"
        : "任务已提交，进度会自动更新。",
    );
    await refresh();
  } catch (error) {
    notice(error.message, true);
  } finally {
    pending = false;
    renderControls();
  }
}

async function selectProject(name) {
  requestEpoch++;
  selected = name;
  viewedRevision = null;
  detail = null;
  detailSignature = "";
  previewUrl = null;
  source = reference = null;
  $("source").value = $("reference").value = $("prompt").value = "";
  $("source-status").textContent = "PNG / JPEG / PSD";
  $("reference-status").textContent = "仅新角色生成使用，保留参考角色特征";
  $("prompt-count").textContent = "0 / 6000";
  $("until").value = "image";
  $("operation-mode").value = "plan";
  $("reuse-inputs").checked = true;
  $("notice").hidden = true;
  localStorage.setItem("l2d-project", name);
  renderProjects();
  renderDetail();
  renderControls();
  await refresh();
}

$("project-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-project]");
  if (button) selectProject(button.dataset.project);
});
document.querySelectorAll("[data-view]").forEach((button) =>
  button.addEventListener("click", () => {
    view = button.dataset.view;
    previewUrl = null;
    renderGallery();
  }),
);
document.querySelectorAll("[data-panel]").forEach((button) =>
  button.addEventListener("click", () => {
    const create = button.dataset.panel === "create";
    $("create-panel").hidden = !create;
    $("history-panel").hidden = create;
    document.querySelectorAll("[data-panel]").forEach((tab) => {
      tab.classList.toggle("active", tab === button);
      tab.setAttribute("aria-selected", String(tab === button));
    });
  }),
);
$("thumbnails").addEventListener("click", (event) => {
  const button = event.target.closest("[data-image]");
  if (button) {
    previewUrl = detail.previews.filter((image) => image.group === view)[
      Number(button.dataset.image)
    ].url;
    renderGallery();
  }
});
$("history-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-revision]");
  if (button) {
    requestEpoch++;
    viewedRevision = button.dataset.revision;
    previewUrl = null;
    await refresh();
  }
});
$("prompt").addEventListener("input", () => {
  $("prompt-count").textContent = `${$("prompt").value.length} / 6000`;
  renderControls();
});
$("example-prompt").addEventListener("click", () => {
  const rev = detail?.revision;
  $("prompt").value = $("operation-mode").value === "refine" ? "把领结拆成独立部件，让尾部轻微摆动，保持原来的画风。" : $("operation-mode").value === "review" ? "检查转头和闭眼时是否有穿帮。" :
    rev && !["built", "accepted"].includes(rev.status)
      ? rev.plan.request
      : rev
        ? "把前发改成浅蓝色，保留其他部件。"
        : "成年女性，银色长发、蓝眼、白色外套，正面半身二次元角色，睁眼、闭嘴微笑，纯白背景。";
  $("prompt").dispatchEvent(new Event("input"));
  $("prompt").focus();
});
$("prompt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const mode = $("operation-mode").value;
  submitJob(mode, mode === "plan" ? {
    request: $("prompt").value.trim(),
    until: $("until").value,
    source,
    reference,
    reuse_inputs: $("reuse-inputs").checked,
  } : { request: $("prompt").value.trim() });
});
$("resume").addEventListener("click", () => submitJob("run", { until: $("resume-until").value }));
for (const id of ["operation-mode", "until", "resume-until", "reuse-inputs"]) $(id).addEventListener("change", renderControls);
$("generate-video").addEventListener("click", () => submitJob("video"));
$("export").addEventListener("click", () => submitJob("export"));
$("accept").addEventListener("click", () => submitJob("accept"));
$("checkout").addEventListener("click", () => submitJob("checkout"));
async function cancelJob(job) {
  if (!job) return;
  try {
    await api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
    await refresh();
  } catch (error) {
    notice(error.message, true);
  }
}
$("cancel").addEventListener("click", () => cancelJob(currentJob));
$("stop-active-task").addEventListener("click", () => cancelJob(activeJob()));
$("logs-details").addEventListener("toggle", () =>
  refreshLogs().catch((error) => notice(error.message, true)),
);
for (const key of ["source", "reference"]) {
  $(key).addEventListener("change", async () => {
    const file = $(key).files[0],
      epoch = requestEpoch;
    if (key === "source") source = null;
    else reference = null;
    if (!file) {
      $(key + "-status").textContent = key === "source" ? "PNG / JPEG / PSD" : "未选择参考图";
      renderControls();
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      $(key).value = "";
      notice("文件不能超过 500 MB。", true);
      return;
    }
    uploading++;
    renderControls();
    $(key + "-status").textContent = "正在上传…";
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api("/api/uploads", { method: "POST", body: form });
      if (epoch !== requestEpoch) return;
      $("reuse-inputs").checked = false;
      if (key === "source") source = result.token;
      else reference = result.token;
      $(key + "-status").textContent = `${result.name} · 已上传`;
    } catch (error) {
      if (epoch === requestEpoch) {
        $(key + "-status").textContent = "上传失败，请重新选择";
        notice(error.message, true);
      }
    } finally {
      uploading--;
      renderControls();
    }
  });
}
$("review-content").addEventListener("click", event => {
  const button = event.target.closest("[data-review-view]");
  if (!button) return;
  const preview = detail.previews.find(p => p.view === button.dataset.reviewView);
  if (preview) {
    view = "poses";
    previewUrl = preview.url;
    renderGallery();
    $("preview-stage").scrollIntoView({block: "center"});
  }
});
for (const key of ["source", "reference"]) $("clear-" + key).addEventListener("click", () => {
  if (key === "source") source = null;
  else reference = null;
  $(key).value = "";
  $(key + "-status").textContent = key === "source" ? "PNG / JPEG / PSD" : "未选择参考图";
  renderControls();
});
$("new-project").addEventListener("click", () => {
  $("new-project-dialog").showModal();
  $("create-error").hidden = true;
  $("project-name").focus();
});
$("close-dialog").addEventListener("click", () =>
  $("new-project-dialog").close(),
);
$("new-project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const result = await api("/api/projects", {
      method: "POST",
      body: { name: $("project-name").value.trim() },
    });
    $("new-project-dialog").close();
    $("project-name").value = "";
    await selectProject(result.name);
    document.querySelector('[data-panel="create"]').click();
    $("prompt").focus();
  } catch (error) {
    $("create-error").textContent = error.message;
    $("create-error").hidden = false;
  } finally {
    button.disabled = false;
  }
});

renderDetail();
renderProgress();
renderControls();
await refresh();
async function poll() {
  await refresh();
  setTimeout(poll, 2000);
}
setTimeout(poll, 2000);
