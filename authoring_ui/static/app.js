const SVG_NS = "http://www.w3.org/2000/svg";
const CANVAS_WIDTH = 1181;
const CANVAS_HEIGHT = 705;
const COORDINATE_SCALE = 0.82;
const MAX_FIT_DISPLAY_SCALE = 1.1;
const DEFAULT_PANEL_LAYOUT = { paletteRatio: 0.13, inspectorRatio: 0.18 };
const DEFAULT_PROCESS_CONDITIONS = { duration_s: 1, time_step_s: 0.1, ambient_temperature_K: 300 };
const INITIAL_CONDITION_LABELS = {
  enabled: "초기 활성 상태",
  pressure_Pa_abs: "초기 압력 (Pa, 절대압)",
  inlet_pressure_Pa_abs: "입구 초기 압력 (Pa, 절대압)",
  temperature_K: "초기 온도 (K)",
  inlet_temperature_K: "입구 초기 온도 (K)",
  fluid_temperature_K: "유체 초기 온도 (K)",
  wall_temperature_K: "벽체 초기 온도 (K)",
  lower_wall_temperature_K: "하부 벽체 초기 온도 (K)",
  upper_wall_temperature_K: "상부 벽체 초기 온도 (K)",
  cold_mass_temperature_K: "저온부 초기 온도 (K)",
  ambient_temperature_K: "외기 초기 온도 (K)",
  liquid_fill_fraction: "초기 액체 체적분율 (0~1)",
  vapor_quality: "초기 건도 (0~1)",
  mass_flow_kg_s: "경계 질량유량 (kg/s)",
  initial_mass_flow_kg_s: "초기 질량유량 (kg/s)",
  speed_rpm: "초기 회전수 (rpm)",
  opening_fraction: "초기 개도 (0~1)",
  load_fraction: "초기 부하율 (0~1)",
  initial_input: "초기 입력값",
  initial_output: "초기 출력값",
  setpoint: "초기 설정값",
};

function storedPanelLayout() {
  try {
    const value = JSON.parse(localStorage.getItem("lh2-panel-layout") || "null");
    if (Number.isFinite(value?.paletteRatio) && Number.isFinite(value?.inspectorRatio)) return value;
  } catch (_) { /* Use the responsive defaults. */ }
  return { ...DEFAULT_PANEL_LAYOUT };
}

const state = {
  bootstrap: null,
  design: null,
  selected: null,
  connectionMode: false,
  connectionSource: null,
  activeEdges: new Set(),
  nodeValues: {},
  jobId:null, runStatus:"idle", telemetry:{}, initialRows:{}, pollTimer:null, runGeneration:0, chartRange:null,
  iconMode: localStorage.getItem("lh2-icon-mode") === "3d" ? "3d" : "2d",
  drag: null,
  panelLayout: storedPanelLayout(),
  zoomFactor: 1,
  viewBox: { x: 0, y: 0, width: CANVAS_WIDTH, height: CANVAS_HEIGHT },
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function svg(tag, attrs = {}, text = null) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  if (text !== null) node.textContent = text;
  return node;
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function defaultInitialConditions(node) {
  const builtIn = state.bootstrap?.design?.nodes?.find((item) => item.id === node.id);
  if (builtIn?.initial_conditions) return clone(builtIn.initial_conditions);
  const template = state.bootstrap?.library?.templates?.find((item) => item.template_id === node.template_id);
  if (template?.initial_conditions) return clone(template.initial_conditions);
  if (["tank", "receiver"].includes(node.kind)) { const wallTemperature = node.kind === "receiver" ? 27.2 : 26; return { enabled: true, pressure_Pa_abs: node.kind === "receiver" ? 500000 : 400000, liquid_fill_fraction: 0.5, fluid_temperature_K: 20, wall_temperature_K: wallTemperature, lower_wall_temperature_K: wallTemperature, upper_wall_temperature_K: wallTemperature }; }
  if (node.kind === "boundary") return { enabled: true, pressure_Pa_abs: 200000, temperature_K: node.id.includes("GH2") ? 300 : 20, mass_flow_kg_s: 0, vapor_quality: node.id.includes("GH2") ? 1 : 0 };
  if (node.kind === "header") return { enabled: true, pressure_Pa_abs: 300000, temperature_K: 20, mass_flow_kg_s: 0 };
  return { enabled: true };
}

function normalizeDesign(design) {
  design.process_conditions = { ...DEFAULT_PROCESS_CONDITIONS, ...(design.process_conditions || {}) };
  design.nodes.forEach((node) => {
    node.initial_conditions = { ...defaultInitialConditions(node), ...(node.initial_conditions || {}) };
  });
  return design;
}

function editableFieldMarkup(attribute, key, value, label = key) {
  if(attribute!=='data-param') label = unitInputLabel(key,label);
  value = convertFieldValue(key,value,'display');
  if(typeof value==='number') value=displayNumberInput(value);
  if (typeof value === "boolean") return `<div class="field"><label>${label}</label><select ${attribute}="${key}" data-value-type="boolean"><option value="true" ${value ? "selected" : ""}>사용</option><option value="false" ${!value ? "selected" : ""}>정지</option></select></div>`;
  if (value !== null && typeof value === "object") return `<div class="field"><label>${label} (JSON)</label><textarea ${attribute}="${key}" data-value-type="json">${JSON.stringify(value, null, 2)}</textarea></div>`;
  if (typeof value === "string") return `<div class="field"><label>${label}</label><input ${attribute}="${key}" data-value-type="string" value="${value}"></div>`;
  return `<div class="field"><label>${label}</label><input ${attribute}="${key}" data-value-type="number" type="number" step="any" value="${value}"></div>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function toast(message, timeout = 2300) {
  const target = $("#toast");
  target.textContent = message;
  target.classList.remove("hidden");
  clearTimeout(target._timer);
  target._timer = setTimeout(() => target.classList.add("hidden"), timeout);
}

function setBusy(busy, label = "계산 중") {
  $("#scenarioSelect").disabled = busy;
  $("#runBtn").disabled = busy;
  $("#previewBtn").disabled = busy;
  $("#solverBadge").textContent = busy ? label : "완료";
  $("#solverBadge").className = `badge ${busy ? "running" : "success"}`;
}

function iconMarkup(model) {
  model = String(model || "module");
  const color = "currentColor";
  const common = `viewBox="0 0 48 32" fill="none" stroke="${color}" stroke-width="2"`;
  if (model.includes("tank")) return `<svg ${common}><rect x="3" y="7" width="42" height="18" rx="9"/><path d="M14 7v18M34 7v18" opacity=".35"/></svg>`;
  if (model.includes("pump")) return `<svg ${common}><circle cx="24" cy="16" r="11"/><path d="m19 10 12 6-12 6z"/><path d="M2 16h11M35 16h11"/></svg>`;
  if (model.includes("compressor")) return `<svg ${common}><circle cx="24" cy="16" r="12"/><path d="M17 9v14l15-5v-4z" fill="currentColor" opacity=".22"/><path d="M2 16h10M36 16h10"/><text x="24" y="20" text-anchor="middle" stroke="none" fill="currentColor" font-size="11" font-weight="800">C</text></svg>`;
  if (model.includes("instrument") || model.includes("dpt") || model.includes("transmitter") || model.includes("hart")) return `<svg ${common}><circle cx="24" cy="16" r="12"/><path d="M12 16H3M45 16h-9"/><text x="24" y="20" text-anchor="middle" stroke="none" fill="currentColor" font-size="10" font-weight="800">PT</text></svg>`;
  if (model.includes("pid") || model.includes("control") || model.includes("interlock")) return `<svg ${common}><rect x="8" y="5" width="32" height="22" rx="3"/><path d="M13 21l6-8 6 5 9-9"/></svg>`;
  if (model.includes("vacuum") || model.includes("thermal")) return `<svg ${common}><rect x="5" y="7" width="38" height="18" rx="9"/><rect x="10" y="11" width="28" height="10" rx="5"/><path d="M14 4v5M34 4v5"/></svg>`;
  if (model.includes("solver") || model.includes("network") || model.includes("scenario")) return `<svg ${common}><circle cx="10" cy="16" r="4"/><circle cx="24" cy="7" r="4"/><circle cx="38" cy="16" r="4"/><circle cx="24" cy="25" r="4"/><path d="m13 14 8-5m6 0 8 5m0 4-8 5m-6 0-8-5"/></svg>`;
  if (model.includes("mixer")) return `<svg ${common}><path d="M3 8h12l12 8h18M3 24h12l12-8"/><circle cx="27" cy="16" r="5"/></svg>`;
  if (model.includes("splitter")) return `<svg ${common}><path d="M3 16h18l12-8h12M21 16l12 8h12"/><circle cx="21" cy="16" r="5"/></svg>`;
  if (model.includes("vaporizer")) return `<svg ${common}><rect x="7" y="4" width="34" height="24" rx="3"/><path d="M12 22c5 0 5-12 10-12s5 12 10 12 5-12 10-12"/></svg>`;
  if (model.includes("reliquefier")) return `<svg ${common}><rect x="6" y="4" width="36" height="24" rx="3"/><path d="M24 7v18M16 11l16 10M32 11 16 10"/></svg>`;
  if (model.includes("vent")) return `<svg ${common}><path d="M19 28h10l-2-21h-6z"/><path d="M15 7h18M14 3h20"/></svg>`;
  if (model.includes("pressure_relief")) return `<svg ${common}><path d="M7 9l17 7L7 23zM41 9l-17 7 17 7z"/><path d="M24 16V6m0 0 4-3m-4 3-4-3"/></svg>`;
  if (model.includes("valve")) return `<svg ${common}><path d="M7 8l17 8L7 24zM41 8l-17 8 17 8z"/><path d="M24 16V4h9"/></svg>`;
  return `<svg ${common}><path d="M3 16h42"/><circle cx="24" cy="16" r="8"/></svg>`;
}

function applyIconMode() {
  const is3d = state.iconMode === "3d";
  document.body.classList.toggle("icon-3d", is3d);
  const button = $("#view3dBtn");
  button.classList.toggle("view-active", is3d);
  button.setAttribute("aria-pressed", String(is3d));
  button.textContent = is3d ? "▣ 2D Symbols" : "◇ 3D Icons";
}

function displayTemplateName(template) {
  if (template.display_name) return template.display_name;
  const names = {
    "lh2-horizontal-storage-tank": "LH2 수평 저장탱크",
    "lh2-reliquefaction-receiver": "재액화 수액기",
    "ambient-hydrogen-vaporizer": "대기식 기화기",
    "hydrogen-reliquefier-unit": "수소 재액화기",
    "virtual-lh2-transfer-pump": "가상 LH2 펌프",
    "cryogenic-hydrogen-pipe": "극저온 배관",
    "cryogenic-isolation-valve": "극저온 차단밸브",
    "hydrogen-vent-stack": "수소 벤트스택",
  };
  return names[template.template_id] || template.template_id;
}

function renderPalette(filter = "") {
  const palette = $("#palette");
  palette.innerHTML = "";
  const templates = state.bootstrap.library.templates.filter((item) =>
    `${displayTemplateName(item)} ${item.template_id} ${item.component_model || item.module_id || ""}`.toLowerCase().includes(filter.toLowerCase())
  );
  $("#libraryCount").textContent = templates.length;
  const categoryOrder = ["Storage", "Pressure Changers", "Heat Exchangers", "Piping", "Flow Routing", "Valves", "Safety", "Thermal Physics", "Instrumentation & Control", "Advanced Physics", "Property & Solvers", "Other"];
  categoryOrder.forEach((category) => {
    const members = templates.filter((item) => (item.category || "Other") === category);
    if (!members.length) return;
    const section = document.createElement("section");
    section.className = "palette-category";
    section.innerHTML = `<div class="palette-category-title"><span>${category}</span><span>${members.length}</span></div><div class="palette-category-grid"></div>`;
    const grid = section.querySelector(".palette-category-grid");
    members.forEach((template) => {
      const item = document.createElement("div");
      item.className = "palette-item";
      item.draggable = true;
      item.dataset.templateId = template.template_id;
      const modelKey = template.component_model || template.module_id;
      item.title = `${modelKey} · drag to flowsheet`;
      item.innerHTML = `${iconMarkup(modelKey)}<strong>${displayTemplateName(template)}</strong><span>${modelKey}</span>`;
      item.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/template-id", template.template_id));
      item.addEventListener("click", () => addComponent(template.template_id, 590, 350));
      grid.appendChild(item);
    });
    palette.appendChild(section);
  });
}

function edgeClass(edge) {
  const service = (edge.service || "").toLowerCase();
  let medium = "liquid";
  if (service.includes("vent") || service.includes("stack") || service.includes("relief")) medium = "vent";
  else if (service.includes("bog") || service.includes("gas") || service.includes("vapor")) medium = "vapor";
  return ["process-edge", medium, edge.enabled === false ? "disabled" : "", state.activeEdges.has(edge.id) ? "active" : "", state.selected?.type === "edge" && state.selected.id === edge.id ? "selected" : ""].filter(Boolean).join(" ");
}

function nodeSize(node) {
  if (node.kind === "tank") return [132, 56];
  if (node.kind === "receiver") return [124, 54];
  if (node.kind === "header") return [104, 30];
  if (node.kind === "boundary") return [116, 42];
  if (node.kind === "vent") return [62, 62];
  return [104, 48];
}

function canvasLabel(node) {
  const compact = {
    "TRUCK-LH2-BOUNDARY": "TRUCK-LH2",
    "LH2-DEMAND-BOUNDARY": "LH2-DEMAND",
    "GH2-SUPPLY-BOUNDARY": "GH2-SUPPLY",
    "RELIQUEFACTION-RETURN-HEADER": "RELQ-RETURN",
    "VIRTUAL-LH2-PUMP-01": "LH2-PUMP-01",
  };
  return compact[node.label] || node.label;
}

function connectorPoint(node, toward) {
  const [w, h] = nodeSize(node);
  const dx = toward.x - node.x;
  const dy = toward.y - node.y;
  if (Math.abs(dx / w) >= Math.abs(dy / h)) return { x: node.x + Math.sign(dx || 1) * w / 2, y: node.y };
  return { x: node.x, y: node.y + Math.sign(dy || 1) * h / 2 };
}

function roundedOrthogonalPath(points, radius = 9) {
  if (points.length < 2) return "";
  if (points.length === 2) return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const prev = points[i - 1], current = points[i], next = points[i + 1];
    const inLength = Math.hypot(current.x - prev.x, current.y - prev.y);
    const outLength = Math.hypot(next.x - current.x, next.y - current.y);
    const r = Math.min(radius, inLength / 2, outLength / 2);
    const before = {
      x: current.x - (current.x - prev.x) / (inLength || 1) * r,
      y: current.y - (current.y - prev.y) / (inLength || 1) * r,
    };
    const after = {
      x: current.x + (next.x - current.x) / (outLength || 1) * r,
      y: current.y + (next.y - current.y) / (outLength || 1) * r,
    };
    d += ` L ${before.x} ${before.y} Q ${current.x} ${current.y} ${after.x} ${after.y}`;
  }
  const last = points.at(-1);
  return `${d} L ${last.x} ${last.y}`;
}

function pointAlongPolyline(points, fraction = .5) {
  const segments = points.slice(1).map((point, index) => ({
    a: points[index], b: point,
    length: Math.hypot(point.x - points[index].x, point.y - points[index].y),
  }));
  const target = segments.reduce((sum, item) => sum + item.length, 0) * fraction;
  let traversed = 0;
  for (const segment of segments) {
    if (traversed + segment.length >= target) {
      const ratio = (target - traversed) / (segment.length || 1);
      return {
        x: segment.a.x + (segment.b.x - segment.a.x) * ratio,
        y: segment.a.y + (segment.b.y - segment.a.y) * ratio,
        angle: Math.atan2(segment.b.y - segment.a.y, segment.b.x - segment.a.x) * 180 / Math.PI,
      };
    }
    traversed += segment.length;
  }
  return { ...points.at(-1), angle: 0 };
}

function pathForEdge(edge) {
  const source = state.design.nodes.find((node) => node.id === edge.source);
  const target = state.design.nodes.find((node) => node.id === edge.target);
  if (!source || !target) return null;
  if (Array.isArray(edge.route) && edge.route.length >= 2) {
    const points = edge.route.map(([x, y]) => ({ x: Number(x), y: Number(y) }));
    points[0] = connectorPoint(source, points[1]);
    points[points.length - 1] = connectorPoint(target, points[points.length - 2]);
    return { d: roundedOrthogonalPath(points), points, mid: pointAlongPolyline(points) };
  }
  const a = connectorPoint(source, target), b = connectorPoint(target, source);
  const points = [a, { x: (a.x + b.x) / 2, y: a.y }, { x: (a.x + b.x) / 2, y: b.y }, b];
  return { d: roundedOrthogonalPath(points), points, mid: pointAlongPolyline(points) };
}

function renderZones() {
  const layer = $("#zoneLayer");
  layer.innerHTML = "";
  const zones = [
    { x: 20, y: 100, w: 1100, h: 420, title: "가상 LH2 공정", subtitle: "공개용 가상 입력 · 지정 경계유량 예제", alt: false },
  ];
  zones.forEach((zone) => {
    const x = zone.x * COORDINATE_SCALE, y = zone.y * COORDINATE_SCALE;
    layer.appendChild(svg("rect", { x, y, width: zone.w * COORDINATE_SCALE, height: zone.h * COORDINATE_SCALE, class: `process-zone ${zone.alt ? "alt" : ""}` }));
    layer.appendChild(svg("text", { x: x + 13, y: y + 18, class: "zone-title" }, zone.title));
    layer.appendChild(svg("text", { x: x + 13, y: y + 33, class: "zone-subtitle" }, zone.subtitle));
  });
}

function renderEdges() {
  const layer = $("#edgeLayer");
  layer.innerHTML = "";
  state.design.edges.forEach((edge) => {
    const points = pathForEdge(edge);
    if (!points) return;
    const group = svg("g", { "data-edge-id": edge.id });
    const path = svg("path", { d: points.d, class: edgeClass(edge) });
    path.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      state.selected = { type: "edge", id: edge.id };
      render();
      if(state.jobId) activateTab('results');
    });
    group.appendChild(path);
    if (edge.show_valve) {
      const valve = svg("g", { transform: `translate(${points.mid.x} ${points.mid.y}) rotate(${points.mid.angle || 0})`, style: `color:${state.activeEdges.has(edge.id) ? "#e6523c" : "#6d8fa2"}` });
      valve.append(svg("path", { d: "M -8 -6 L 0 0 L -8 6 Z M 8 -6 L 0 0 L 8 6 Z", class: "valve-symbol" }));
      group.appendChild(valve);
    }
    group.appendChild(svg("text", { x: points.mid.x, y: points.mid.y - 9, class: "edge-label", "text-anchor": "middle" }, edge.id));
    layer.appendChild(group);
  });
}

function appendNodeShape(group, node, w, h) {
  if (node.kind === "tank" || node.kind === "receiver") {
    group.appendChild(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: h / 2, class: "node-shape" }));
    group.appendChild(svg("path", { d: `M ${-w * .3} ${-h * .34} V ${h * .34} M ${w * .3} ${-h * .34} V ${h * .34}`, stroke: "#b5d4df", "stroke-width": 1.4 }));
  } else if (node.kind === "pump") {
    group.appendChild(svg("circle", { cx: 0, cy: 0, r: 23, class: "node-shape" }));
    group.appendChild(svg("path", { d: "M -7 -10 L 12 0 L -7 10 Z", fill: "#74c7d8", stroke: "#24728c" }));
  } else if (node.kind === "compressor") {
    group.appendChild(svg("circle", { cx: 0, cy: 0, r: 23, class: "node-shape" }));
    group.appendChild(svg("path", { d: "M -10 -12 V 12 L 12 6 V -6 Z", fill: "#a8c8e2", stroke: "#17649a", "stroke-width": 1.5 }));
    group.appendChild(svg("text", { x: 0, y: 4, "text-anchor": "middle", fill: "#0f4c78", "font-size": 10, "font-weight": 800 }, "C"));
  } else if (node.kind === "vaporizer") {
    group.appendChild(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 7, class: "node-shape" }));
    group.appendChild(svg("path", { d: `M ${-w * .37} 10 C ${-w * .24} -15, ${-w * .12} 18, 0 -8 S ${w * .23} 18, ${w * .38} -9`, fill: "none", stroke: "#2fa4bf", "stroke-width": 2 }));
  } else if (node.kind === "reliquefier") {
    group.appendChild(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 7, class: "node-shape" }));
    group.appendChild(svg("text", { x: 0, y: 6, "text-anchor": "middle", fill: "#2b83a3", "font-size": 24 }, "❄"));
  } else if (node.kind === "vent") {
    group.appendChild(svg("path", { d: "M -14 24 L 14 24 L 9 -21 L -9 -21 Z", class: "node-shape" }));
    group.appendChild(svg("path", { d: "M -17 -21 H 17 M -20 -26 H 20", stroke: "#7e58a4", "stroke-width": 2.5 }));
  } else if (node.kind === "header") {
    group.appendChild(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 17, class: "node-shape" }));
    group.appendChild(svg("path", { d: `M ${-w / 2 + 12} 0 H ${w / 2 - 12}`, stroke: "#2782aa", "stroke-width": 4 }));
  } else if (node.kind === "boundary") {
    group.appendChild(svg("path", { d: `M ${-w / 2 + 13} ${-h / 2} H ${w / 2} V ${h / 2} H ${-w / 2 + 13} L ${-w / 2} 0 Z`, class: "node-shape" }));
  } else {
    group.appendChild(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 7, class: "node-shape" }));
  }
  if (!["vent", "boundary", "header"].includes(node.kind)) {
    const highlight = node.kind === "tank" || node.kind === "receiver"
      ? `M ${-w * .34} ${-h * .26} C ${-w * .08} ${-h * .46}, ${w * .18} ${-h * .38}, ${w * .34} ${-h * .18}`
      : `M ${-w * .28} ${-h * .22} L ${w * .20} ${-h * .22}`;
    group.appendChild(svg("path", { d: highlight, class: "node-highlight", fill: "none", stroke: "#fff", "stroke-width": 3, opacity: .72, "stroke-linecap": "round" }));
  }
}

function renderNodes() {
  const layer = $("#nodeLayer");
  layer.innerHTML = "";
  state.design.nodes.forEach((node) => {
    const [w, h] = nodeSize(node);
    const classes = ["process-node", `node-${node.kind}`, state.selected?.type === "node" && state.selected.id === node.id ? "selected" : ""].filter(Boolean).join(" ");
    const group = svg("g", { class: classes, transform: `translate(${node.x} ${node.y})`, "data-node-id": node.id, tabindex: 0 });
    appendNodeShape(group, node, w, h);
    const labelY = node.kind === "vent" ? 36 : (["pump", "compressor"].includes(node.kind) ? 36 : -2);
    const visibleLabel = canvasLabel(node);
    const fontSize = visibleLabel.length > 18 ? 8.5 : (visibleLabel.length > 13 ? 9.5 : 11);
    group.appendChild(svg("text", { x: 0, y: labelY, class: "node-tag", style: `font-size:${fontSize}px` }, visibleLabel));
    if (!["vent", "pump", "compressor"].includes(node.kind)) group.appendChild(svg("text", { x: 0, y: labelY + 13, class: "node-desc" }, node.description || node.component_model || node.kind));
    if (state.nodeValues[node.id]) group.appendChild(svg("text", { x: 0, y: h / 2 + 16, class: "node-value" }, state.nodeValues[node.id]));
    group.appendChild(svg("circle", { cx: -w / 2, cy: 0, r: 3.5, class: "port-dot" }));
    group.appendChild(svg("circle", { cx: w / 2, cy: 0, r: 3.5, class: "port-dot" }));
    group.addEventListener("pointerdown", (event) => onNodePointerDown(event, node));
    group.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.connectionMode) handleConnectionNode(node);
      else { state.selected = { type: "node", id: node.id }; render(); if(state.jobId) activateTab('results'); }
    });
    layer.appendChild(group);
  });
}

function render() {
  renderZones();
  renderEdges();
  renderNodes();
  renderInspector();
  if(state.design) updateMonitor();
  $("#designMeta").textContent = `${state.design.nodes.length} nodes · ${state.design.edges.length} connections`;
  $("#processCanvas").setAttribute("viewBox", `${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.width} ${state.viewBox.height}`);
  $("#zoomStatus").textContent = `${Math.round(100 / state.zoomFactor)}%`;
  const cursor = state.selected ? `${state.selected.type === "node" ? "설비" : "연결"}: ${state.selected.id}` : "선택 없음";
  $("#cursorStatus").textContent = cursor;
}

function screenToSvg(clientX, clientY) {
  const point = $("#processCanvas").createSVGPoint();
  point.x = clientX; point.y = clientY;
  return point.matrixTransform($("#processCanvas").getScreenCTM().inverse());
}

function onNodePointerDown(event, node) {
  if (state.connectionMode || event.button !== 0) return;
  event.stopPropagation();
  const p = screenToSvg(event.clientX, event.clientY);
  state.drag = { node, dx: p.x - node.x, dy: p.y - node.y, pointerId: event.pointerId };
  event.currentTarget.setPointerCapture(event.pointerId);
}

function onPointerMove(event) {
  if (!state.drag) return;
  const p = screenToSvg(event.clientX, event.clientY);
  state.drag.node.x = Math.round((p.x - state.drag.dx) / 10) * 10;
  state.drag.node.y = Math.round((p.y - state.drag.dy) / 10) * 10;
  renderEdges(); renderNodes();
}

function onPointerUp() {
  if (state.drag) { state.selected = { type: "node", id: state.drag.node.id }; state.drag = null; render(); persistLocal(); }
}

function templateById(templateId) { return state.bootstrap.library.templates.find((item) => item.template_id === templateId); }

function kindForModel(model) {
  model = String(model || "module");
  if (model.includes("tank")) return "tank";
  if (model.includes("pump")) return "pump";
  if (model.includes("compressor")) return "compressor";
  if (model.includes("vaporizer")) return "vaporizer";
  if (model.includes("reliquefier")) return "reliquefier";
  if (model.includes("vent")) return "vent";
  if (model.includes("valve")) return "valve";
  return "pipe";
}

function uniqueId(base) {
  let id = base.toUpperCase().replace(/[^0-9A-Z가-힣-]/g, "-");
  let n = 1;
  while (state.design.nodes.some((node) => node.id === id)) id = `${base}-${++n}`;
  return id;
}

function addComponent(templateId, x, y) {
  const template = templateById(templateId);
  const modelKey = template.component_model || template.module_id;
  const id = uniqueId(template.template_id.split("-").map((p) => p[0]).join("") || "COMP");
  state.design.nodes.push({
    id, label: id, description: displayTemplateName(template), kind: template.kind || kindForModel(modelKey),
    template_id: template.template_id, component_model: template.component_model || null,
    module_id: template.module_id || null, capacity_scale: 1,
    scalable: Object.keys(template.scale_exponents || {}).length > 0,
    parameters: clone(template.base_parameters), initial_conditions: clone(template.initial_conditions || { enabled: true }), ports: clone(template.ports), x, y, locked: false,
  });
  state.selected = { type: "node", id };
  render(); persistLocal(); toast(`${displayTemplateName(template)} 추가`);
}

function nextEdgeId() {
  let n = 1; let id;
  do { id = `C${String(n++).padStart(3, "0")}`; } while (state.design.edges.some((edge) => edge.id === id));
  return id;
}

function handleConnectionNode(node) {
  if (!state.connectionSource) {
    state.connectionSource = node.id;
    showModeHint(`시작점 ${node.id} 선택됨 · 연결할 대상 설비를 선택하세요`);
    return;
  }
  if (state.connectionSource === node.id) { toast("같은 설비에는 연결할 수 없습니다"); return; }
  const edge = { id: nextEdgeId(), source: state.connectionSource, target: node.id, service: "사용자 정의 수소 공정 연결", enabled: true, show_valve: true };
  state.design.edges.push(edge);
  state.connectionSource = null;
  state.connectionMode = false;
  $("#connectBtn").classList.remove("primary");
  hideModeHint();
  state.selected = { type: "edge", id: edge.id };
  render(); persistLocal(); toast(`${edge.source} → ${edge.target} 연결`);
}

function showModeHint(message) { $("#modeHint").textContent = message; $("#modeHint").classList.remove("hidden"); }
function hideModeHint() { $("#modeHint").classList.add("hidden"); }

function selectedNode() { return state.selected?.type === "node" ? state.design.nodes.find((n) => n.id === state.selected.id) : null; }
function selectedEdge() { return state.selected?.type === "edge" ? state.design.edges.find((e) => e.id === state.selected.id) : null; }

function renderInspector() {
  const form = $("#propertyForm");
  const empty = $("#emptyInspector");
  const node = selectedNode();
  const edge = selectedEdge();
  if (!node && !edge) { form.classList.add("hidden"); empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden"); form.classList.remove("hidden");
  if (edge) {
    form.innerHTML = `<span class="model-chip">PROCESS CONNECTION</span><h3>${edge.id}</h3>
      <div class="field"><label>연결 ID</label><input data-edge="id" value="${edge.id}"></div>
      <div class="field-row"><div class="field"><label>시작</label><input value="${edge.source}" disabled></div><div class="field"><label>도착</label><input value="${edge.target}" disabled></div></div>
      <div class="field"><label>서비스</label><input data-edge="service" value="${edge.service || ""}"></div>
      <div class="field"><label>상태</label><select data-edge="enabled"><option value="true" ${edge.enabled !== false ? "selected" : ""}>활성</option><option value="false" ${edge.enabled === false ? "selected" : ""}>비활성</option></select></div>
      <p class="parameter-help">간선 중앙의 밸브 기호는 공정 차단점을 나타냅니다. 삭제하면 연결만 제거되고 설비는 유지됩니다.</p>`;
    form.querySelectorAll("[data-edge]").forEach((input) => input.addEventListener("change", () => {
      const field = input.dataset.edge;
      if (field === "enabled") edge.enabled = input.value === "true";
      else if (field === "id") {
        if (!input.value || state.design.edges.some((e) => e !== edge && e.id === input.value)) { toast("연결 ID는 고유해야 합니다"); input.value = edge.id; return; }
        edge.id = input.value; state.selected.id = edge.id;
      } else edge[field] = input.value;
      render(); persistLocal();
    }));
    return;
  }
  const fields = Object.entries(node.parameters || {}).map(([key, value]) => editableFieldMarkup("data-param", key, value, parameterLabel(key))).join("");
  const initialFields = Object.entries(node.initial_conditions || {}).map(([key, value]) => editableFieldMarkup("data-initial", key, value, INITIAL_CONDITION_LABELS[key] || key)).join("");
  form.innerHTML = `<span class="model-chip">${node.component_model || node.module_id || node.kind.toUpperCase()}</span><h3>${node.label}</h3>
    <div class="field"><label>설비 태그</label><input data-node="label" value="${node.label}"></div>
    <div class="field"><label>설명</label><input data-node="description" value="${node.description || ""}"></div>
    <div class="field-row"><div class="field"><label>X</label><input data-node="x" type="number" value="${node.x}"></div><div class="field"><label>Y</label><input data-node="y" type="number" value="${node.y}"></div></div>
    ${node.scalable ? `<div class="field"><label>용량 배율</label><input id="capacityScaleInput" type="number" min="0.01" step="0.05" value="${node.capacity_scale || 1}"></div>` : ""}
    <div class="parameter-group initial-condition-group"><div class="group-heading"><strong>초기 상태·운전값</strong><button id="resetInitialBtn" type="button">기본값</button></div>${initialFields}</div>
    ${fields ? `<div class="parameter-group"><strong>물리 파라미터</strong>${fields}</div>` : "<p class='parameter-help'>이 노드는 공정 경계 또는 헤더입니다. 직접 수치 부품을 갖지 않습니다.</p>"}
    ${node.template_id ? `<p class="parameter-help">템플릿: ${node.template_id}<br>초기 상태는 설계 파일에 저장되며 지원 시나리오와 탱크 미리보기에 전달됩니다. 용량 배율은 선언된 기하·열 스케일 법칙으로 파라미터를 다시 계산합니다.</p>` : "<p class='parameter-help'>경계·헤더 초기값은 연결망의 시작 상태와 경계조건으로 사용됩니다.</p>"}`;
  form.querySelectorAll("[data-node]").forEach((input) => input.addEventListener("change", () => {
    const key = input.dataset.node;
    node[key] = ["x", "y"].includes(key) ? Number(input.value) : input.value;
    render(); persistLocal();
  }));
  form.querySelectorAll("[data-param]").forEach((input) => input.addEventListener("change", () => {
    try {
      const type = input.dataset.valueType;
      const displayValue = type === "json" ? JSON.parse(input.value) : (type === "boolean" ? input.value === "true" : (type === "string" ? input.value : readDisplayNumber(input.value)));
      node.parameters[input.dataset.param] = convertFieldValue(input.dataset.param,displayValue,'si');
      persistLocal(); render();
    } catch (error) { toast(`${input.dataset.param}: ${error.message}`, 4000); }
  }));
  form.querySelectorAll("[data-initial]").forEach((input) => input.addEventListener("change", () => {
    try {
      const type = input.dataset.valueType;
      const displayValue = type === "json" ? JSON.parse(input.value) : (type === "boolean" ? input.value === "true" : (type === "string" ? input.value : readDisplayNumber(input.value)));
      node.initial_conditions[input.dataset.initial] = convertFieldValue(input.dataset.initial,displayValue,'si');
      persistLocal(); render();
    } catch (error) { toast(`${input.dataset.initial}: 입력 형식 오류`, 4000); }
  }));
  $("#resetInitialBtn")?.addEventListener("click", () => {
    node.initial_conditions = defaultInitialConditions(node); persistLocal(); render(); toast(`${node.id} 초기값을 기본값으로 복원했습니다`);
  });
  $("#capacityScaleInput")?.addEventListener("change", (event) => applyCapacityScale(node, Number(event.target.value)));
}

function applyCapacityScale(node, scale) {
  if (!(scale > 0)) { toast("용량 배율은 0보다 커야 합니다"); render(); return; }
  const template = templateById(node.template_id);
  node.capacity_scale = scale;
  node.parameters = clone(template.base_parameters);
  Object.entries(template.scale_exponents || {}).forEach(([key, exponent]) => {
    node.parameters[key] = Number(template.base_parameters[key]) * scale ** Number(exponent);
  });
  render(); persistLocal(); toast(`용량 ${scale.toFixed(2)}배 적용`);
}

function deleteSelected() {
  if (!state.selected) return;
  if (state.selected.type === "edge") state.design.edges = state.design.edges.filter((edge) => edge.id !== state.selected.id);
  else {
    state.design.nodes = state.design.nodes.filter((node) => node.id !== state.selected.id);
    state.design.edges = state.design.edges.filter((edge) => edge.source !== state.selected.id && edge.target !== state.selected.id);
  }
  state.selected = null; render(); persistLocal(); toast("삭제했습니다");
}

function duplicateSelected() {
  const source = selectedNode();
  if (!source) { toast("복제할 설비를 선택하세요"); return; }
  const copy = clone(source); copy.id = uniqueId(`${source.id}-COPY`); copy.label = copy.id; copy.x += 35; copy.y += 35;
  state.design.nodes.push(copy); state.selected = { type: "node", id: copy.id }; render(); persistLocal(); toast(`${source.id} 복제`);
}

function persistLocal() { state.design.display_units={...unitPreferences};localStorage.setItem("lh2-public-authoring-design", JSON.stringify(state.design)); }

async function validate() {
  try {
    const result = await api("/api/validate", { method: "POST", body: JSON.stringify({ design: state.design }) });
    $("#auditReady").textContent = `${result.ready_computational_block_count}/${result.computational_block_count}`;
    $("#auditEdges").textContent = result.edge_count;
    const target = $("#auditMessages"); target.innerHTML = "";
    if (!result.issues.length) target.innerHTML += `<div class="audit-message ok">계산 객체 검증 통과 · factory 유닛 ${result.ready_component_count}개 · 보조 모듈 ${result.ready_auxiliary_module_count}개</div>`;
    result.issues.forEach((message) => target.innerHTML += `<div class="audit-message error">${message}</div>`);
    result.warnings.slice(0, 6).forEach((message) => target.innerHTML += `<div class="audit-message">${message}</div>`);
    if (result.warnings.length > 6) target.innerHTML += `<div class="audit-message">경계/헤더 경고 ${result.warnings.length - 6}건 더 있음</div>`;
    return result;
  } catch (error) { toast(error.message); throw error; }
}

function activateTab(name) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".tab-content").forEach((tab) => tab.classList.toggle("active", tab.id === `${name}Tab`));
  if(name==='results') requestAnimationFrame(()=>{if(state.design) updateMonitor();});
}

function formatNumber(value, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  if (Math.abs(number) >= 1e5 || (Math.abs(number) > 0 && Math.abs(number) < 1e-3)) return number.toExponential(2);
  return number.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function extractTrajectory(result) {
  if (Array.isArray(result.trajectory)) return result.trajectory.map((row) => ({ time: row.time_s, pressure: row.tank_pressure_Pa || row.pressure_Pa, mass: row.tank_mass_kg || row.mass_kg, flow: row.liquid_supply_mass_flow_kg_s || row.gas_supply_mass_flow_kg_s || row.vent_mass_flow_kg_s || row.pump_mass_flow_kg_s }));
  if (result.series) {
    const [id, rows] = Object.entries(result.series)[0];
    return rows.map((row) => ({ time: row.time_s, pressure: row.pressure_Pa, mass: row.mass_kg, id }));
  }
  return [];
}

function drawChart(rows, field='pressure_Pa') {
  renderProcessChart($('#resultChart'),rows,field,$('#monitorTarget').value);
}

function showResult(result, title) {
  if (result.series) state.telemetry=Object.fromEntries(Object.entries(result.series).map(([id,rows])=>['node:'+id,rows.map(row=>({...row,mass_flow_kg_s:row.mass_flow_kg_s??0}))]));
  activateTab("results");
  $("#resultTitle").textContent = title;
  $("#solverBadge").textContent = result.network_solver_success === false ? "실패" : "정상";
  $("#solverBadge").className = `badge ${result.network_solver_success === false ? "neutral" : "success"}`;
  const rows = extractTrajectory(result); drawChart(rows.map(r=>({...r,pressure_Pa:r.pressure})));
  const last = rows.at(-1) || {};
  const metrics = [
    [last.pressure ? pressureValueText(last.pressure) : "—", "최종 탱크 압력"],
    [last.mass ? `${monitorValue(toDisplayValue("mass_kg",last.mass))} ${displayUnit("mass_kg").label}` : "—", "최종 수소 질량"],
    [result.max_abs_dynamic_mass_balance_residual_kg ?? result.dynamic_mass_balance_residual_kg, "질량 보존 잔차 kg"],
    [result.max_abs_dynamic_energy_balance_residual_J ?? result.dynamic_energy_balance_residual_J, "에너지 보존 잔차 J"],
  ];
  $("#resultMetrics").innerHTML = metrics.map(([value, label]) => `<div class="metric"><strong>${typeof value === "number" ? formatNumber(value) : value}</strong><span>${label}</span></div>`).join("");
  const detail = { scenario_id: result.scenario_id, mode: result.ui_execution_mode || result.mode, model_family: result.model_family, solver_success: result.network_solver_success, phase_route: result.phase_route, link_count: result.link_count, initial_conditions_applied: result.ui_initial_conditions_applied, initial_condition_node_count: result.ui_initial_condition_node_count, process_conditions: result.ui_process_conditions, screening_parameters: result.screening_parameters, facility_binding_ready: result.facility_binding_ready, fitting_or_inverse_estimation: result.fitting_or_inverse_estimation };
  $("#resultDetails").textContent = JSON.stringify(detail, null, 2);
  state.nodeValues = {};
  if (result.final_pressure_Pa && typeof result.final_pressure_Pa === "object") Object.entries(result.final_pressure_Pa).forEach(([id, p]) => state.nodeValues[id] = pressureValueText(Number(p)));
  if (result.summaries) Object.entries(result.summaries).forEach(([id, item]) => state.nodeValues[id] = pressureValueText(Number(item.final_pressure_Pa)));
  if (last.id && last.pressure) state.nodeValues[last.id] = pressureValueText(last.pressure);
  renderNodes();
}

async function runScenario() {
  if(state.jobId && ['running','pausing','paused','cancelling'].includes(state.runStatus)) return;
  const generation=++state.runGeneration;
  state.telemetry={}; state.runStatus='running';
  $('#resultTitle').textContent=$('#scenarioSelect').selectedOptions[0].textContent;
  $('#resultDetails').textContent='현재 입력조건으로 계산 중입니다. 유닛 또는 라인을 선택해 추적하세요.';
  const scenarioId=$('#scenarioSelect').value;
  state.activeEdges=new Set(state.bootstrap.scenarios.find(s=>s.scenario_id===scenarioId).active_edges);
  renderEdges(); setBusy(true,'계산 중'); activateTab('results'); updateMonitor();
  try {
    const job=await api('/api/jobs',{method:'POST',body:JSON.stringify({scenario_id:scenarioId,design:clone(state.design)})});
    if(generation!==state.runGeneration) {await api(`/api/jobs/${job.job_id}`,{method:'POST',body:JSON.stringify({action:'cancel'})});return;}
    state.jobId=job.job_id; localStorage.setItem('lh2-public-active-run',JSON.stringify({id:job.job_id,scenarioId})); pollRun(generation);
  } catch(error) {state.runStatus='failed';setBusy(false);$('#solverBadge').textContent='오류';$('#resultDetails').textContent=error.message;toast(error.message,5000);}
}

async function pollRun(generation) {
  if(generation!==state.runGeneration||!state.jobId) return;
  try {
    const job=await api(`/api/jobs/${state.jobId}`);
    if(generation!==state.runGeneration) return;
    state.telemetry=job.series; state.initialRows=job.initial_rows||{}; state.runStatus=job.status;
    if(!state.selected) {
      const first=state.design.nodes.find(n=>job.series['node:'+n.id]?.length);
      if(first) state.selected={type:'node',id:first.id};
    }
    const live=['running','pausing','paused','cancelling'].includes(job.status);
    setBusy(live);
    updateRunControls();
    if(job.status==='completed') showResult(job.result,$('#scenarioSelect').selectedOptions[0].textContent);
    if(job.error) $('#resultDetails').textContent=job.error;
    $('#solverBadge').textContent=({running:'계산 중',pausing:'단계 완료 후 정지',paused:'일시정지',cancelling:'단계 완료 후 취소',completed:'완료',failed:'오류',cancelled:'취소'})[job.status];
    updateMonitor();
    if(live) state.pollTimer=setTimeout(()=>pollRun(generation),400);
  } catch(error) {if(generation===state.runGeneration){state.runStatus='failed';setBusy(false);updateRunControls();$('#resultDetails').textContent=error.message;}}
}

function updateRunControls() {
  $('#pauseRunBtn').disabled = !state.jobId || state.runStatus !== 'running';
  $('#resumeRunBtn').disabled = !state.jobId || !['paused','pausing'].includes(state.runStatus);
}

async function controlRun(action) {
  if (!state.jobId) return;
  if (action === 'pause' && state.runStatus !== 'running') return;
  if (action === 'resume' && !['paused','pausing'].includes(state.runStatus)) return;
  const generation = state.runGeneration;
  $('#pauseRunBtn').disabled = true;
  $('#resumeRunBtn').disabled = true;
  try {
    const job = await api(`/api/jobs/${state.jobId}`, {method:'POST',body:JSON.stringify({action})});
    if (generation !== state.runGeneration) return;
    state.runStatus = job.status;
    clearTimeout(state.pollTimer);
    await pollRun(generation);
  } catch (error) { toast(error.message); }
  finally { updateRunControls(); }
}

function updateMonitor() {
  renderMonitorPanel();
}

async function resetRun() {
  ++state.runGeneration;clearTimeout(state.pollTimer);
  if(state.jobId){try{await api(`/api/jobs/${state.jobId}`,{method:'POST',body:JSON.stringify({action:'cancel'})});}catch(error){toast(error.message);}}
  state.jobId=null;state.runStatus='idle';state.telemetry={};state.initialRows={};state.chartRange=null;state.nodeValues={};state.activeEdges=new Set();
  localStorage.removeItem('lh2-public-active-run');
  setBusy(false);updateRunControls();$('#solverBadge').textContent='대기';$('#resultTitle').textContent='실행 전';$('#resultDetails').textContent='입력 조건을 유지하고 계산 결과를 초기화했습니다.';render();updateMonitor();
}

function syncProcessDialog() {
  const conditions = state.design.process_conditions || DEFAULT_PROCESS_CONDITIONS;
  $("#durationInput").value = displayNumberInput(toDisplayValue("duration_s",conditions.duration_s));
  $("#stepInput").value = displayNumberInput(toDisplayValue("time_step_s",conditions.time_step_s));
  $("#ambientInput").value = displayNumberInput(toDisplayValue("ambient_temperature_K",conditions.ambient_temperature_K));
  for(const [id,key,label] of [['durationInput','duration_s','계산 시간'],['stepInput','time_step_s','시간 간격'],['ambientInput','ambient_temperature_K','외기 온도']]) {
    const input=$('#'+id);input.closest('label').firstChild.textContent=label+' ('+displayUnit(key).label+')';
    input.min=toDisplayValue(key,1e-6);input.step='any';
    if(key.endsWith('_s')) input.max=toDisplayValue(key,86400);
  }
  $("#processConditionsSummary").textContent = `${state.design.nodes.length}개 유닛 초기값은 개별 Input에 저장됩니다. 이 창은 계산시간·시간간격·외기온도만 변경합니다.`;
}

function applyProcessConditions({ close = true, applyTankFallbacks = false } = {}) {
  const duration = toSIValue("duration_s",readDisplayNumber($("#durationInput").value));
  const step = toSIValue("time_step_s",readDisplayNumber($("#stepInput").value));
  const ambient = toSIValue("ambient_temperature_K",readDisplayNumber($("#ambientInput").value));
  if (![duration,step,ambient].every(Number.isFinite) || !(duration >= 1e-6 && duration <= 86400) || !(step >= 1e-6) || step > duration || Math.ceil(duration/step)>10000 || !(ambient >= 1e-6)) throw new Error("계산시간·시간간격·외기온도를 확인하세요");
  state.design.process_conditions = { duration_s: duration, time_step_s: step, ambient_temperature_K: ambient };
  persistLocal();
  if (close) $("#simulationDialog").close();
  toast("공정조건을 설계에 적용했습니다");
}

async function runPreview() {
  state.telemetry={};
  setBusy(true, "해석 중"); activateTab("results");
  const body = {
    mode: "design_preview", design: state.design,
    ...state.design.process_conditions,
  };
  try {
    const result = await api("/api/simulate", { method: "POST", body: JSON.stringify(body) });
    state.activeEdges = new Set(); showResult(result, "편집 설계 열침입 미리보기"); updateMonitor(); toast("편집 파라미터 동적 계산 완료");
  } catch (error) { $("#resultDetails").textContent = error.message; toast(error.message, 5000); }
  finally { setBusy(false); }
}

function updateScenarioHighlight() {
  const scenario = state.bootstrap.scenarios.find((item) => item.scenario_id === $("#scenarioSelect").value);
  state.activeEdges = new Set(scenario.active_edges); renderEdges();
}

function resetDesign() {
  if(state.jobId) {toast("먼저 계산 초기화를 눌러주세요");return;}
  state.design = normalizeDesign(clone(state.bootstrap.design)); state.selected = null; state.nodeValues = {}; state.activeEdges = new Set(); fitView(); persistLocal(); render(); validate(); toast("가상 공정 기본 배치를 불러왔습니다");
}

function activateMenu(name) {
  $$(".menu-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.menu === name));
  if (name === "file") { $("#projectDialog").showModal(); return; }
  if (name === "home") { fitView(); toast("홈 화면에 맞췄습니다"); return; }
  if (name === "flowsheet") { activateTab("properties"); $("#paletteSearch").focus(); toast("팔레트에서 유닛을 끌어 배치할 수 있습니다"); return; }
  if (name === "properties") { activateTab("properties"); if (!state.selected) toast("유닛 또는 연결선을 선택하세요"); return; }
  if (name === "simulation") { syncProcessDialog(); $("#simulationDialog").showModal(); return; }
  if (name === "results") activateTab("results");
}

function fittedViewBox() {
  const viewport = $("#canvasViewport");
  const pixelWidth = Math.max(1, viewport.clientWidth);
  const pixelHeight = Math.max(1, viewport.clientHeight);
  const viewportAspect = pixelWidth / pixelHeight;
  let width = CANVAS_WIDTH;
  let height = CANVAS_HEIGHT;
  if (width / height < viewportAspect) width = height * viewportAspect;
  else height = width / viewportAspect;

  // Limit large-monitor magnification to about 1.1 CSS pixels per SVG unit.
  // This keeps equipment readable and avoids the oversized 4K-screen layout.
  const naturalMagnification = Math.min(pixelWidth / width, pixelHeight / height);
  if (naturalMagnification > 1) {
    const controlledMagnification = naturalMagnification / MAX_FIT_DISPLAY_SCALE;
    width *= controlledMagnification;
    height *= controlledMagnification;
  }
  width *= state.zoomFactor;
  height *= state.zoomFactor;
  return {
    x: (CANVAS_WIDTH - width) / 2,
    y: (CANVAS_HEIGHT - height) / 2,
    width,
    height,
  };
}

function syncResponsiveViewBox() {
  if (!$("#canvasViewport")?.clientWidth) return;
  state.viewBox = fittedViewBox();
  $("#processCanvas").setAttribute("viewBox", `${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.width} ${state.viewBox.height}`);
  $("#zoomStatus").textContent = `${Math.round(100 / state.zoomFactor)}%`;
}

function fitView() {
  state.zoomFactor = 1;
  syncResponsiveViewBox();
}

function zoom(factor) {
  state.zoomFactor = Math.max(0.42, Math.min(2.8, state.zoomFactor * factor));
  syncResponsiveViewBox();
}

function normalizedPanelLayout(layout) {
  let paletteRatio = Math.max(0.09, Math.min(0.26, Number(layout.paletteRatio) || DEFAULT_PANEL_LAYOUT.paletteRatio));
  let inspectorRatio = Math.max(0.12, Math.min(0.32, Number(layout.inspectorRatio) || DEFAULT_PANEL_LAYOUT.inspectorRatio));
  const maximumSideShare = 0.48;
  if (paletteRatio + inspectorRatio > maximumSideShare) {
    const scale = maximumSideShare / (paletteRatio + inspectorRatio);
    paletteRatio *= scale;
    inspectorRatio *= scale;
  }
  return { paletteRatio, inspectorRatio };
}

function applyPanelLayout(persist = false) {
  state.panelLayout = normalizedPanelLayout(state.panelLayout);
  const workspace = $(".workspace");
  workspace.style.setProperty("--palette-share", `${state.panelLayout.paletteRatio * 100}%`);
  workspace.style.setProperty("--inspector-share", `${state.panelLayout.inspectorRatio * 100}%`);
  if (persist) localStorage.setItem("lh2-panel-layout", JSON.stringify(state.panelLayout));
}

function resizePanel(side, clientX) {
  const workspace = $(".workspace");
  const bounds = workspace.getBoundingClientRect();
  if (!bounds.width) return;
  if (side === "palette") {
    state.panelLayout.paletteRatio = (clientX - bounds.left) / bounds.width;
  } else {
    state.panelLayout.inspectorRatio = (bounds.right - clientX) / bounds.width;
  }
  applyPanelLayout(false);
}

function bindPaneResizer(element, side) {
  const finish = (event) => {
    element.classList.remove("dragging");
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", finish);
    document.removeEventListener("pointercancel", finish);
    applyPanelLayout(true);
    syncResponsiveViewBox();
    if (event?.pointerId !== undefined && element.hasPointerCapture?.(event.pointerId)) element.releasePointerCapture(event.pointerId);
  };
  const move = (event) => {
    resizePanel(side, event.clientX);
    syncResponsiveViewBox();
  };
  element.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    element.classList.add("dragging");
    element.setPointerCapture?.(event.pointerId);
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", finish);
    document.addEventListener("pointercancel", finish);
  });
  element.addEventListener("dblclick", () => {
    state.panelLayout = { ...DEFAULT_PANEL_LAYOUT };
    applyPanelLayout(true);
    syncResponsiveViewBox();
    toast("패널 비율을 초기화했습니다");
  });
  element.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const delta = direction * 0.01 * (side === "palette" ? 1 : -1);
    const key = side === "palette" ? "paletteRatio" : "inspectorRatio";
    state.panelLayout[key] += delta;
    applyPanelLayout(true);
    syncResponsiveViewBox();
  });
}

async function saveDesign() {
  const name = state.design.design_id || "lh2_design";
  try { const result = await api("/api/save", { method: "POST", body: JSON.stringify({ name, design: state.design }) }); toast(`저장 완료: ${result.name}`); await validate(); }
  catch (error) { toast(error.message, 5000); }
}

function downloadDesign() {
  const blob = new Blob([JSON.stringify(state.design, null, 2)], { type: "application/json" });
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${state.design.design_id || "lh2_design"}.json`; link.click(); URL.revokeObjectURL(link.href);
}

async function loadFile(file) {
  try {
    const value = JSON.parse(await file.text());
    if (!Array.isArray(value.nodes) || !Array.isArray(value.edges)) throw new Error("설계 JSON에 nodes/edges가 없습니다");
    state.design = normalizeDesign(value); if(value.display_units) unitPreferences=validUnitPreferences(value.display_units); state.selected = null; state.nodeValues = {}; persistLocal(); render(); await validate(); toast("설계를 불러왔습니다");
  } catch (error) { toast(error.message, 5000); }
}

function bindEvents() {
  bindPaneResizer($("#paletteResizer"), "palette");
  bindPaneResizer($("#inspectorResizer"), "inspector");
  const viewportObserver = new ResizeObserver(() => requestAnimationFrame(syncResponsiveViewBox));
  viewportObserver.observe($("#canvasViewport"));
  $("#paletteSearch").addEventListener("input", (event) => renderPalette(event.target.value));
  $("#processCanvas").addEventListener("pointermove", onPointerMove);
  $("#processCanvas").addEventListener("pointerup", onPointerUp);
  $("#processCanvas").addEventListener("pointercancel", onPointerUp);
  $("#processCanvas").addEventListener("pointerdown", (event) => { if (event.target.id === "processCanvas" || event.target.tagName === "rect" && !event.target.closest(".process-node")) { state.selected = null; render(); } });
  $("#canvasViewport").addEventListener("dragover", (event) => event.preventDefault());
  $("#canvasViewport").addEventListener("drop", (event) => { event.preventDefault(); const id = event.dataTransfer.getData("text/template-id"); if (id) { const p = screenToSvg(event.clientX, event.clientY); addComponent(id, p.x, p.y); } });
  $("#connectBtn").addEventListener("click", () => { state.connectionMode = !state.connectionMode; state.connectionSource = null; $("#connectBtn").classList.toggle("primary", state.connectionMode); state.connectionMode ? showModeHint("연결 시작 설비를 선택하세요") : hideModeHint(); });
  $("#deleteBtn").addEventListener("click", deleteSelected);
  $("#duplicateBtn").addEventListener("click", duplicateSelected);
  $("#newBtn").addEventListener("click", resetDesign);
  $("#saveBtn").addEventListener("click", saveDesign);
  $("#downloadBtn").addEventListener("click", downloadDesign);
  $("#uploadInput").addEventListener("change", (event) => event.target.files[0] && loadFile(event.target.files[0]));
  $("#fitBtn").addEventListener("click", fitView); $("#zoomInBtn").addEventListener("click", () => zoom(.8)); $("#zoomOutBtn").addEventListener("click", () => zoom(1.25));
  $("#view3dBtn").addEventListener("click", () => {
    state.iconMode = state.iconMode === "3d" ? "2d" : "3d";
    localStorage.setItem("lh2-icon-mode", state.iconMode);
    applyIconMode(); renderPalette($("#paletteSearch").value); renderNodes();
    toast(state.iconMode === "3d" ? "입체형 공정 아이콘 보기" : "2D PFD 기호 보기");
  });
  $$(".menu-tab").forEach((tab) => tab.addEventListener("click", () => activateMenu(tab.dataset.menu)));
  $("#scenarioSelect").addEventListener("change", updateScenarioHighlight);
  $('#monitorTarget').addEventListener('change',()=>{const [type,...id]=$('#monitorTarget').value.split(':');state.selected={type,id:id.join(':')};updateMonitor();renderNodes();renderEdges();});
  $('#monitorVariable').addEventListener('change',()=>{state.chartRange=null;updateMonitor();});
  bindDetailedMonitor();
  bindUnitSettings();
  $('#resetRunBtn').addEventListener('click',resetRun);
  $('#pauseRunBtn').addEventListener('click',()=>controlRun('pause'));
  $('#resumeRunBtn').addEventListener('click',()=>controlRun('resume'));
  $("#runBtn").addEventListener("click", runScenario);
  $("#previewBtn").addEventListener("click", () => { syncProcessDialog(); $("#simulationDialog").showModal(); });
  $("#applyConditionsBtn").addEventListener("click", (event) => { event.preventDefault(); try { applyProcessConditions({ applyTankFallbacks: false }); } catch (error) { toast(error.message, 5000); } });
  $("#runPreviewConfirm").addEventListener("click", (event) => { event.preventDefault(); try { applyProcessConditions({ applyTankFallbacks: false }); runPreview(); } catch (error) { toast(error.message, 5000); } });
  $("#fileNewBtn").addEventListener("click", resetDesign);
  $("#fileSaveBtn").addEventListener("click", saveDesign);
  $("#fileExportBtn").addEventListener("click", downloadDesign);
  $("#fileImportBtn").addEventListener("click", () => $("#uploadInput").click());
  $("#validateBtn").addEventListener("click", validate);
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
  document.addEventListener("keydown", (event) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (event.key === "Delete") deleteSelected();
    if (event.key === "Escape") { state.connectionMode = false; state.connectionSource = null; hideModeHint(); $("#connectBtn").classList.remove("primary"); }
  });
}

async function initialize() {
  try {
    applyIconMode();
    applyPanelLayout(false);
    state.bootstrap = await api("/api/bootstrap");
    state.design = normalizeDesign(clone(state.bootstrap.design));
    try {
      const saved=JSON.parse(localStorage.getItem('lh2-public-authoring-design')||'null');
      if(Array.isArray(saved?.nodes)&&Array.isArray(saved?.edges)) state.design=normalizeDesign(saved);
    } catch (_) { toast('저장된 설계를 읽지 못해 가상 공정 기본 배치를 열었습니다'); }
    if(state.design.display_units) unitPreferences=validUnitPreferences(state.design.display_units);
    $("#serverStatus").textContent = "lh2dt 물리모델 연결됨";
    $("#librarySummary").textContent = `${state.bootstrap.library.unit_operation_template_count}개 유닛 템플릿 · ${state.bootstrap.model_coverage.catalog_model_count}개 factory 모델 · ${state.bootstrap.library.auxiliary_template_count}개 보조 모듈. 모든 입력은 Python 계산 객체로 검증됩니다.`;
    $("#designTitle").textContent = state.design.title;
    state.bootstrap.scenarios.forEach((scenario) => {
      const option = document.createElement("option"); option.value = scenario.scenario_id; option.textContent = scenario.label; $("#scenarioSelect").appendChild(option);
    });
    renderPalette(); bindEvents(); render(); updateMonitor(); requestAnimationFrame(fitView); await validate();
    try {
      const savedRun=JSON.parse(localStorage.getItem('lh2-public-active-run')||'null');
      if(savedRun?.id) {
        await api(`/api/jobs/${savedRun.id}`);
        state.jobId=savedRun.id; $('#scenarioSelect').value=savedRun.scenarioId;
        state.activeEdges=new Set(state.bootstrap.scenarios.find(s=>s.scenario_id===savedRun.scenarioId)?.active_edges||[]);
        renderEdges(); activateTab('results'); pollRun(++state.runGeneration);
      }
    } catch (_) { localStorage.removeItem('lh2-public-active-run'); }
  } catch (error) {
    $("#serverStatus").textContent = "서버 연결 실패";
    $("#serverStatus").previousElementSibling.style.background = "#e6523c";
    toast(error.message, 10000);
  }
}

initialize();
