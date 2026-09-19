// Repair drafts use source pixels; viewport transforms never enter saved annotations.
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const modes = {
  split: "提取独立部件",
  mask: "修正图层掩膜",
  replace: "替换素材",
  add: "补充缺失部件",
  repaint: "局部重绘",
  bind: "调整连接与摆动",
  rig: "修正指定姿态",
};
const semantics = [
  "face",
  "facedetail",
  "mouth",
  "mouth_open",
  "mouth_close",
  "eye_close",
  "eyewear",
  "ears",
  "nose",
  "tooth-t",
  "tooth-b",
  "tongue",
  "neck",
  "irides",
  "eyewhite",
  "eyelash",
  "eyebrow",
  "front hair",
  "back hair",
  "headwear",
  "earwear",
  "neckwear",
  "topwear",
  "bottomwear",
  "handwear",
  "legwear",
  "footwear",
  "tail",
  "wings",
  "objects",
];
let project = null,
  detail = null,
  state = null,
  draft = null,
  dirty = false,
  pending = false,
  epoch = 0,
  history = [],
  future = [];
let source = null,
  mask = null,
  zoom = 1,
  pan = [0, 0],
  gesture = null,
  tool = "keep",
  signature = "",
  displayed = "";
let selectedMode = "split";
let maskLayer = "extraction";
const activeMask = () =>
  maskLayer === "cleanup" ? draft.edit.cleanup.mask : draft.edit.mask;
const endpoint = () => `/api/projects/${encodeURIComponent(project)}`;
async function api(path, options = {}) {
  const headers = { "X-L2D-Request": "1" };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, {
    ...options,
    headers,
    cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "操作失败");
  return data;
}
function message(text, bad = false) {
  $("repair-message").textContent = text;
  $("repair-message").className = bad ? "job-error" : "help";
}
function busy() {
  return (
    pending ||
    state?.jobs.some((j) =>
      ["running", "queued", "cancelling"].includes(j.status),
    )
  );
}
function shell() {
  $("repair-workspace").innerHTML =
    `<div class="repair-heading"><div><h2>部件修正工作区</h2><p class="help">圈出问题、修改掩膜或替换素材。草稿与模型版本分开保存，应用后才产生新版本。</p></div></div>
 <div class="repair-start"><label>部件<select id="repair-part"></select></label><label>修正类型<select id="repair-mode">${Object.entries(
   modes,
 )
   .map(([v, n]) => `<option value="${v}">${n}</option>`)
   .join(
     "",
   )}</select></label><label id="repair-upload-label" hidden>导入部件<input id="repair-upload" type="file" accept=".png,.jpg,.jpeg"></label><button id="repair-create" class="button secondary">创建修正草稿</button></div>
 <div class="repair-start"><label>已保存的草稿<select id="repair-drafts"><option value="">选择草稿</option></select></label><button id="repair-discard" class="text-link" disabled>丢弃草稿</button></div>
 <p id="repair-message" class="help" role="status">选择部件后开始。缺少素材时可以补充新部件。</p>
 <div id="repair-editor" hidden><div class="repair-layout"><div>
 <label id="repair-mask-layer-field">正在编辑的范围<select id="repair-mask-layer"><option value="extraction">提取范围：保留为独立部件</option><option value="cleanup">底层清理范围：移除残影并补全</option></select></label>
 <div id="repair-tools" class="repair-tools"></div>
 <div class="repair-tools"><label>笔刷 / 影响半径 <input id="repair-radius" type="range" min="1" max="80" value="5"></label><button id="repair-undo">撤销</button><button id="repair-redo">重做</button><button id="repair-fit">适应画布</button><label><input type="checkbox" id="repair-overlay" checked>显示掩膜</label></div>
 <div class="repair-canvas-wrap"><canvas id="repair-canvas" aria-label="部件标注画布"></canvas></div>
 <p id="repair-canvas-help" class="help">滚轮缩放；平移工具拖动画布。绿色为保留点，红色为排除点；画笔和橡皮擦会直接修正掩膜。</p>
 <div id="repair-candidates" class="repair-candidates"></div>
 </div><div class="repair-fields">
 <label>部件名称<input id="repair-name" maxlength="80"></label>
 <label>想修正什么<textarea id="repair-request" rows="3" placeholder="例如：两条飘带都要保留，不要包含白色衬衣"></textarea></label>
 <div id="repair-category"><label>部件类别<select id="repair-semantic">${semantics.map((s) => `<option value="${s}">${{ mouth_open: "开口口型", mouth_close: "闭口口型", earwear: "耳饰", headwear: "发饰", neckwear: "领结／颈饰", objects: "其他装饰" }[s] || s}</option>`).join("")}</select></label><label>角色自身侧别<select id="repair-side"><option value="none">不分左右</option><option value="left">左侧</option><option value="right">右侧</option></select></label></div>
 <div id="repair-placement"><label>画布位置与尺寸（X、Y、宽、高）<div class="repair-four">${["x", "y", "w", "h"].map((v) => `<input id="repair-${v}" type="number" min="0" aria-label="${v}">`).join("")}</div></label></div>
 <div id="repair-cleanup-fields"><h3>底层清理</h3><label>清理范围向外扩展（像素）<input id="repair-cleanup-expansion" type="number" min="0" max="64" step="1" value="0"></label><label>补全方式<select id="repair-cleanup-backend"><option value="lama">LaMa 遮挡补全</option><option value="comfy">局部语义重绘</option></select></label><label>底层应该是什么<textarea id="repair-cleanup-prompt" rows="2" maxlength="2000" placeholder="例如：移除领结，补全连续的白色衬衣与自然褶皱"></textarea></label><label><input id="repair-cleanup-visible" type="checkbox">允许重绘原画中可见的区域</label><p class="help">默认仅修改被提取部件遮住的底层，保持默认姿态合成一致。开启后可清理外沿残影，也会改变原画可见内容。橙色显示清理范围，保存后刷新扩展范围。</p></div>
 <label id="repair-inpaint-field">模型参考上下文扩展（像素）<input id="repair-inpaint-padding" type="number" min="0" max="32" step="1" value="4"><span class="help">仅增加模型参考范围，不扩大写回区域。</span></label>
 <div id="repair-binding"><label>独立运动<select id="repair-motion"><option value="fixed">固定，跟随原有头部或身体</option><option value="sway">独立摆动</option></select></label><label>摆动角度<input id="repair-degrees" type="number" min="0.1" max="12" step="0.1"></label><div class="repair-four">${["root-x", "root-y", "tip-x", "tip-y"].map((v) => `<label>${v.replace("root", "根部").replace("tip", "末端")}<input id="repair-${v}" type="number" min="0" max="1" step="0.01"></label>`).join("")}</div><label>固定根部比例<input id="repair-pin" type="number" min="0" max="0.95" step="0.05"></label><label>末端柔软程度<input id="repair-softness" type="number" min="0" max="8" step="0.1"></label></div>
 <div id="repair-rig-fields" hidden><label>绑定对象<select id="repair-target"></select></label><label>参考姿态<select id="repair-pose"><option value="">默认姿态</option></select></label><div class="repair-start"><label>参数<select id="repair-param"></select></label><label>值<input id="repair-param-value" type="number" step="0.1"></label><button id="repair-set-param">设置姿态</button></div><label>修正动作<select id="repair-rig-action"><option value="landmarks">局部拖动 / 固定连接点</option><option value="smooth">平滑独立变形器</option><option value="opacity">当前姿态显隐</option></select></label><label>透明度<input id="repair-opacity" type="number" min="0" max="1" step="0.1" value="0"></label><label>影响范围<input id="repair-influence" type="number" min="0.01" max="1" step="0.01" value="0.2"></label><button id="repair-rig-context" class="button secondary">载入当前姿态</button></div>
 <div id="repair-checks"></div>
 <label><input id="repair-visual-accepted" type="checkbox">我已查看视觉检查的提示，确认此候选可应用</label>
 <details class="repair-search-settings"><summary>自动尝试设置</summary><label>最多候选数<input id="repair-candidate-limit" type="number" min="1" max="3" step="1" value="3"></label><label>搜索预算（秒）<input id="repair-candidate-timeout" type="number" min="60" max="1800" step="1" value="600"></label><p class="help">按顺序尝试少量修正并复查，保留独立草稿。达到预算后不启动新候选，正在执行的步骤可能超时。已完成候选可继续查看，不会自动应用到模型。</p></details>
 <div class="repair-actions"><button id="repair-save" class="button secondary">保存草稿</button><button data-step="segment" class="button secondary">重新分割</button><button data-step="underpaint" class="button secondary">只重做遮挡补全</button><button data-step="suggest" class="button secondary">分析问题</button><button data-step="inspect" class="button secondary">检查完整性</button><button data-step="preview" class="button primary">预览修正</button><button data-step="search" class="button secondary">自动尝试并比较候选</button><button data-step="binding_preview" class="button secondary">预览绑定效果</button><button id="repair-retry" class="text-link">重新生成此部件</button><button data-step="apply" class="button primary">应用并构建新版本</button></div>
 <p class="help">应用不会自动标记验收通过。画笔用于修掩膜；局部重绘需要填写文字要求。</p>
 </div></div><div id="repair-search-results"></div><div id="repair-advice"></div><div id="repair-results" class="repair-results"></div></div>`;
  $("repair-mode").onchange = () => {
    selectedMode = $("repair-mode").value;
    $("repair-upload-label").hidden = !["replace", "add"].includes(
      selectedMode,
    );
  };
  $("repair-create").onclick = () => guard(createDraft);
  $("repair-drafts").onchange = () =>
    guard(async () => {
      if (dirty) await saveDraft();
      await openDraft($("repair-drafts").value);
    });
  $("repair-save").onclick = () => guard(saveDraft);
  $("repair-discard").onclick = () =>
    guard(async () => {
      if (!draft) return;
      await api(`${endpoint()}/repairs/${draft.id}`, {
        method: "DELETE",
        body: { version: draft.version },
      });
      draft = null;
      dirty = false;
      $("repair-editor").hidden = true;
      message("草稿已丢弃，原版本保持不变");
      signature = "";
    });
  for (const element of document.querySelectorAll(
    "#repair-editor input,#repair-editor select,#repair-editor textarea",
  )) {
    if (
      [
        "repair-radius",
        "repair-overlay",
        "repair-param",
        "repair-pose",
        "repair-target",
        "repair-param-value",
        "repair-visual-accepted",
        "repair-mask-layer",
      ].includes(element.id)
    )
      continue;
    element.addEventListener("input", () => {
      remember();
      readFields();
      changed();
    });
  }
  $("repair-mask-layer").onchange = () => guard(async () => {
    await saveDraft();
    maskLayer = $("repair-mask-layer").value;
    gesture = null;
    fillFields();
    await loadImages();
  });
  $("repair-undo").onclick = () => guard(async () => {
    if (history.length) {
      const entry = history.pop();
      future.push(capture(entry.scope));
      restore(entry);
      changed();
      fillFields();
      renderChecks();
      await loadImages();
    }
  });
  $("repair-redo").onclick = () => guard(async () => {
    if (future.length) {
      const entry = future.pop();
      history.push(capture(entry.scope));
      restore(entry);
      changed();
      fillFields();
      renderChecks();
      await loadImages();
    }
  });
  $("repair-fit").onclick = fit;
  $("repair-visual-accepted").onchange = () => {
    draft.edit.visual_accepted = $("repair-visual-accepted").checked;
    dirty = true;
    message("视觉确认尚未保存");
    controls();
  };
  $("repair-overlay").onchange = draw;
  $("repair-rig-context").onclick = () => guard(() => job("rig_context"));
  $("repair-set-param").onclick = () => {
    remember();
    draft.edit.rig.pose[$("repair-param").value] = Number(
      $("repair-param-value").value,
    );
    draft.edit.rig.drags = [];
    changed();
  };
  $("repair-target").onchange = () => {
    remember();
    draft.edit.rig.target = $("repair-target").value;
    draft.edit.rig.drags = [];
    changed();
  };
  $("repair-pose").onchange = async () => {
    remember();
    draft.edit.rig.pose = JSON.parse($("repair-pose").value || "{}");
    draft.edit.rig.drags = [];
    changed();
  };
  $("repair-retry").onclick = () => guard(() => job("preview", true));
  document
    .querySelectorAll("[data-step]")
    .forEach(
      (button) =>
        (button.onclick = () => guard(() => job(button.dataset.step))),
    );
  const canvas = $("repair-canvas");
  canvas.onpointerdown = down;
  canvas.onpointermove = move;
  canvas.onpointerup = up;
  canvas.onpointercancel = up;
  canvas.onwheel = (e) => {
    e.preventDefault();
    const p = canvasPoint(e),
      factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    zoom = Math.min(20, Math.max(0.05, zoom * factor));
    pan = [e.offsetX - p[0] * zoom, e.offsetY - p[1] * zoom];
    draw();
  };
  new ResizeObserver(() => {
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = 520;
    draw();
  }).observe(canvas.parentElement);
}
async function guard(fn) {
  if (pending) return;
  pending = true;
  controls();
  try {
    await fn();
  } catch (e) {
    message(e.message, true);
  } finally {
    pending = false;
    controls();
  }
}
function capture(scope) {
  return {
    scope,
    value: structuredClone(
      scope === "extraction" ? draft.edit.mask :
      scope === "cleanup" ? draft.edit.cleanup.mask : draft.edit,
    ),
  };
}
function restore(entry) {
  if (entry.scope === "edit") draft.edit = entry.value;
  else {
    maskLayer = entry.scope;
    if (maskLayer === "cleanup") draft.edit.cleanup.mask = entry.value;
    else draft.edit.mask = entry.value;
  }
}
function remember(scope = "edit") {
  history.push(capture(scope));
  if (history.length > 60) history.shift();
  future = [];
}
function changed() {
  dirty = true;
  draft.edit.visual_accepted = false;
  $("repair-visual-accepted").checked = false;
  message("有未保存的修改；执行任务前会自动保存");
  controls();
}
function readFields() {
  if (!draft) return;
  const e = draft.edit;
  e.name = $("repair-name").value;
  e.request = $("repair-request").value;
  e.semantic = $("repair-semantic").value;
  e.side = $("repair-side").value;
  e.inpaint_padding = Number($("repair-inpaint-padding").value);
  e.cleanup.expansion = Number($("repair-cleanup-expansion").value);
  e.cleanup.backend = $("repair-cleanup-backend").value;
  e.cleanup.prompt = $("repair-cleanup-prompt").value;
  e.cleanup.allow_visible = $("repair-cleanup-visible").checked;
  e.candidate_limit = Number($("repair-candidate-limit").value);
  e.candidate_timeout = Number($("repair-candidate-timeout").value);
  e.placement = ["x", "y", "w", "h"].map((k) => Number($(`repair-${k}`).value));
  if (["split", "bind"].includes(e.mode))
    e.binding = {
      ...(e.binding || {}),
      motion: $("repair-motion").value,
      degrees: Number($("repair-degrees").value),
      root: ["root-x", "root-y"].map((k) => Number($(`repair-${k}`).value)),
      tip: ["tip-x", "tip-y"].map((k) => Number($(`repair-${k}`).value)),
      root_pin: Number($("repair-pin").value),
      softness: Number($("repair-softness").value),
      toggle: e.binding?.toggle || false,
    };
  e.rig.action = $("repair-rig-action").value;
  e.rig.opacity = Number($("repair-opacity").value);
  e.rig.radius = Number($("repair-influence").value);
}
function fillFields() {
  const e = draft.edit,
    b = e.binding || {
      motion: "fixed",
      degrees: 4,
      root: [0.5, 0],
      tip: [0.5, 1],
      root_pin: 0.3,
      softness: 1.5,
    };
  $("repair-name").value = e.name;
  $("repair-visual-accepted").checked = e.visual_accepted;
  $("repair-request").value = e.request;
  $("repair-semantic").value = e.semantic;
  $("repair-side").value = e.side;
  $("repair-inpaint-padding").value = e.inpaint_padding;
  $("repair-inpaint-field").hidden = e.mode !== "split";
  $("repair-mask-layer-field").hidden = e.mode !== "split";
  $("repair-mask-layer").value = maskLayer;
  $("repair-candidates").hidden = maskLayer === "cleanup";
  $("repair-cleanup-fields").hidden = e.mode !== "split";
  $("repair-cleanup-expansion").value = e.cleanup.expansion;
  $("repair-cleanup-backend").value = e.cleanup.backend;
  $("repair-cleanup-prompt").value = e.cleanup.prompt;
  $("repair-cleanup-visible").checked = e.cleanup.allow_visible;
  $("repair-candidate-limit").value = e.candidate_limit;
  $("repair-candidate-timeout").value = e.candidate_timeout;
  $("repair-candidate-limit").closest("details").hidden = !["split", "repaint", "bind", "rig"].includes(e.mode);
  ["x", "y", "w", "h"].forEach(
    (k, i) => ($(`repair-${k}`).value = e.placement?.[i] ?? 0),
  );
  $("repair-motion").value = b.motion;
  $("repair-degrees").value = b.degrees;
  ["root-x", "root-y", "tip-x", "tip-y"].forEach(
    (k, i) => ($(`repair-${k}`).value = [...b.root, ...b.tip][i]),
  );
  $("repair-pin").value = b.root_pin;
  $("repair-softness").value = b.softness;
  $("repair-placement").hidden = !["replace", "add"].includes(e.mode);
  $("repair-category").hidden = !["split", "replace", "add"].includes(e.mode);
  $("repair-radius").closest("label").hidden = ["rig", "bind"].includes(e.mode);
  $("repair-binding").hidden = !["split", "bind"].includes(e.mode);
  $("repair-rig-fields").hidden = e.mode !== "rig";
  $("repair-rig-action").value = e.rig.action;
  $("repair-opacity").value = e.rig.opacity;
  $("repair-influence").value = e.rig.radius;
  const tools =
    e.mode === "rig"
      ? {
          problem: "圈出问题",
          drag: "拖动局部",
          pin: "固定连接点",
          pan: "平移",
        }
      : {
          keep: "保留点",
          exclude: "排除点",
          box: "框选",
          paint: "画笔",
          erase: "橡皮擦",
          include: "检查保留区域",
          reject: "检查排除区域",
          pan: "平移",
        };
  if (e.mode === "bind") {
    for (const key of Object.keys(tools)) delete tools[key];
    tools.pan = "平移";
    tools.root = "标记连接根部";
    tools.tip = "标记自由末端";
  }
  if (e.mode === "split" && maskLayer === "cleanup") {
    for (const key of Object.keys(tools)) delete tools[key];
    Object.assign(tools, { paint: "加入清理范围", erase: "排除清理范围", pan: "平移" });
  }
  if (e.mode === "rig" && draft.result.advice?.candidates?.length > 1)
    tools.candidate = "选择定位候选";
  $("repair-canvas-help").textContent =
    maskLayer === "cleanup"
      ? "橙色为底层清理范围。画笔加入、橡皮擦排除；提取部件的轮廓不会因此改变。滚轮缩放，平移工具拖动画布。"
      : e.mode === "repaint"
        ? "先用画笔涂出需要重绘的区域，再填写文字要求。绿色范围内进行局部重绘，范围外保持不变；滚轮缩放，平移工具拖动画布。"
      : e.mode === "rig"
      ? "滚轮缩放；拖动会吸附最近的网格点。先载入姿态，标记固定点与移动点，再预览完整修正。"
      : "滚轮缩放；平移工具拖动画布。绿色为保留，红点为排除；画笔和橡皮擦直接修正掩膜。";
  if (!(tool in tools)) tool = Object.keys(tools)[0];
  $("repair-tools").innerHTML = Object.entries(tools)
    .map(
      ([k, n]) =>
        `<button data-tool="${k}" class="${k === tool ? "active" : ""}">${n}</button>`,
    )
    .join("");
  document.querySelectorAll("[data-tool]").forEach(
    (b) =>
      (b.onclick = () => {
        tool = b.dataset.tool;
        fillFields();
      }),
  );
  const context = draft.rig_context;
  if (context) {
    $("repair-target").innerHTML = context.objects
      .map(
        (o) =>
          `<option value="${esc(o.id)}">${esc(o.name || o.id)} · ${o.kind}</option>`,
      )
      .join("");
    $("repair-target").value = e.rig.target || context.target.id;
    $("repair-param").innerHTML = context.parameters
      .map(
        (p) =>
          `<option value="${esc(p.id)}">${esc(p.name || p.id)} (${p.min}…${p.max})</option>`,
      )
      .join("");
  }
  $("repair-pose").innerHTML =
    '<option value="{}">默认姿态</option>' +
    (detail?.previews || [])
      .filter((p) => p.group === "poses" && p.parameters)
      .map(
        (p) =>
          `<option value="${esc(JSON.stringify(p.parameters))}">${esc(p.label)}</option>`,
      )
      .join("");
}
async function createDraft() {
  if (busy() && !pending) return;
  const origin = project;
  let upload = null;
  if (["replace", "add"].includes(selectedMode)) {
    const file = $("repair-upload").files[0];
    if (!file) throw Error("请选择要导入的部件图片");
    const form = new FormData();
    form.append("file", file);
    upload = (await api("/api/uploads", { method: "POST", body: form })).token;
  }
  if (origin !== project) throw Error("项目已切换，请重新提交");
  const result = await api(`${endpoint()}/repairs`, {
    method: "POST",
    body: {
      revision: detail.revision.id,
      part: selectedMode === "add" ? null : $("repair-part").value,
      mode: selectedMode,
      upload,
    },
  });
  if (origin !== project) return;
  await openDraft(result.id);
  signature = "";
}
async function openDraft(id) {
  if (!id) return;
  const e = ++epoch,
    result = await api(`${endpoint()}/repairs/${id}`);
  if (e !== epoch) return;
  draft = result;
  maskLayer = "extraction";
  selectedMode = draft.edit.mode;
  tool = selectedMode === "repaint" ? "paint" : "keep";
  $("repair-mode").value = selectedMode;
  $("repair-part").value = draft.part || "";
  $("repair-drafts").value = draft.id;
  dirty = false;
  history = [];
  future = [];
  localStorage.setItem(`l2d-repair-${project}`, id);
  displayed = "";
  $("repair-editor").hidden = false;
  fillFields();
  renderResult();
  await loadImages();
  message(
    `${modes[draft.edit.mode]} · 草稿 ${draft.id.slice(0, 8)} · v${draft.version}`,
  );
  controls();
}
async function saveDraft() {
  if (!draft || !dirty) return;
  for (const input of document.querySelectorAll(
    '#repair-editor input[type="number"]',
  )) {
    if (
      input.id !== "repair-param-value" &&
      !input.closest("[hidden]") &&
      (!input.value || !input.validity.valid)
    )
      throw Error("请填写有效的坐标、尺寸或绑定数值");
  }
  readFields();
  const e = epoch;
  const saved = await api(`${endpoint()}/repairs/${draft.id}`, {
    method: "PUT",
    body: { version: draft.version, edit: draft.edit },
  });
  if (e !== epoch) throw Error("已切换草稿，保存结果留在原草稿中");
  draft = saved;
  dirty = false;
  message("草稿已保存");
  await loadImages();
  renderResult();
}
async function job(step, retry = false) {
  if (!draft) throw Error("请先打开草稿");
  await saveDraft();
  if (step === "apply" && detail.head !== draft.base_revision)
    throw Error("请先在版本记录中将草稿基础版本设为创作起点");
  await api(`${endpoint()}/jobs`, {
    method: "POST",
    body: {
      action: "repair",
      expected_head: detail.head,
      revision: draft.base_revision,
      draft: draft.id,
      draft_version: draft.version,
      repair_step: step,
      retry,
      until: "rig",
    },
  });
  message("任务已提交，完成后结果会显示在这里");
}
function loadImage(url) {
  return new Promise((resolve, reject) => {
    const im = new Image();
    im.onload = () => resolve(im);
    im.onerror = () => reject(Error("无法载入修正图片"));
    im.src = url;
  });
}
async function loadImages() {
  if (!draft) return;
  const e = epoch;
  const rigMode = draft.edit.mode === "rig";
  const url =
    rigMode && draft.rig_context ? draft.rig_context.url : draft.source;
  const selectedCandidate = draft.candidates?.find((c) => c.mask === draft.edit.mask.candidate)
    || (draft.edit.mask.candidate ? null : [...(draft.candidates || [])].sort((a, b) => b.score - a.score)[0]);
  const maskURL = maskLayer === "extraction" && selectedCandidate
    ? selectedCandidate.url + `?t=${Date.now()}`
    : draft.mask + `${maskLayer === "cleanup" ? "&layer=cleanup" : ""}&annotations=0&t=${Date.now()}`;
  const images = await Promise.all([
    loadImage(url),
    rigMode
      ? Promise.resolve(null)
      : loadImage(maskURL),
  ]);
  if (e !== epoch) return;
  source = images[0];
  mask = images[1];
  if (displayed !== url) {
    displayed = url;
    fit();
  } else draw();
}
function fit() {
  if (!source || !draft) return;
  const c = $("repair-canvas");
  zoom = Math.min(
    (c.width - 24) / source.width,
    (c.height - 24) / source.height,
  );
  pan = [
    (c.width - source.width * zoom) / 2,
    (c.height - source.height * zoom) / 2,
  ];
  draw();
}
function canvasPoint(e) {
  const r = $("repair-canvas").getBoundingClientRect();
  return [
    (e.clientX - r.left - pan[0]) / zoom,
    (e.clientY - r.top - pan[1]) / zoom,
  ];
}
function inBounds(p) {
  return (
    source &&
    p[0] >= 0 &&
    p[1] >= 0 &&
    p[0] < source.width &&
    p[1] < source.height
  );
}
function rigPoint(p) {
  const v = draft.rig_context?.view?.canvasRect;
  if (!v) throw Error("请先载入当前姿态");
  return [
    v.left + (p[0] * (v.right - v.left)) / source.width,
    v.top + (p[1] * (v.bottom - v.top)) / source.height,
  ];
}
function fromRig(p) {
  const v = draft.rig_context?.view?.canvasRect;
  return v
    ? [
        ((p[0] - v.left) * source.width) / (v.right - v.left),
        ((p[1] - v.top) * source.height) / (v.bottom - v.top),
      ]
    : p;
}
function down(e) {
  if (!draft || draft.applied_revision || pending) return;
  const p = canvasPoint(e);
  if (tool !== "pan" && !inBounds(p)) return;
  $("repair-canvas").setPointerCapture(e.pointerId);
  gesture = { start: p, last: p, pixels: [e.clientX, e.clientY], points: [p] };
  if (tool !== "pan") remember(["keep", "exclude", "paint", "erase", "box", "include", "reject"].includes(tool) ? maskLayer : "edit");
}
function move(e) {
  if (!gesture) return;
  const p = canvasPoint(e);
  if (tool === "pan") {
    pan[0] += e.clientX - gesture.pixels[0];
    pan[1] += e.clientY - gesture.pixels[1];
    gesture.pixels = [e.clientX, e.clientY];
  } else if (inBounds(p)) {
    gesture.last = p;
    if (["paint", "erase"].includes(tool)) gesture.points.push(p);
  }
  draw();
}
function up() {
  if (!gesture) return;
  const g = gesture;
  gesture = null;
  if (tool === "pan") {
    draw();
    return;
  }
  const m = activeMask();
  if (tool === "candidate") {
    const p = rigPoint(g.start);
    const choices = detail.repair_parts.filter((part) => {
      const b = draft.rig_context?.part_regions?.[part.id];
      return (
        b &&
        draft.result.advice.candidates.includes(part.id) &&
        p[0] >= b[0] &&
        p[0] <= b[0] + b[2] &&
        p[1] >= b[1] &&
        p[1] <= b[1] + b[3]
      );
    });
    if (choices.length === 1) {
      $("repair-part").value = choices[0].id;
      message("已选中候选部件，请创建对应修正草稿");
    } else message("候选重叠或未选中，请使用下方候选名称选择");
    return;
  }
  if (["keep", "exclude"].includes(tool))
    m.marks.push({
      kind: tool,
      points: [g.start],
      radius: Number($("repair-radius").value),
    });
  if (["paint", "erase"].includes(tool))
    m.marks.push({
      kind: tool,
      points: g.points,
      radius: Number($("repair-radius").value),
    });
  if (["box", "include", "reject"].includes(tool)) {
    const x = Math.floor(Math.min(g.start[0], g.last[0])),
      y = Math.floor(Math.min(g.start[1], g.last[1]));
    const b = [
      x,
      y,
      Math.max(1, Math.ceil(Math.abs(g.last[0] - g.start[0]))),
      Math.max(1, Math.ceil(Math.abs(g.last[1] - g.start[1]))),
    ];
    if (tool === "box") m.box = b;
    else {
      m.checks.push({
        name: `${tool === "include" ? "保留" : "排除"}区域 ${m.checks.length + 1}`,
        kind: tool === "include" ? "include" : "exclude",
        box: b,
        confirmed: true,
      });
    }
  }
  if (tool === "root" || tool === "tip") {
    draft.edit.binding = draft.edit.binding || {
      motion: "sway",
      degrees: 4,
      root: [0.5, 0],
      tip: [0.5, 1],
      root_pin: 0.3,
      softness: 1.5,
      toggle: false,
    };
    draft.edit.binding[tool] = [
      g.start[0] / source.width,
      g.start[1] / source.height,
    ];
    fillFields();
  }
  if (tool === "problem") {
    const a = rigPoint(g.start),
      b = rigPoint(g.last);
    draft.edit.rig.region = [
      Math.min(a[0], b[0]),
      Math.min(a[1], b[1]),
      Math.abs(a[0] - b[0]),
      Math.abs(a[1] - b[1]),
    ];
  }
  if (tool === "pin" || tool === "drag") {
    try {
      if (
        JSON.stringify(draft.rig_context?.pose) !==
          JSON.stringify(draft.edit.rig.pose) ||
        (draft.edit.rig.target &&
          draft.edit.rig.target !== draft.rig_context?.target.id)
      )
        throw Error("姿态已改变，请先载入当前姿态再标记");
      draft.edit.rig.drags.push({
        start: rigPoint(g.start),
        end: rigPoint(tool === "pin" ? g.start : g.last),
        fixed: tool === "pin",
      });
    } catch (e) {
      message(e.message, true);
      return;
    }
  }
  changed();
  renderChecks();
  draw();
}
function draw() {
  const canvas = $("repair-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!source || !draft) return;
  ctx.save();
  ctx.translate(...pan);
  ctx.scale(zoom, zoom);
  ctx.drawImage(source, 0, 0);
  if (draft.edit.mode === "rig") {
    ctx.fillStyle = "#5374b2";
    for (const point of draft.rig_context?.points || []) {
      const p = fromRig(point.slice(1));
      ctx.fillRect(p[0] - 1 / zoom, p[1] - 1 / zoom, 2 / zoom, 2 / zoom);
    }
    ctx.strokeStyle = "#ec9433";
    ctx.fillStyle = "#ae5707";
    ctx.font = `${12 / zoom}px sans-serif`;
    for (const part of detail.repair_parts.filter((p) =>
      draft.result.advice?.candidates?.includes(p.id),
    )) {
      if (!draft.rig_context?.part_regions?.[part.id]) continue;
      const [x, y, w, h] = draft.rig_context.part_regions[part.id],
        a = fromRig([x, y]),
        b = fromRig([x + w, y + h]);
      ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      ctx.fillText(
        detail.part_names?.[part.id] || part.name,
        a[0],
        a[1] - 4 / zoom,
      );
    }
  }
  if (mask && $("repair-overlay").checked) {
    const temp = document.createElement("canvas");
    temp.width = source.width;
    temp.height = source.height;
    const t = temp.getContext("2d");
    t.drawImage(mask, 0, 0);
    for (const mark of activeMask().marks) {
      if (!["paint", "erase"].includes(mark.kind)) continue;
      t.strokeStyle = t.fillStyle = mark.kind === "paint" ? "#fff" : "#000";
      t.lineWidth = mark.radius * 2;
      t.lineCap = t.lineJoin = "round";
      t.beginPath();
      t.moveTo(...mark.points[0]);
      mark.points.forEach((p) => t.lineTo(...p));
      t.stroke();
      for (const p of mark.points) {
        t.beginPath();
        t.arc(...p, mark.radius, 0, Math.PI * 2);
        t.fill();
      }
    }
    const d = t.getImageData(0, 0, temp.width, temp.height);
    for (let i = 0; i < d.data.length; i += 4) {
      const a = d.data[i];
      d.data[i] = maskLayer === "cleanup" ? 230 : 54;
      d.data[i + 1] = maskLayer === "cleanup" ? 146 : 157;
      d.data[i + 2] = maskLayer === "cleanup" ? 45 : 115;
      d.data[i + 3] = a * 0.4;
    }
    t.putImageData(d, 0, 0);
    t.globalCompositeOperation = "destination-in";
    t.drawImage(source, 0, 0);
    ctx.drawImage(temp, 0, 0);
  }
  ctx.lineWidth = 1.5 / zoom;
  function stroke(mark) {
    ctx.strokeStyle = ctx.fillStyle = ["keep", "paint"].includes(mark.kind)
      ? "#19b57c"
      : "#f25b63";
    if (["paint", "erase"].includes(mark.kind)) {
      ctx.globalAlpha = 0.5;
      ctx.lineWidth = mark.radius * 2;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(...mark.points[0]);
      mark.points.forEach((p) => ctx.lineTo(...p));
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(...mark.points[0], mark.radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    } else {
      ctx.beginPath();
      ctx.arc(...mark.points[0], 4 / zoom, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  activeMask().marks
    .filter((m) => ["keep", "exclude"].includes(m.kind))
    .forEach(stroke);
  ctx.lineWidth = 1.5 / zoom;
  for (const c of activeMask().checks) {
    ctx.strokeStyle = c.kind === "include" ? "#18a76f" : "#ec5463";
    ctx.strokeRect(...c.box);
  }
  if (activeMask().box) {
    ctx.strokeStyle = "#398be7";
    ctx.strokeRect(...activeMask().box);
  }
  if (draft?.edit.rig.region) {
    const [x, y, w, h] = draft.edit.rig.region,
      a = fromRig([x, y]),
      b = fromRig([x + w, y + h]);
    ctx.strokeStyle = "#ef9135";
    ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
  }
  for (const d of draft?.edit.rig.drags || []) {
    const a = fromRig(d.start),
      b = fromRig(d.end);
    ctx.strokeStyle = ctx.fillStyle = d.fixed ? "#eaba37" : "#437be3";
    ctx.beginPath();
    ctx.moveTo(...a);
    ctx.lineTo(...b);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(...b, 4 / zoom, 0, Math.PI * 2);
    ctx.fill();
  }
  if (gesture && tool !== "pan") {
    ctx.strokeStyle = "#318bc0";
    ctx.lineWidth = 2 / zoom;
    if (["paint", "erase"].includes(tool))
      stroke({
        kind: tool,
        points: gesture.points,
        radius: Number($("repair-radius").value),
      });
    else if (tool === "drag") {
      ctx.beginPath();
      ctx.moveTo(...gesture.start);
      ctx.lineTo(...gesture.last);
      ctx.stroke();
    } else
      ctx.strokeRect(
        gesture.start[0],
        gesture.start[1],
        gesture.last[0] - gesture.start[0],
        gesture.last[1] - gesture.start[1],
      );
  }
  ctx.restore();
}
function renderChecks() {
  if (!draft) return;
  $("repair-checks").innerHTML =
    "<h3>区域检查清单</h3>" +
    draft.edit.mask.checks
      .map(
        (c, i) =>
          `<div class="repair-check-row"><input data-check-name="${i}" value="${esc(c.name)}" aria-label="检查项名称"><span>${c.kind === "include" ? "保留" : "排除"}</span><label><input type="checkbox" data-confirm-check="${i}" ${c.confirmed ? "checked" : ""}>区域已核对</label><button data-remove-check="${i}" aria-label="删除检查项">×</button></div>`,
      )
      .join("");
  document.querySelectorAll("[data-confirm-check]").forEach(
    (el) =>
      (el.onchange = () => {
        remember("extraction");
        draft.edit.mask.checks[Number(el.dataset.confirmCheck)].confirmed =
          el.checked;
        changed();
      }),
  );
  document.querySelectorAll("[data-check-name]").forEach(
    (el) =>
      (el.onchange = () => {
        remember("extraction");
        draft.edit.mask.checks[Number(el.dataset.checkName)].name = el.value;
        changed();
      }),
  );
  document.querySelectorAll("[data-remove-check]").forEach(
    (el) =>
      (el.onclick = () => {
        remember("extraction");
        draft.edit.mask.checks.splice(Number(el.dataset.removeCheck), 1);
        changed();
        renderChecks();
        draw();
      }),
  );
}
function renderResult() {
  renderChecks();
  renderSearch();
  $("repair-candidates").hidden = maskLayer === "cleanup";
  $("repair-candidates").innerHTML = (draft.candidates || [])
    .map(
      (c) =>
        `<button data-candidate="${esc(c.mask)}" class="${draft.edit.mask.candidate === c.mask ? "active" : ""}"><img src="${c.url}" alt="分割候选"><span>${esc(c.mask)} · SAM ${(c.score * 100).toFixed(0)}%</span></button>`,
    )
    .join("");
  document.querySelectorAll("[data-candidate]").forEach(
    (el) =>
      (el.onclick = () =>
        guard(async () => {
          remember("extraction");
          draft.edit.mask.candidate = el.dataset.candidate;
          changed();
          await saveDraft();
        })),
  );
  const p = draft.result.preview,
    v = draft.result.visual;
  const checks = [
    ...(p?.checks || []),
    ...(v?.items || []).map((i) => ({ ...i, source: "VLM" })),
  ];
  $("repair-results").innerHTML =
    `<div class="repair-evidence"><h3>检查证据</h3>${checks.map((c) => `<p class="${c.status === "fail" ? "job-error" : "help"}">${{ pass: "通过", fail: "失败", uncertain: "待确认" }[c.status] || "待确认"} · ${c.source === "VLM" ? "视觉判断" : "程序规则"} · ${esc(c.name)} ${esc(c.reason || "")}</p>`).join("")}${(p?.quality?.missing_parts || []).map((s) => `<p class="job-error">缺少部件：${esc(s)}</p>`).join("")}${(p?.quality?.errors || []).map((s) => `<p class="job-error">${esc(s)}</p>`).join("")}<p class="help">几何与像素检查通过不代表视觉效果已验收。</p></div>` +
    [{ url: draft.before, label: "原版本合成" }, ...(draft.images || [])]
      .map(
        (im) =>
          `<figure><a href="${im.url}" target="_blank" rel="noopener"><img src="${im.url}" alt="${esc(im.label)}"></a><figcaption>${esc(im.label)}</figcaption></figure>`,
      )
      .join("");
  const a = draft.result.advice;
  $("repair-advice").innerHTML = a
    ? `<h3>修正建议</h3><p>${esc(a.summary || "")}</p><p>${esc(a.reason || "")}</p>${a.action && ["binding", "landmarks", "opacity", "smooth"].includes(a.action) ? '<button id="repair-use-advice" class="button secondary">将建议载入草稿并预览</button>' : ""}${
        a.candidates
          ?.filter((p) => p !== draft.part)
          .map(
            (p) =>
              `<button class="text-link" data-other-part="${esc(p)}">检查 ${esc(detail.part_names?.[p] || p)}</button>`,
          )
          .join("") || ""
      }`
    : "";
  if (a?.automatic_draft) {
    const open = document.createElement("button");
    open.className = "button primary";
    open.textContent = "打开自动修正候选";
    open.onclick = () => guard(() => openDraft(a.automatic_draft));
    $("repair-advice").append(open);
    if (a.automatic_error) {
      const p = document.createElement("p");
      p.textContent = "自动尝试未完成：" + a.automatic_error;
      p.className = "job-error";
      $("repair-advice").append(p);
    }
  }
  if ($("repair-use-advice"))
    $("repair-use-advice").onclick = () =>
      guard(async () => {
        remember();
        if (a.action === "binding" && draft.edit.mode !== "bind") {
          const next = await api(`${endpoint()}/repairs`, {
            method: "POST",
            body: {
              revision: draft.base_revision,
              part: draft.part,
              mode: "bind",
            },
          });
          await openDraft(next.id);
        }
        if (a.action === "binding") draft.edit.binding = a.binding;
        else {
          draft.edit.rig = { ...draft.edit.rig, ...a.rig, action: a.action };
        }
        changed();
        fillFields();
        await job("preview");
      });
  document.querySelectorAll("[data-other-part]").forEach(
    (el) =>
      (el.onclick = () => {
        $("repair-part").value = el.dataset.otherPart;
        message("已选择候选部件，请创建对应修正草稿");
      }),
  );
  if (v?.regions?.length) {
    const button = document.createElement("button");
    button.className = "button secondary";
    button.textContent = "将视觉模型建议区域加入检查清单";
    button.onclick = () => {
      remember("extraction");
      draft.edit.mask.checks.push(...v.regions);
      changed();
      renderChecks();
      draw();
    };
    $("repair-advice").append(button);
  }
}
function renderSearch() {
  const search = draft.result.search;
  const statuses = { pass: "通过", fail: "失败", ready: "检查通过，待比较", rejected: "已淘汰", uncertain: "待确认", error: "执行失败", running: "正在执行", paused: "已暂停，可继续", stale: "候选已改变，旧检查不再有效", queued: "等待执行", skipped: "未执行", cancelled: "已取消", timeout: "达到预算" };
  const status = (value) => statuses[value] || "待检查";
  const groupStatus = (items) => !items.length ? "skipped"
    : items.some((i) => i.status === "fail") ? "fail"
    : items.every((i) => i.status === "pass") ? "pass" : "uncertain";
  const candidates = search?.candidates || [];
  const stale = search && search.version !== draft.version;
  $("repair-search-results").innerHTML = search
    ? `<h3>自动尝试结果</h3><p class="help">${stale ? "这是修改草稿前的结果，请重新搜索以核对新标注。" : "候选保存在独立草稿中。打开候选查看前后对比，确认后再应用。推荐项仍需视觉验收。"}</p>${search.summary ? `<p class="help">${esc(search.summary)}</p>` : ""}${search.budget_reached ? '<p class="help">已达到本次搜索预算，已完成的候选仍可查看，也可继续搜索。</p>' : ""}${search.comparison_error ? `<p class="job-error">候选比较未完成：${esc(search.comparison_error)}</p>` : ""}<div class="repair-search-grid">${candidates.map((c) => {
      const rules = c.checks || [];
      const visual = c.visual?.items || [];
      const recommended = !stale && search.recommended === c.draft;
      return `<article class="repair-search-card ${recommended ? "recommended" : ""}"><h4>${esc(c.label)}${recommended ? " · 推荐查看" : ""}</h4><p>${status(c.status)}${Number.isFinite(c.elapsed) ? ` · ${Math.round(c.elapsed)} 秒` : ""}</p>${c.reason ? `<p class="${["fail", "rejected", "error"].includes(c.status) ? "job-error" : "help"}">${esc(c.reason)}</p>` : ""}<p class="help">程序规则：${status(c.rule_status || groupStatus(rules))} · VLM 判断：${status(visual.length ? c.visual_status || groupStatus(visual) : "skipped")}</p>${rules.length || visual.length ? `<details><summary>查看规则与视觉证据</summary>${rules.map((r) => `<p class="help">程序规则 · ${status(r.status)} · ${esc(r.name)} ${esc(r.reason || "")}</p>`).join("")}${visual.map((r) => `<p class="help">视觉判断 · ${status(r.status)} · ${esc(r.name)} ${esc(r.reason || "")}</p>`).join("")}</details>` : ""}${c.draft ? `<button class="button secondary" data-search-draft="${esc(c.draft)}">打开此候选</button>` : ""}</article>`;
    }).join("")}</div>`
    : "";
  if (draft.result.origin?.draft) {
    const back = document.createElement("button");
    back.className = "button secondary";
    back.textContent = "返回候选比较";
    back.dataset.searchDraft = draft.result.origin.draft;
    $("repair-search-results").prepend(back);
  }
  document.querySelectorAll("[data-search-draft]").forEach((button) => {
    button.onclick = () => guard(async () => {
      if (dirty) await saveDraft();
      await openDraft(button.dataset.searchDraft);
    });
  });
}
function controls() {
  if (!$("repair-create")) return;
  const active = busy();
  $("repair-create").disabled = active;
  const closed = !draft || !!draft.applied_revision;
  document
    .querySelectorAll(
      ".repair-fields input,.repair-fields select,.repair-fields textarea",
    )
    .forEach((el) => (el.disabled = closed || pending));
  $("repair-undo").disabled = closed || pending || !history.length;
  $("repair-redo").disabled = closed || pending || !future.length;
  $("repair-discard").disabled = active || closed;
  $("repair-save").disabled = pending || closed || !dirty;
  $("repair-mask-layer").disabled = pending;
  document.querySelectorAll("[data-step]").forEach((b) => {
    if (b.dataset.step === "search")
      b.textContent = draft?.result.search?.version === draft?.version
        ? "继续搜索并复查候选"
        : "自动尝试并比较候选";
    if (b.dataset.step === "apply")
      b.textContent = draft?.result.preview?.quality?.missing_parts?.length
        ? "应用素材（补齐后再构建）"
        : "应用并构建新版本";
    b.disabled =
      active ||
      closed ||
      (b.dataset.step === "apply" &&
        (!draft?.result.preview?.ready ||
          dirty ||
          draft.result.preview.version !== draft.version));
    b.hidden =
      !!draft &&
      ((b.dataset.step === "segment" &&
        ["rig", "bind"].includes(draft.edit.mode)) ||
        (b.dataset.step === "search" && !["split", "repaint", "bind", "rig"].includes(draft.edit.mode)) ||
        (b.dataset.step === "underpaint" && draft.edit.mode !== "split") ||
        (b.dataset.step === "binding_preview" && draft.edit.mode === "rig"));
  });
  $("repair-retry").disabled = active || closed;
  $("repair-rig-context").disabled = active || closed;
}
window.addEventListener("l2d:state", async (event) => {
  const next = event.detail;
  if (!next.detail?.repair_parts?.length) {
    $("repair-workspace").hidden = true;
    return;
  }
  $("repair-workspace").hidden = false;
  if (next.project !== project) {
    project = next.project;
    draft = null;
    dirty = false;
    epoch++;
    signature = "";
    shell();
  }
  detail = next.detail;
  state = next.state;
  const s = JSON.stringify([
    detail.revision.id,
    detail.repair_parts,
    detail.repairs,
  ]);
  if (signature !== s) {
    signature = s;
    const selected = $("repair-part").value;
    $("repair-part").innerHTML = detail.repair_parts
      .map(
        (p) =>
          `<option value="${esc(p.id)}">${esc(detail.part_names[p.id] || p.name)}</option>`,
      )
      .join("");
    if (detail.repair_parts.some((p) => p.id === selected))
      $("repair-part").value = selected;
    $("repair-drafts").innerHTML =
      '<option value="">选择草稿</option>' +
      detail.repairs
        .map(
          (d) =>
            `<option value="${d.id}">${esc(d.edit.name)} · ${modes[d.edit.mode]} · ${d.applied_revision ? "已应用" : "v" + d.version}</option>`,
        )
        .join("");
    if (draft) $("repair-drafts").value = draft.id;
    const saved = localStorage.getItem(`l2d-repair-${project}`);
    if (!draft && detail.repairs.some((d) => d.id === saved))
      await guard(() => openDraft(saved));
    const server = draft && detail.repairs.find((d) => d.id === draft.id);
    if (
      server &&
      !dirty &&
      !pending &&
      JSON.stringify(server.result) !== JSON.stringify(draft.result)
    ) {
      await guard(() => openDraft(draft.id));
    }
  }
  controls();
});
window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});

window.addEventListener("l2d:repair-part", (event) =>
  guard(async () => {
    if (dirty) await saveDraft();
    selectedMode = event.detail.mode;
    $("repair-mode").value = selectedMode;
    $("repair-part").value = event.detail.part;
    $("repair-upload-label").hidden = true;
    await createDraft();
    if (event.detail.request) {
      draft.edit.request = event.detail.request;
      draft.edit.rig.pose = event.detail.pose || {};
      changed();
      fillFields();
      await saveDraft();
    }
    $("repair-workspace").scrollIntoView({
      block: "start",
      behavior: "smooth",
    });
    if (selectedMode === "rig") await job("rig_context");
  }),
);
