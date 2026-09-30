(function () {
  "use strict";

  const CONFIG = Object.freeze({
    entryDuration: 360, exitDuration: 300, overlapDuration: 480, overlapExitDuration: 400,
    breadcrumbDuration: 160, reducedDuration: 80,
    rearmThreshold: 0.50, stableMs: 100,
    wheelIdleMs: 180, prepareNoticeMs: 100, dampingMs: 60, minScale: 0.15, maxScale: 32,
    desktopPadding: 32, compactPadding: 16, portalInset: 8,
  });
  const manifest = JSON.parse(document.getElementById("archer-manifest").textContent);
  const sources = JSON.parse(document.getElementById("archer-scenes").textContent);
  const diagram = document.getElementById("diagram");
  const underlays = document.getElementById("underlays");
  const stage = document.getElementById("stage");
  const portal = document.getElementById("portal");
  const shell = document.getElementById("shell");
  const title = document.getElementById("scene-title");
  const crumbs = document.getElementById("breadcrumbs");
  const list = document.getElementById("node-list");
  const details = document.getElementById("details");
  const diagnostics = document.getElementById("diagnostics");
  const diagnosticSummary = document.getElementById("diagnostic-summary");
  const diagnosticList = document.getElementById("diagnostic-list");
  const live = document.getElementById("live");
  const status = document.getElementById("status");
  const backButton = document.getElementById("back");
  const openButton = document.getElementById("open");
  const panSpeedInput = document.getElementById("pan-speed");
  const panZoomScaleInput = document.getElementById("pan-zoom-scale");
  const panSpeedValue = document.getElementById("pan-speed-value");
  const panZoomScaleValue = document.getElementById("pan-zoom-scale-value");
  const panEffective = document.getElementById("pan-effective");
  const entryThresholdInput = document.getElementById("entry-threshold");
  const exitThresholdInput = document.getElementById("exit-threshold");
  const entryThresholdValue = document.getElementById("entry-threshold-value");
  const exitThresholdValue = document.getElementById("exit-threshold-value");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const metrics = { startedAt: performance.now(), usableAt: null, preparations: [], transitions: [] };
  window.__ARCHER_METRICS__ = metrics;

  let current = manifest.rootSceneId;
  let history = [];
  let selected = null;
  let selectedPinned = false;
  let camera = { k: 1, tx: 0, ty: 0 };
  let targetCamera = { ...camera };
  let fitScale = 1;
  let entryFitScale = 1;
  let paintFrame = 0;
  let lastPaint = 0;
  let drag = null;
  let suppressClick = false;
  let pointers = new Map();
  let pinch = null;
  let wheelTime = -Infinity;
  let controlZoomTime = -Infinity;
  let gestureSerial = 0;
  let semanticSuppressedSerial = -1;
  let transition = null;
  let candidateState = { id: null, since: 0, serial: -1 };
  let exitSince = 0;
  let disarmed = new Set();
  let lastActivator = null;
  let queuedWheel = null;
  let zoomCandidate = null;
  let zoomingOut = false;
  let panTime = -Infinity;
  let panHandoffState = { sceneId: null, id: null, since: 0 };
  let queuedSemanticEntry = null;
  let longTaskCount = 0;
  const slowScenes = new Set();
  const navigationDefaults = Object.freeze({ speed: 48, zoomScale: 0.71 });
  const navigation = loadNavigationSettings();
  const semanticZoomDefaults = Object.freeze({ entryThreshold: 0.68, exitThreshold: 0.62 });
  const semanticZoom = loadSemanticZoomSettings();
  if ("PerformanceObserver" in window) {
    try {
      const observer = new PerformanceObserver((entries) => { longTaskCount += entries.getEntries().length; });
      observer.observe({ type: "longtask", buffered: true });
    } catch (_) { /* Long-task timing is optional outside Chromium. */ }
  }

  function activeScene() { return manifest.scenes[current]; }
  function sceneById(id) { return manifest.scenes[id]; }
  function recordMetric(collection, item) {
    collection.push(item);
    if (collection.length > 200) collection.shift();
  }
  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
  function loadNavigationSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem("archer:navigation:v1") || "null");
      return {
        speed: clamp(Number(saved && saved.speed) || navigationDefaults.speed, 8, 160),
        zoomScale: clamp(Number(saved && saved.zoomScale) || navigationDefaults.zoomScale, 0.25, 1),
      };
    } catch (_) { return { ...navigationDefaults }; }
  }
  function saveNavigationSettings() {
    try { localStorage.setItem("archer:navigation:v1", JSON.stringify(navigation)); }
    catch (_) { /* The offline viewer also works when storage is unavailable. */ }
  }
  function loadSemanticZoomSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem("archer:semantic-zoom:v1") || "null");
      return {
        entryThreshold: clamp(Number(saved && saved.entryThreshold) || semanticZoomDefaults.entryThreshold, 0.4, 1.2),
        exitThreshold: clamp(Number(saved && saved.exitThreshold) || semanticZoomDefaults.exitThreshold, 0.25, 1),
      };
    } catch (_) { return { ...semanticZoomDefaults }; }
  }
  function saveSemanticZoomSettings() {
    try { localStorage.setItem("archer:semantic-zoom:v1", JSON.stringify(semanticZoom)); }
    catch (_) { /* The offline viewer also works when storage is unavailable. */ }
  }
  function updateSemanticZoomUi() {
    entryThresholdInput.value = String(Math.round(semanticZoom.entryThreshold * 100));
    exitThresholdInput.value = String(Math.round(semanticZoom.exitThreshold * 100));
    entryThresholdValue.textContent = `${Math.round(semanticZoom.entryThreshold * 100)}%`;
    exitThresholdValue.textContent = `${Math.round(semanticZoom.exitThreshold * 100)}%`;
  }
  function effectivePanStep() {
    const zoomLevels = Math.max(0, Math.log2(targetCamera.k / fitScale));
    return navigation.speed * Math.pow(navigation.zoomScale, zoomLevels);
  }
  function updateNavigationUi() {
    panSpeedInput.value = String(navigation.speed);
    panZoomScaleInput.value = String(navigation.zoomScale);
    panSpeedValue.textContent = `${Math.round(navigation.speed)} px`;
    panZoomScaleValue.textContent = `${Math.round(navigation.zoomScale * 100)}%`;
    panEffective.textContent = `Effective arrow step: ${effectivePanStep().toFixed(1)} px`;
  }
  function smoothstep(value) { const u = clamp(value, 0, 1); return u * u * (3 - 2 * u); }
  function interval(progress, start, end) { return smoothstep((progress - start) / (end - start)); }
  function viewport() { return { w: diagram.clientWidth, h: diagram.clientHeight }; }
  function nodeByVisual(id, scene = activeScene()) { return scene.nodes.find((node) => node.visualId === id); }
  function transform(layer, value) {
    layer.style.transform = `matrix(${value.k},0,0,${value.k},${value.tx},${value.ty})`;
  }
  function interpolateCamera(a, b, progress, size = viewport()) {
    const q = smoothstep(progress);
    const ca = { x: (size.w / 2 - a.tx) / a.k, y: (size.h / 2 - a.ty) / a.k };
    const cb = { x: (size.w / 2 - b.tx) / b.k, y: (size.h / 2 - b.ty) / b.k };
    const k = Math.exp(Math.log(a.k) * (1 - q) + Math.log(b.k) * q);
    const center = { x: ca.x * (1 - q) + cb.x * q, y: ca.y * (1 - q) + cb.y * q };
    return { k, tx: size.w / 2 - k * center.x, ty: size.h / 2 - k * center.y };
  }
  function compose(parent, embedding) {
    return { k: parent.k * embedding.a, tx: parent.k * embedding.bx + parent.tx, ty: parent.k * embedding.by + parent.ty };
  }
  function inverseRebase(child, embedding) {
    const k = child.k / embedding.a;
    return { k, tx: child.tx - k * embedding.bx, ty: child.ty - k * embedding.by };
  }
  function fitForSize(scene, size, padding) {
    const [vx, vy, vw, vh] = scene.frameBounds || scene.viewBox;
    const pad = padding ?? (scene.frameBounds ? 0
      : size.w < 480 ? CONFIG.compactPadding : CONFIG.desktopPadding);
    const k = Math.max(0.0001, Math.min((size.w - 2 * pad) / vw, (size.h - 2 * pad) / vh));
    return { k, tx: (size.w - k * vw) / 2 - k * vx, ty: (size.h - k * vh) / 2 - k * vy };
  }
  function fitFor(scene, padding) { return fitForSize(scene, viewport(), padding); }
  function configureSvg(svg, scene) {
    const [, , width, height] = scene.viewBox;
    svg.setAttribute("width", width); svg.setAttribute("height", height);
    svg.style.width = `${width}px`; svg.style.height = `${height}px`;
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", scene.title);
    return svg;
  }
  function parseScene(id) {
    const doc = new DOMParser().parseFromString(sources[id], "image/svg+xml");
    if (doc.querySelector("parsererror")) throw new Error(`Scene ${id} could not be parsed`);
    return configureSvg(document.importNode(doc.documentElement, true), sceneById(id));
  }
  async function prepareScene(id, token) {
    const started = performance.now();
    const notice = setTimeout(() => {
      if (transition && transition.token === token) status.textContent = "Preparing view…";
    }, CONFIG.prepareNoticeMs);
    try {
      const svg = parseScene(id);
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      return svg;
    } finally {
      clearTimeout(notice);
      recordMetric(metrics.preparations, { sceneId: id, duration: performance.now() - started });
    }
  }
  function sceneStatus() {
    const scene = activeScene();
    const ownership = scene.ownership || {};
    const notes = [`${scene.entityCount} entities`, scene.layout];
    if (ownership.hiddenInitializerCount) notes.push(`${ownership.hiddenInitializerCount} initializers hidden`);
    if (ownership.freeFunctionCount) notes.push(`${ownership.freeFunctionCount} module-level functions; drill-down is class-only`);
    if (ownership.nestedClassCount) notes.push(`${ownership.nestedClassCount} nested classes; available in the module type view`);
    const diagnosticCount = (manifest.build.scanDiagnostics || []).length + (scene.diagnostics || []).length;
    if (diagnosticCount) notes.push(`${diagnosticCount} diagnostic${diagnosticCount === 1 ? "" : "s"}`);
    status.textContent = notes.join(" · ");
  }
  function diagnosticText(item) {
    if (typeof item === "string") return item;
    if (!item || typeof item !== "object") return String(item);
    return [item.file || item.path, item.stage, item.message || item.error].filter(Boolean).join(": ") || JSON.stringify(item);
  }
  function renderDiagnostics() {
    const items = [...(manifest.build.scanDiagnostics || []), ...(activeScene().diagnostics || [])];
    diagnosticList.replaceChildren();
    diagnostics.hidden = items.length === 0;
    diagnosticSummary.textContent = `${items.length} diagnostic${items.length === 1 ? "" : "s"}`;
    for (const item of items) {
      const row = document.createElement("li"); row.textContent = diagnosticText(item); diagnosticList.append(row);
    }
  }
  function commitScene(id, svg, nextCamera, announce = true) {
    current = id; selected = null; selectedPinned = false; zoomCandidate = null; zoomingOut = false;
    const mounted = svg || parseScene(id); mounted.style.transform = ""; mounted.style.opacity = "";
    stage.replaceChildren(mounted);
    portal.replaceChildren(); portal.style.display = "none"; portal.style.clipPath = "none";
    portal.style.zIndex = ""; stage.style.zIndex = ""; shell.style.display = "none";
    camera = nextCamera || fitFor(activeScene()); targetCamera = { ...camera };
    fitScale = fitFor(activeScene()).k;
    if (activeScene().parentSceneId) entryFitScale = fitScale;
    applyActiveCamera(); title.textContent = activeScene().title;
    updateNavigationUi(); updateSemanticZoomUi();
    renderList(); renderCrumbs(); renderDiagnostics(); sceneStatus();
    backButton.disabled = !activeScene().parentSceneId; openButton.disabled = true;
    if (announce) live.textContent = `Opened ${activeScene().title}`;
    if (metrics.usableAt === null) metrics.usableAt = performance.now();
  }
  function renderList() {
    list.replaceChildren();
    for (const node of activeScene().nodes.filter((item) => item.childSceneId)) {
      const item = document.createElement("li"); const button = document.createElement("button");
      button.type = "button"; button.textContent = node.label; button.dataset.visualId = node.visualId;
      button.addEventListener("click", () => select(node.visualId, button));
      button.addEventListener("dblclick", () => enter(node.visualId, true, button));
      button.addEventListener("keydown", (event) => { if (event.key === "Enter") enter(node.visualId, true, button); });
      item.append(button); list.append(item);
    }
  }
  function ancestorChain(id) {
    const result = [id]; let parent = sceneById(id).parentSceneId;
    while (parent) { result.unshift(parent); parent = sceneById(parent).parentSceneId; }
    return result;
  }
  function renderCrumbs() {
    crumbs.replaceChildren();
    for (const id of ancestorChain(current)) {
      const button = document.createElement("button"); button.type = "button";
      button.textContent = id === manifest.rootSceneId ? "Modules" : sceneById(id).title;
      button.disabled = id === current; button.addEventListener("click", () => unwindTo(id)); crumbs.append(button);
    }
  }
  function waitForIdle() {
    return new Promise((resolve) => {
      function check() { if (!transition) resolve(); else requestAnimationFrame(check); }
      check();
    });
  }
  async function unwindTo(id) {
    while (current !== id) { await exit(true, CONFIG.breadcrumbDuration); await waitForIdle(); }
  }
  function visualElement(id, layer = stage) { return layer.querySelector(`[data-visual-id="${CSS.escape(id)}"]`); }
  function select(id, activator = null, pinned = true) {
    selected = id; selectedPinned = pinned; lastActivator = activator;
    stage.querySelectorAll(".selected").forEach((element) => element.classList.remove("selected"));
    const element = visualElement(id); if (element) element.classList.add("selected");
    const node = nodeByVisual(id); details.textContent = node ? (node.entityId || node.label) : "Select a node to see its full name.";
    openButton.disabled = !(node && node.childSceneId);
  }
  function portalEmbedding(parentNode, childScene) {
    const [px, py, pw, ph] = parentNode.bounds;
    const framed = Boolean(childScene.frameBounds);
    const ix = framed ? 0 : Math.min(CONFIG.portalInset, pw * 0.1);
    const iy = framed ? 0 : Math.min(CONFIG.portalInset, ph * 0.1);
    const interior = { x: px + ix, y: py + iy, w: pw - 2 * ix, h: ph - 2 * iy };
    const [vx, vy, vw, vh] = childScene.frameBounds || childScene.viewBox;
    const a = Math.min(interior.w / vw, interior.h / vh);
    return { a, bx: interior.x + (interior.w - a * vw) / 2 - a * vx,
      by: interior.y + (interior.h - a * vh) / 2 - a * vy, portal: { x: px, y: py, w: pw, h: ph } };
  }
  function screenRect(rect, value) {
    return { x: value.k * rect.x + value.tx, y: value.k * rect.y + value.ty, w: value.k * rect.w, h: value.k * rect.h };
  }
  function boundsRect(bounds) {
    return { x: bounds[0], y: bounds[1], w: bounds[2], h: bounds[3] };
  }
  function coversViewport(rect, size) {
    return rect.x <= 0 && rect.y <= 0 && rect.x + rect.w >= size.w && rect.y + rect.h >= size.h;
  }
  function intersectsViewport(rect, size) {
    return rect.x < size.w && rect.y < size.h && rect.x + rect.w > 0 && rect.y + rect.h > 0;
  }
  function rectVisibleFraction(rect, size) {
    const width = Math.max(0, Math.min(rect.x + rect.w, size.w) - Math.max(rect.x, 0));
    const height = Math.max(0, Math.min(rect.y + rect.h, size.h) - Math.max(rect.y, 0));
    return rect.w > 0 && rect.h > 0 ? (width * height) / (rect.w * rect.h) : 0;
  }
  function retainScene(svg) {
    const layer = document.createElement("div");
    layer.className = "scene-layer retained-scene";
    layer.append(svg); underlays.append(layer);
    return layer;
  }
  function syncRetainedLayers(baseCamera = camera, foregroundScene = activeScene()) {
    const size = viewport();
    let ancestorCamera = baseCamera;
    let occluded = Boolean(foregroundScene.frameBounds)
      && coversViewport(screenRect(boundsRect(foregroundScene.frameBounds), baseCamera), size);
    for (let index = history.length - 1; index >= 0; index -= 1) {
      const item = history[index];
      ancestorCamera = inverseRebase(ancestorCamera, item.embedding);
      if (!item.retainedLayer) continue;
      transform(item.retainedLayer, ancestorCamera);
      const scene = sceneById(item.sceneId);
      const sceneRect = screenRect(boundsRect(scene.viewBox), ancestorCamera);
      item.retainedLayer.style.display = !occluded && intersectsViewport(sceneRect, size) ? "block" : "none";
      if (!occluded && scene.frameBounds) {
        occluded = coversViewport(screenRect(boundsRect(scene.frameBounds), ancestorCamera), size);
      }
    }
  }
  function applyActiveCamera() {
    transform(stage, camera);
    syncRetainedLayers(camera, activeScene());
  }
  function clipFor(rect, size = viewport()) {
    return `inset(${Math.max(0, rect.y)}px ${Math.max(0, size.w - rect.x - rect.w)}px ${Math.max(0, size.h - rect.y - rect.h)}px ${Math.max(0, rect.x)}px round 10px)`;
  }
  function roleElements(layer) {
    const frames = [...layer.querySelectorAll('[data-role="frame"]')];
    return {
      edges: [...layer.querySelectorAll('[data-role="edge"]')],
      frames,
      frameBoundaries: frames
        .map((element) => element.querySelector('[data-archer-boundary="true"]'))
        .filter(Boolean),
      nodes: [...layer.querySelectorAll('[data-role="node"], [data-role="group"], [data-role="context"], [data-role="owner"], [data-role="empty"]')],
      all: [...layer.querySelectorAll("[data-role]")],
    };
  }
  function setElementsOpacity(elements, opacity) {
    elements.forEach((element) => { element.style.opacity = String(clamp(opacity, 0, 1)); });
  }
  function setNodeOpacity(elements, selectedId, opacity, selectedOpacity) {
    const selectedElement = selectedId
      ? elements.find((element) => element.dataset.visualId === selectedId) || visualElement(selectedId)
      : null;
    elements.forEach((element) => {
      const preservesSelection = selectedElement
        && (element === selectedElement || element.contains(selectedElement));
      element.style.opacity = String(preservesSelection ? selectedOpacity : opacity);
    });
  }
  function resetOpacity(elements) { elements.forEach((element) => { element.style.opacity = ""; }); }
  function setFillOpacity(elements, opacity) {
    elements.forEach((element) => { element.style.fillOpacity = String(clamp(opacity, 0, 1)); });
  }
  function resetFillOpacity(elements) {
    elements.forEach((element) => { element.style.fillOpacity = ""; });
  }
  function configureShell(visualId) {
    const element = visualElement(visualId);
    const boundary = element && (element.querySelector('[data-archer-boundary="true"]') || element);
    if (!boundary) return;
    const style = getComputedStyle(boundary);
    shell.style.background = style.fill === "none" ? "transparent" : style.fill;
    shell.style.borderColor = style.stroke === "none" ? "transparent" : style.stroke;
    shell.style.borderWidth = style.strokeWidth || "2px";
    shell.style.borderRadius = style.rx && style.rx !== "auto" ? style.rx : "10px";
  }
  function showShell(rect, progress) {
    shell.style.display = "block"; shell.style.left = `${rect.x}px`; shell.style.top = `${rect.y}px`;
    shell.style.width = `${rect.w}px`; shell.style.height = `${rect.h}px`;
    shell.style.opacity = String(1 - interval(progress, 0.65, 1));
  }
  function applyTransitionState(state, progress) {
    const parentCamera = interpolateCamera(state.parentStart, state.parentEnd, progress, state.viewport);
    transform(state.parentLayer, parentCamera); transform(state.childLayer, compose(parentCamera, state.embedding));
    syncRetainedLayers(parentCamera, sceneById(state.parentSceneId));
    const portalRect = screenRect(state.embedding.portal, parentCamera);
    state.clipLayer.style.clipPath = clipFor(portalRect, state.viewport);
    if (!state.overlapFrames) showShell(portalRect, progress);
    setElementsOpacity(state.parentRoles.edges,
      state.overlapFrames ? 1 : 1 - interval(progress, 0, 0.2));
    if (state.overlapFrames) setElementsOpacity(state.parentRoles.nodes, 1);
    else setNodeOpacity(state.parentRoles.nodes, state.parentVisualId,
      1 - interval(progress, 0.08, 0.35), 1 - interval(progress, 0.08, 0.35));
    setElementsOpacity(state.childRoles.frames, state.overlapFrames ? interval(progress, 0, 0.25) : interval(progress, 0.2, 0.65));
    if (state.overlapFrames) setFillOpacity(state.childRoles.frameBoundaries, interval(progress, 0.55, 1));
    setNodeOpacity(state.childRoles.nodes, "", interval(progress, 0.2, 0.65), interval(progress, 0.2, 0.65));
    setElementsOpacity(state.childRoles.edges, interval(progress, 0.55, 0.9));
  }
  function transitionFrame(state, timestamp) {
    if (transition !== state) return;
    if (!state.started) state.started = timestamp - state.progress * state.duration;
    const raw = clamp((timestamp - state.started) / state.duration, 0, 1);
    const progress = state.reversing ? 1 - raw : raw; state.progress = progress;
    if (state.lastFrame !== null) state.frameIntervals.push(timestamp - state.lastFrame);
    state.lastFrame = timestamp;
    applyTransitionState(state, progress);
    if ((!state.reversing && progress >= 1) || (state.reversing && progress <= 0)) finishTransition(state, !state.reversing);
    else state.frame = requestAnimationFrame((time) => transitionFrame(state, time));
  }
  function finishTransition(state, atChild) {
    if (transition !== state) return;
    cancelAnimationFrame(state.frame); transition = null;
    resetOpacity(state.parentRoles.all); resetOpacity(state.childRoles.all);
    resetFillOpacity(state.childRoles.frameBoundaries);
    shell.style.display = "none";
    if (atChild) {
      const retainedLayer = state.overlapFrames ? retainScene(state.parentSvg) : null;
      history.push({ sceneId: state.parentSceneId, camera: state.savedParentCamera, embedding: state.embedding,
        visualId: state.parentVisualId, viewport: viewport(), retainedLayer,
        retainedSvg: retainedLayer ? state.parentSvg : null });
      commitScene(state.childSceneId, state.childSvg, state.childFit);
    } else {
      commitScene(state.parentSceneId, state.parentSvg, state.savedParentCamera);
      select(state.parentVisualId, lastActivator, false);
      const restored = list.querySelector(`[data-visual-id="${CSS.escape(state.parentVisualId)}"]`);
      if (restored) restored.focus();
      else diagram.focus();
    }
    semanticSuppressedSerial = gestureSerial;
    disarmed.add(state.parentVisualId);
    const ordered = [...state.frameIntervals].sort((a, b) => a - b);
    const p95 = ordered.length ? ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * 0.95))] : 0;
    const longTasks = Math.max(0, longTaskCount - state.longTaskStart);
    recordMetric(metrics.transitions, { sceneId: state.childSceneId, direction: atChild ? "in" : "out", p95FrameMs: p95, longTasks });
    if (p95 > 20 || longTasks) {
      slowScenes.add(state.childSceneId);
      status.textContent += ` · performance warning: ${p95.toFixed(1)} ms p95 frame interval${longTasks ? `, ${longTasks} long task(s)` : ""}; shorter transitions enabled`;
    }
    if (queuedWheel && performance.now() - queuedWheel.time <= CONFIG.wheelIdleMs) {
      const queued = queuedWheel; queuedWheel = null;
      zoom(queued.factor, queued.x, queued.y, false);
    } else queuedWheel = null;
    if (!atChild && queuedSemanticEntry && queuedSemanticEntry.sceneId === current) {
      const queued = queuedSemanticEntry; queuedSemanticEntry = null;
      requestAnimationFrame(() => {
        const element = visualElement(queued.visualId);
        if (!transition && element && occupancy(element) >= semanticZoom.entryThreshold
          && visibleFraction(element) >= 0.5) enter(queued.visualId, false);
      });
    }
  }
  async function enter(visualId = selected, explicit = false, activator = null) {
    if (transition) return;
    const node = nodeByVisual(visualId); if (!node || !node.childSceneId || (!explicit && reducedMotion.matches)) return;
    const token = Symbol("transition"); transition = { token, phase: "preparing", parentVisualId: visualId };
    try {
      const childSvg = await prepareScene(node.childSceneId, token); if (!transition || transition.token !== token) return;
      if (paintFrame) cancelAnimationFrame(paintFrame);
      paintFrame = 0; lastPaint = 0; targetCamera = { ...camera };
      const overlaps = Boolean(sceneById(node.childSceneId).frameBounds);
      lastActivator = activator; const embedding = portalEmbedding(node, sceneById(node.childSceneId));
      const childFit = overlaps ? compose(camera, embedding) : fitFor(sceneById(node.childSceneId));
      const parentEnd = overlaps ? { ...camera } : inverseRebase(childFit, embedding);
      const duration = reducedMotion.matches || slowScenes.has(node.childSceneId)
        ? CONFIG.reducedDuration : overlaps ? CONFIG.overlapDuration : CONFIG.entryDuration;
      portal.replaceChildren(childSvg); portal.style.display = "none"; portal.style.zIndex = "2"; stage.style.zIndex = "1";
      const state = { token, phase: "entering", duration, progress: 0, reversing: false, parentSceneId: current,
        childSceneId: node.childSceneId, parentVisualId: visualId, parentLayer: stage, childLayer: childSvg,
        clipLayer: portal, parentSvg: stage.firstElementChild, childSvg, embedding, parentStart: { ...camera }, parentEnd,
        savedParentCamera: { ...camera }, childFit, viewport: viewport(), parentRoles: roleElements(stage),
        childRoles: roleElements(childSvg), frameIntervals: [], lastFrame: null, longTaskStart: longTaskCount,
        overlapFrames: overlaps };
      transition = state;
      configureShell(visualId);
      applyTransitionState(state, 0);
      portal.style.display = "block";
      if (reducedMotion.matches) finishTransition(state, true);
      else state.frame = requestAnimationFrame((time) => transitionFrame(state, time));
    } catch (error) {
      transition = null; portal.replaceChildren(); portal.style.display = "none";
      status.textContent = `Unable to prepare view: ${error.message}`;
    }
  }
  async function exit(explicit = false, durationOverride = null) {
    if (transition) {
      if (transition.phase === "preparing") { transition = null; sceneStatus(); }
      else if (transition.phase === "entering") {
        const state = transition; cancelAnimationFrame(state.frame); state.reversing = true;
        state.duration = state.overlapFrames ? CONFIG.overlapExitDuration : CONFIG.exitDuration;
        state.started = performance.now() - (1 - state.progress) * state.duration;
        state.frame = requestAnimationFrame((time) => transitionFrame(state, time));
      }
      return;
    }
    if (!history.length || (!explicit && reducedMotion.matches)) return;
    const item = history[history.length - 1]; const token = Symbol("transition"); transition = { token, phase: "preparing" };
    let parentSvg;
    if (item.retainedSvg) {
      parentSvg = item.retainedSvg;
      item.retainedLayer.remove(); item.retainedLayer = null; item.retainedSvg = null;
    } else {
      try { parentSvg = await prepareScene(item.sceneId, token); }
      catch (error) { transition = null; status.textContent = `Unable to prepare parent: ${error.message}`; return; }
    }
    if (!transition || transition.token !== token) return;
    if (paintFrame) cancelAnimationFrame(paintFrame);
    paintFrame = 0; lastPaint = 0; targetCamera = { ...camera };
    history.pop(); const childSvg = stage.firstElementChild; const mappedParent = inverseRebase(camera, item.embedding);
    stage.replaceChildren(parentSvg); portal.replaceChildren(childSvg); portal.style.display = "none";
    portal.style.zIndex = "2"; stage.style.zIndex = "1";
    const overlaps = Boolean(activeScene().frameBounds);
    const parentStart = overlaps ? mappedParent : item.camera;
    const duration = reducedMotion.matches || slowScenes.has(current)
      ? CONFIG.reducedDuration
      : (durationOverride || (overlaps ? CONFIG.overlapExitDuration : CONFIG.exitDuration));
    const state = { token, phase: "exiting", duration, progress: 1, reversing: true, parentSceneId: item.sceneId,
      childSceneId: current, parentVisualId: item.visualId, parentLayer: stage, childLayer: childSvg,
      clipLayer: portal, parentSvg, childSvg, embedding: item.embedding, parentStart, parentEnd: mappedParent,
      savedParentCamera: parentStart, childFit: { ...camera }, viewport: viewport(),
      parentRoles: roleElements(parentSvg), childRoles: roleElements(childSvg), frameIntervals: [],
      lastFrame: null, longTaskStart: longTaskCount,
      overlapFrames: overlaps };
    transition = state;
    configureShell(item.visualId);
    applyTransitionState(state, 1);
    portal.style.display = "block";
    if (reducedMotion.matches) finishTransition(state, false);
    else { state.started = performance.now(); state.frame = requestAnimationFrame((time) => transitionFrame(state, time)); }
  }
  function schedulePaint() { if (!paintFrame) paintFrame = requestAnimationFrame(paint); }
  function paint(timestamp) {
    paintFrame = 0; const elapsed = lastPaint ? Math.min(64, timestamp - lastPaint) : 16; lastPaint = timestamp;
    const alpha = 1 - Math.exp(-elapsed / CONFIG.dampingMs);
    camera.k += (targetCamera.k - camera.k) * alpha;
    camera.tx += (targetCamera.tx - camera.tx) * alpha; camera.ty += (targetCamera.ty - camera.ty) * alpha;
    applyActiveCamera();
    updateNavigationUi();
    evaluateSemanticCamera();
    if (zoomingOut && activeScene().parentSceneId) evaluateExit(camera.k);
    rearmNodes();
    if (Math.abs(camera.k - targetCamera.k) > 0.0001 || Math.abs(camera.tx - targetCamera.tx) > 0.05
      || Math.abs(camera.ty - targetCamera.ty) > 0.05) schedulePaint();
  }
  function zoom(factor, sx, sy, allowSemantic = true) {
    if (transition) return;
    const old = targetCamera.k; const unclamped = old * factor;
    if (allowSemantic && factor < 1 && activeScene().parentSceneId) zoomingOut = true;
    else if (factor >= 1) { zoomingOut = false; exitSince = 0; }
    const next = clamp(unclamped, CONFIG.minScale * fitScale, CONFIG.maxScale * fitScale); const ratio = next / old;
    targetCamera.tx = sx - (sx - targetCamera.tx) * ratio; targetCamera.ty = sy - (sy - targetCamera.ty) * ratio;
    targetCamera.k = next; schedulePaint();
  }
  function boundaryRect(element) {
    const boundary = element.querySelector('[data-archer-boundary="true"]');
    return (boundary || element).getBoundingClientRect();
  }
  function visibleFraction(element) {
    const rect = boundaryRect(element); const bounds = diagram.getBoundingClientRect();
    const width = Math.max(0, Math.min(rect.right, bounds.right) - Math.max(rect.left, bounds.left));
    const height = Math.max(0, Math.min(rect.bottom, bounds.bottom) - Math.max(rect.top, bounds.top));
    return rect.width > 0 && rect.height > 0 ? (width * height) / (rect.width * rect.height) : 0;
  }
  function occupancy(element) {
    const rect = boundaryRect(element); return Math.max(rect.width / diagram.clientWidth, rect.height / diagram.clientHeight);
  }
  function expandable(id) { const node = nodeByVisual(id); return Boolean(node && node.childSceneId); }
  function pointerCandidate(clientX, clientY) {
    for (const element of document.elementsFromPoint(clientX, clientY)) {
      const node = element.closest && element.closest('[data-role="node"][data-visual-id]');
      if (node && stage.contains(node) && expandable(node.dataset.visualId) && visibleFraction(node) >= 0.5) return node.dataset.visualId;
    }
    return null;
  }
  function centerCandidate() {
    const bounds = diagram.getBoundingClientRect(); const middle = { left: bounds.left + bounds.width * 0.25,
      right: bounds.right - bounds.width * 0.25, top: bounds.top + bounds.height * 0.25,
      bottom: bounds.bottom - bounds.height * 0.25 };
    let result = null; let best = Infinity;
    for (const node of activeScene().nodes.filter((item) => item.childSceneId)) {
      const element = visualElement(node.visualId); if (!element || visibleFraction(element) < 0.5) continue;
      const rect = boundaryRect(element); const x = (rect.left + rect.right) / 2; const y = (rect.top + rect.bottom) / 2;
      if (x < middle.left || x > middle.right || y < middle.top || y > middle.bottom) continue;
      const distance = Math.hypot(x - (bounds.left + bounds.width / 2), y - (bounds.top + bounds.height / 2));
      if (distance < best) { best = distance; result = node.visualId; }
    }
    return result;
  }
  function parentPanCandidate() {
    if (!history.length || !activeScene().frameBounds) return null;
    const item = history[history.length - 1];
    const size = viewport();
    const parentCamera = inverseRebase(camera, item.embedding);
    const activeRect = screenRect(item.embedding.portal, parentCamera);
    if (rectVisibleFraction(activeRect, size) >= 0.5) return null;
    const middle = { left: size.w * 0.25, right: size.w * 0.75, top: size.h * 0.25, bottom: size.h * 0.75 };
    let result = null; let best = Infinity;
    for (const node of sceneById(item.sceneId).nodes.filter((candidate) => candidate.childSceneId
      && candidate.visualId !== item.visualId)) {
      const rect = screenRect(boundsRect(node.bounds), parentCamera);
      const occupancy = Math.max(rect.w / size.w, rect.h / size.h);
      if (occupancy < semanticZoom.entryThreshold || rectVisibleFraction(rect, size) < 0.5) continue;
      const x = rect.x + rect.w / 2; const y = rect.y + rect.h / 2;
      if (x < middle.left || x > middle.right || y < middle.top || y > middle.bottom) continue;
      const distance = Math.hypot(x - size.w / 2, y - size.h / 2);
      if (distance < best) { best = distance; result = { sceneId: item.sceneId, visualId: node.visualId }; }
    }
    return result;
  }
  function evaluatePanHandoff() {
    const candidate = parentPanCandidate();
    if (!candidate || transition || reducedMotion.matches) {
      panHandoffState = { sceneId: null, id: null, since: 0 }; return false;
    }
    const now = performance.now();
    if (panHandoffState.sceneId !== candidate.sceneId || panHandoffState.id !== candidate.visualId) {
      panHandoffState = { sceneId: candidate.sceneId, id: candidate.visualId, since: now };
      setTimeout(() => {
        const currentCandidate = parentPanCandidate();
        if (!transition && currentCandidate && currentCandidate.sceneId === candidate.sceneId
          && currentCandidate.visualId === candidate.visualId
          && performance.now() - panHandoffState.since >= CONFIG.stableMs) {
          queuedSemanticEntry = candidate;
          panHandoffState = { sceneId: null, id: null, since: 0 };
          exit(false);
        }
      }, CONFIG.stableMs + 1);
    }
    return true;
  }
  function evaluateSemanticCamera() {
    if (transition || reducedMotion.matches) return;
    if (evaluatePanHandoff()) {
      candidateState = { id: null, since: 0, serial: -1 }; return;
    }
    const candidate = zoomCandidate && zoomCandidate.serial === gestureSerial
      ? zoomCandidate.id : centerCandidate();
    evaluateEntry(candidate, gestureSerial);
  }
  function candidateAt(x, y) {
    return (selectedPinned && selected && expandable(selected) ? selected : null)
      || pointerCandidate(x, y) || centerCandidate();
  }
  function evaluateEntry(id, serial) {
    if (!id || reducedMotion.matches || transition || disarmed.has(id) || serial === semanticSuppressedSerial) {
      candidateState = { id: null, since: 0, serial: -1 }; return;
    }
    const element = visualElement(id);
    if (!element || occupancy(element) < semanticZoom.entryThreshold || visibleFraction(element) < 0.5) {
      candidateState = { id: null, since: 0, serial: -1 }; return;
    }
    const now = performance.now();
    if (candidateState.id !== id || candidateState.serial !== serial) {
      candidateState = { id, since: now, serial };
      setTimeout(() => {
        const candidate = visualElement(id);
        if (candidateState.id === id && candidateState.serial === serial && candidate
          && gestureSerial === serial && performance.now() - candidateState.since >= CONFIG.stableMs
          && occupancy(candidate) >= semanticZoom.entryThreshold
          && visibleFraction(candidate) >= 0.5) enter(id, false);
      }, CONFIG.stableMs + 1);
    }
  }
  function evaluateExit(unclampedScale) {
    if (gestureSerial === semanticSuppressedSerial) return;
    if (unclampedScale / entryFitScale >= semanticZoom.exitThreshold) { exitSince = 0; return; }
    const now = performance.now(); if (!exitSince) exitSince = now;
    setTimeout(() => {
      if (exitSince && performance.now() - exitSince >= CONFIG.stableMs
        && camera.k / entryFitScale < semanticZoom.exitThreshold) exit(false);
      else exitSince = 0;
    }, CONFIG.stableMs + 1);
  }
  function rearmNodes() {
    for (const id of [...disarmed]) { const element = visualElement(id); if (!element || occupancy(element) < CONFIG.rearmThreshold) disarmed.delete(id); }
  }
  function controlZoom(factor) {
    if (transition) return;
    const now = performance.now();
    if (now - controlZoomTime > CONFIG.wheelIdleMs) gestureSerial += 1;
    controlZoomTime = now;
    const bounds = diagram.getBoundingClientRect();
    const sx = bounds.width / 2; const sy = bounds.height / 2;
    const candidate = factor > 1 ? candidateAt(bounds.left + sx, bounds.top + sy) : null;
    zoom(factor, sx, sy, true); rearmNodes();
    if (factor > 1) {
      zoomingOut = false;
      zoomCandidate = { id: candidate, serial: gestureSerial };
      evaluateEntry(candidate, gestureSerial);
    } else {
      zoomingOut = true; zoomCandidate = null;
      candidateState = { id: null, since: 0, serial: -1 };
    }
  }
  function keyboardPan(key) {
    const now = performance.now();
    if (now - panTime > CONFIG.wheelIdleMs) gestureSerial += 1;
    panTime = now; zoomCandidate = null; zoomingOut = false; exitSince = 0;
    const step = effectivePanStep();
    targetCamera.tx += key === "ArrowLeft" ? step : key === "ArrowRight" ? -step : 0;
    targetCamera.ty += key === "ArrowUp" ? step : key === "ArrowDown" ? -step : 0;
    schedulePaint();
  }
  diagram.addEventListener("wheel", (event) => {
    event.preventDefault();
    const bounds = diagram.getBoundingClientRect(); const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? bounds.height : 1;
    const delta = clamp(event.deltaY * unit, -120, 120); const now = performance.now();
    if (now - wheelTime > CONFIG.wheelIdleMs) gestureSerial += 1; wheelTime = now;
    if (transition) {
      const replacement = delta < 0 ? candidateAt(event.clientX, event.clientY) : null;
      if (transition.phase === "preparing" && replacement
        && replacement !== transition.parentVisualId) {
        transition = null; queuedWheel = null; sceneStatus();
      } else {
        const factor = Math.exp(-0.002 * delta);
        queuedWheel = {
          factor: queuedWheel ? queuedWheel.factor * factor : factor,
          x: event.clientX - bounds.left,
          y: event.clientY - bounds.top,
          time: now,
        };
        return;
      }
    }
    const candidate = delta < 0 ? candidateAt(event.clientX, event.clientY) : null;
    zoom(Math.exp(-0.002 * delta), event.clientX - bounds.left, event.clientY - bounds.top, true);
    rearmNodes();
    if (delta < 0) {
      zoomingOut = false;
      zoomCandidate = { id: candidate, serial: gestureSerial };
      evaluateEntry(candidate, gestureSerial);
    } else {
      zoomingOut = true;
      zoomCandidate = null; candidateState = { id: null, since: 0, serial: -1 };
    }
  }, { passive: false });
  stage.addEventListener("click", (event) => {
    if (suppressClick) { suppressClick = false; return; }
    const element = event.target.closest('[data-role="node"][data-visual-id]'); if (element) select(element.dataset.visualId, element);
  });
  stage.addEventListener("dblclick", (event) => {
    const element = event.target.closest('[data-role="node"][data-visual-id]'); if (element) enter(element.dataset.visualId, true, element);
  });
  diagram.addEventListener("pointerdown", (event) => {
    if (transition) return;
    diagram.setPointerCapture(event.pointerId); pointers.set(event.pointerId, { x: event.clientX, y: event.clientY }); gestureSerial += 1;
    if (pointers.size === 1) { drag = { x: event.clientX, y: event.clientY, camera: { ...targetCamera } }; pinch = null; }
    else if (pointers.size === 2) {
      const points = [...pointers.values()]; const midpoint = { x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 };
      const bounds = diagram.getBoundingClientRect();
      pinch = { distance: Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y), camera: { ...targetCamera },
        scene: { x: (midpoint.x - bounds.left - targetCamera.tx) / targetCamera.k,
          y: (midpoint.y - bounds.top - targetCamera.ty) / targetCamera.k } };
      drag = null;
    }
  });
  diagram.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId) || transition) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 1 && drag) {
      const dx = event.clientX - drag.x; const dy = event.clientY - drag.y;
      if (Math.hypot(dx, dy) > 5) suppressClick = true;
      targetCamera.tx = drag.camera.tx + dx; targetCamera.ty = drag.camera.ty + dy; camera = { ...targetCamera }; applyActiveCamera();
      zoomCandidate = null; zoomingOut = false; exitSince = 0; evaluateSemanticCamera();
    } else if (pointers.size === 2 && pinch) {
      const points = [...pointers.values()]; const distance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
      const midpoint = { x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 };
      const bounds = diagram.getBoundingClientRect(); const k = clamp(pinch.camera.k * distance / Math.max(1, pinch.distance),
        CONFIG.minScale * fitScale, CONFIG.maxScale * fitScale);
      targetCamera = { k, tx: midpoint.x - bounds.left - k * pinch.scene.x, ty: midpoint.y - bounds.top - k * pinch.scene.y };
      camera = { ...targetCamera }; applyActiveCamera();
      updateNavigationUi();
      rearmNodes();
      if (distance > pinch.distance) evaluateEntry(candidateAt(midpoint.x, midpoint.y), gestureSerial);
      else if (activeScene().parentSceneId) { zoomingOut = true; evaluateExit(k); }
    }
  });
  function releasePointer(event) { pointers.delete(event.pointerId); if (!pointers.size) { drag = null; pinch = null; } }
  diagram.addEventListener("pointerup", releasePointer); diagram.addEventListener("pointercancel", releasePointer);
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && ["+", "=", "-"].includes(event.key)) return;
    if (event.target instanceof Element
      && (event.target.closest("input, select, textarea") || event.target.isContentEditable)) return;
    if (event.key === "Escape") { exit(true); event.preventDefault(); }
    else if (event.key === "Enter" && event.target === diagram) { enter(selected, true, lastActivator); event.preventDefault(); }
    else if (event.key === "Home") { fitCurrent(); event.preventDefault(); }
    else if (event.key === "+" || event.key === "=") { controlZoom(1.25); event.preventDefault(); }
    else if (event.key === "-") { controlZoom(0.8); event.preventDefault(); }
    else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      keyboardPan(event.key); event.preventDefault();
    }
  });
  function fitCurrent() {
    if (!transition) {
      exitSince = 0; zoomCandidate = null; zoomingOut = false;
      candidateState = { id: null, since: 0, serial: -1 };
      targetCamera = fitFor(activeScene()); fitScale = targetCamera.k; schedulePaint();
    }
  }
  function handleResize() {
    if (transition && transition.phase !== "preparing") finishTransition(transition, transition.progress >= 0.5);
    const nextViewport = viewport();
    for (const item of history) {
      const oldViewport = item.viewport;
      const oldFit = fitForSize(sceneById(item.sceneId), oldViewport).k;
      const newFit = fitForSize(sceneById(item.sceneId), nextViewport).k;
      const center = {
        x: (oldViewport.w / 2 - item.camera.tx) / item.camera.k,
        y: (oldViewport.h / 2 - item.camera.ty) / item.camera.k,
      };
      item.camera.k *= newFit / oldFit;
      item.camera.tx = nextViewport.w / 2 - item.camera.k * center.x;
      item.camera.ty = nextViewport.h / 2 - item.camera.k * center.y;
      item.viewport = nextViewport;
    }
    targetCamera = fitFor(activeScene()); camera = { ...targetCamera }; fitScale = camera.k; applyActiveCamera();
    updateNavigationUi();
  }
  backButton.addEventListener("click", () => exit(true));
  document.getElementById("fit").addEventListener("click", fitCurrent);
  document.getElementById("zoom-in").addEventListener("click", () => controlZoom(1.25));
  document.getElementById("zoom-out").addEventListener("click", () => controlZoom(0.8));
  openButton.addEventListener("click", () => enter(selected, true, lastActivator));
  panSpeedInput.addEventListener("input", () => {
    navigation.speed = Number(panSpeedInput.value); updateNavigationUi(); saveNavigationSettings();
  });
  panZoomScaleInput.addEventListener("input", () => {
    navigation.zoomScale = Number(panZoomScaleInput.value); updateNavigationUi(); saveNavigationSettings();
  });
  document.getElementById("pan-reset").addEventListener("click", () => {
    Object.assign(navigation, navigationDefaults); updateNavigationUi(); saveNavigationSettings();
  });
  entryThresholdInput.addEventListener("input", () => {
    semanticZoom.entryThreshold = Number(entryThresholdInput.value) / 100;
    updateSemanticZoomUi(); saveSemanticZoomSettings(); evaluateSemanticCamera();
  });
  exitThresholdInput.addEventListener("input", () => {
    semanticZoom.exitThreshold = Number(exitThresholdInput.value) / 100;
    updateSemanticZoomUi(); saveSemanticZoomSettings();
  });
  document.getElementById("semantic-zoom-reset").addEventListener("click", () => {
    Object.assign(semanticZoom, semanticZoomDefaults); updateSemanticZoomUi(); saveSemanticZoomSettings();
  });
  window.addEventListener("resize", handleResize);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && transition && transition.phase !== "preparing") {
      const state = transition;
      const elapsed = state.started ? clamp((performance.now() - state.started) / state.duration, 0, 1) : 0;
      const progress = state.reversing ? 1 - elapsed : elapsed;
      finishTransition(state, progress >= 0.5);
    }
  });
  try { commitScene(manifest.rootSceneId, parseScene(manifest.rootSceneId), null, false); }
  catch (error) { status.textContent = `Unable to open architecture explorer: ${error.message}`; live.textContent = status.textContent; }
})();
