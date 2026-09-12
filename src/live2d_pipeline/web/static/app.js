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
  paused: "等待检查",
  failed: "需要处理",
  built: "模型已生成",
  accepted: "已接受",
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

function renderGallery() {
  const all = detail?.previews || [];
  const images = all.filter((image) => image.group === view);
  const current = images.find((image) => image.url === previewUrl) || images[0];
  previewUrl = current?.url || null;
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-selected", String(button.dataset.view === view));
  });
  $("main-preview").hidden = !current;
  $("empty-preview").hidden = !!current;
  $("open-preview").hidden = !current;
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
  const rev = detail?.revision;
  $("breadcrumb").textContent = selected || "选择项目";
  $("project-title").textContent = selected || "从一个想法开始";
  $("project-subtitle").textContent = rev
    ? viewedRevision && viewedRevision !== detail.head
      ? "正在查看历史版本。设为创作起点后，可从这里继续修改。"
      : "每次修改都保留一个版本，逐步完善你的角色。"
    : "描述角色，检查立绘，再让它动起来。";
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
          `<button class="history-item ${r.id === rev?.id ? "selected" : ""}" data-revision="${esc(r.id)}"><div class="history-meta"><span class="history-id">${esc(r.id.slice(0, 8))}${r.id === detail.head ? " · 当前" : ""}</span><span>${esc(statusNames[r.status])}${r.id === detail.accepted ? " ✓" : ""}</span></div><p>${esc(r.request)}</p></button>`,
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
      `<p>${esc(rev.plan.request)}</p>${rev.plan.clarification ? `<p class="job-error">${esc(rev.plan.clarification)}</p>` : ""}${rev.plan.unsupported.length ? `<p class="job-error">暂不支持：${esc(rev.plan.unsupported.join("；"))}</p>` : ""}<ul>${rev.plan.operations.map((op) => `<li>${esc(names[op.kind])} ${esc(op.target || op.semantic || "")} ${esc(op.instruction || op.color || "")}</li>`).join("")}</ul><details><summary>图像提示词</summary><p class="plan-prompt">${esc(rev.plan.prompt)}</p></details>`;
  }
  renderGallery();
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
  $("composer-title").textContent = unfinished
    ? "调整这份需求"
    : rev
      ? "下一步，想改变什么？"
      : "描述你的角色";
  $("composer-help").textContent = unfinished
    ? "完整描述这次需求，生成新方案。旧方案和中间产物会保留。"
    : rev
      ? "描述具体的变化，系统会在当前版本上创建修改分支。"
      : "发色、服装、表情和画风，都可以用中文描述。";
  $("example-prompt").textContent = unfinished ? "载入当前需求" : "填入示例";
  $("submit-prompt").disabled =
    !selected || !detail || busy() || historical || !$("prompt").value.trim();
  $("submit-prompt").innerHTML =
    `${pending ? "正在提交…" : unfinished ? "重新规划" : rev ? "提交修改" : "开始创作"}<span>↗</span>`;
  $("source").disabled = $("reference").disabled = !selected || busy();
  $("composer-limit").hidden = !historical;
  $("composer-limit").textContent =
    "正在查看历史版本，请先在版本记录中将它设为创作起点。";
  const imageOption = $("until").querySelector('[value="image"]');
  imageOption.disabled =
    !!rev && !source && !(unfinished && (!rev.parent || rev.source));
  if (imageOption.disabled && $("until").value === "image")
    $("until").value = "rig";
  $("revision-actions").hidden = !rev;
  $("resume").hidden = !unfinished;
  $("resume").disabled =
    busy() || !!rev?.plan.clarification || !!rev?.plan.unsupported.length;
  $("resume").textContent =
    rev?.status === "failed" ? "重试未完成阶段 →" : "继续生成完整模型 →";
  $("accept").disabled = busy() || !finished || rev?.status === "accepted";
  $("accept").textContent =
    rev?.status === "accepted" ? "已接受 ✓" : "接受版本";
  $("export").disabled = busy() || !finished;
  $("checkout").hidden = !historical;
  $("checkout").disabled = busy();
}

function renderJob() {
  currentJob =
    activeJob() || state.jobs.find((j) => j.project === selected) || null;
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
    currentJob.status === "cancelling" ? "正在停止并释放模型资源…" : phase;
  const elapsed = Math.max(
    0,
    Math.floor((currentJob.ended || Date.now() / 1000) - currentJob.created),
  );
  const duration =
    elapsed < 60
      ? `${elapsed} 秒`
      : `${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`;
  $("job-subtitle").textContent =
    `${currentJob.project} · ${statusNames[currentJob.status] || currentJob.status} · ${duration}${running ? " · 可以离开或刷新页面" : ""}`;
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
  $("download").hidden =
    !currentJob.download || currentJob.status !== "succeeded";
  if (currentJob.download) $("download").href = currentJob.download;
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
        !(detail?.previews || []).some((p) => p.group === view) &&
        detail?.previews.length
      )
        view = detail.previews[0].group;
      renderDetail();
    }
    renderProgress();
    renderControls();
    renderJob();
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
        source = reference = null;
        $("source").value = $("reference").value = "";
        $("source-status").textContent = "PNG / JPEG / PSD";
        $("reference-status").textContent = "用于约束生成后的外观";
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
  $("reference-status").textContent = "用于约束生成后的外观";
  $("prompt-count").textContent = "0 / 6000";
  $("until").value = "image";
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
  $("prompt").value =
    rev && !["built", "accepted"].includes(rev.status)
      ? rev.plan.request
      : rev
        ? "把前发改成浅蓝色，保留其他部件。"
        : "成年女性，银色长发、蓝眼、白色外套，正面半身二次元角色，open eyes, closed mouth, smile，纯白背景。";
  $("prompt").dispatchEvent(new Event("input"));
  $("prompt").focus();
});
$("prompt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitJob("plan", {
    request: $("prompt").value.trim(),
    until: $("until").value,
    source,
    reference,
  });
});
$("resume").addEventListener("click", () => submitJob("run", { until: "rig" }));
$("export").addEventListener("click", () => submitJob("export"));
$("accept").addEventListener("click", () => submitJob("accept"));
$("checkout").addEventListener("click", () => submitJob("checkout"));
$("cancel").addEventListener("click", async () => {
  if (!currentJob) return;
  try {
    await api(`/api/jobs/${currentJob.id}/cancel`, { method: "POST" });
    await refresh();
  } catch (error) {
    notice(error.message, true);
  }
});
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
