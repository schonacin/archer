/* Experimental architecture map. D3 owns one camera; geometry never relayouts during navigation. */
(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("map-data").textContent);
  const entities = data.entities, root = data.root;
  const $ = (id) => document.getElementById(id);
  const svg = d3.select("#map"), world = d3.select("#world"), labels = d3.select("#labels");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const positions = new Map(), placements = new Map(), chainCache = new Map();
  const incoming = new Map(), outgoing = new Map();
  const history = [];
  let camera = d3.zoomIdentity, current = root, selected = null, relationDirection = "out";
  let allConnections = true, connectionStrength = .24, frame = 0, moving = false, hovered = null;
  let pinnedRegion = false, focusPoint = null, gesturePose = null;
  let candidate = null, candidateSince = 0, lastPaint = 0, semanticTimer = 0;
  let connectionScale = null, scaleChangedAt = 0, frontierTimer = 0, frontierStarted = 0, panTimer = 0, frontierPending = false, lastPanAt = -Infinity;
  let frontierFrom = new Map(), frontierTo = new Map(), outsideOpen = false, outsideSignature = "", gesturing = false;
  const endpointIds = new Set(data.relationships.flatMap(edge => [edge.source, edge.target]));
  const metrics = { startedAt: performance.now(), usableAt: null, frames: [], paints: [], renderedNodes: 0 };
  window.__ARCHER_MAP_METRICS__ = metrics;
  function bounds() { return { w: $("canvas").clientWidth, h: $("canvas").clientHeight }; }
  function clamp(n, a, b) { return Math.max(a, Math.min(b, n)); }
  function ramp(n, a, b) { const t = clamp((n - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function ancestors(id) {
    if (chainCache.has(id)) return chainCache.get(id);
    const result = []; let next = id;
    while (next != null) { result.unshift(next); next = entities[next].parent; }
    chainCache.set(id, result); return result;
  }
  function within(id, owner) { return ancestors(id).includes(owner); }
  function tint(color, amount) { return d3.interpolateRgb("#fff", color || "#64748b")(amount); }
  const typeColors = { package: "#137f89", module: "#4274b8", class: "#8960b2", function: "#ac7421", method: "#ac7421" };
  function typeColor(e) { return typeColors[e.kind] || "#64748b"; }
  function cardPath(d) {
    const { x, y, w, h } = d, k = camera.k;
    const radius = Math.min((d.entity.kind === "function" || d.entity.kind === "method" ? 22 : 6) / k, h / 2, w / 2);
    if (d.entity.kind === "package") {
      const tab = Math.min(100 / k, w * .4), notch = Math.min(9 / k, h * .1);
      return `M${x},${y+notch}V${y}H${x+tab}L${x+tab+notch},${y+notch}H${x+w}V${y+h}H${x}Z`;
    }
    if (d.entity.kind === "class") {
      const cut = Math.min(16 / k, w * .15, h * .2);
      return `M${x},${y}H${x+w-cut}L${x+w},${y+cut}V${y+h}H${x}Z`;
    }
    return `M${x+radius},${y}H${x+w-radius}Q${x+w},${y} ${x+w},${y+radius}V${y+h-radius}Q${x+w},${y+h} ${x+w-radius},${y+h}H${x+radius}Q${x},${y+h} ${x},${y+h-radius}V${y+radius}Q${x},${y} ${x+radius},${y}Z`;
  }
  function kindLabel(e) { return e.kind === "package" ? "package" : e.kind; }
  function icon(e) { return ({ package: "▧", module: "M", class: "C", function: "ƒ", method: "ƒ" })[e.kind] || "↗"; }
  function embed(owner, transform, depth) {
    placements.set(owner, { ...transform, depth });
    const layout = data.layouts[owner]; if (!layout) return;
    for (const id of entities[owner].children) {
      const local = layout.nodes[id]; if (!local) continue;
      const [x, y, w, h] = local;
      const rect = { x: transform.x + transform.k * x, y: transform.y + transform.k * y,
        w: transform.k * w, h: transform.k * h, depth, id };
      positions.set(id, rect);
      if (data.layouts[id]) {
        const [bx, by, bw, bh] = data.layouts[id].bounds;
        const scale = Math.min((w - 32) / bw, (h - 72) / bh) * transform.k;
        embed(id, { k: scale, x: rect.x + rect.w / 2 - scale * (bx + bw / 2),
          y: rect.y + transform.k * 56 + (rect.h - transform.k * 72) / 2 - scale * (by + bh / 2) }, depth + 1);
      }
    }
  }
  embed(root, { x: 0, y: 0, k: 1 }, 0);
  const rootBounds = data.layouts[root].bounds;
  const rootRect = { x: rootBounds[0], y: rootBounds[1], w: rootBounds[2], h: rootBounds[3] };
  function regionRect(id) {
    if (id === root) return rootRect;
    const layout = data.layouts[id], pose = placements.get(id);
    if (!layout || !pose) return positions.get(id);
    const [x, y, w, h] = layout.bounds;
    return { x: pose.x + pose.k * x, y: pose.y + pose.k * y, w: pose.k * w, h: pose.k * h };
  }
  function screen(rect) { return { x: rect.x * camera.k + camera.x, y: rect.y * camera.k + camera.y,
    w: rect.w * camera.k, h: rect.h * camera.k }; }
  function intersects(r, size) { return r.x + r.w > -20 && r.y + r.h > -20 && r.x < size.w + 20 && r.y < size.h + 20; }
  function fitTransform(rect, padding = 76) {
    const size = bounds();
    const k = Math.min(Math.max(40, size.w - padding * 2) / rect.w,
      Math.max(40, size.h - padding * 2) / rect.h);
    return d3.zoomIdentity.translate(size.w / 2 - k * (rect.x + rect.w / 2),
      size.h / 2 - k * (rect.y + rect.h / 2)).scale(k);
  }
  function remember(pose = { camera, current, selected }) {
    history.push(pose); if (history.length > 80) history.shift();
    $("back").disabled = false;
  }
  function applyCamera(target, duration = 420) {
    svg.interrupt(); moving = true;
    const finish = () => { moving = false; candidate = null; requestPaint(); };
    if (reduced.matches || !duration) { svg.call(zoom.transform, target); finish(); }
    else svg.transition().duration(duration).ease(d3.easeCubicInOut).call(zoom.transform, target)
      .on("end.map interrupt.map cancel.map", finish);
  }
  function navigate(id, record = true, inspect = false) {
    if (!entities[id]) return;
    if (record) remember();
    current = entities[id].children.length ? id : (entities[id].parent || root);
    selected = id === root || (entities[id].children.length && !inspect) ? null : id;
    pinnedRegion = true; focusPoint = null;
    outsideOpen = false;
    updateSidebar(); updateCrumbs();
    $("live").textContent = `Opened ${entities[id].name}`;
    applyCamera(fitTransform(regionRect(id)));
  }
  function goBack() {
    if (!history.length) return;
    const saved = history.pop(); current = saved.current; selected = saved.selected; pinnedRegion = true; focusPoint = null;
    updateSidebar(); updateCrumbs(); applyCamera(saved.camera, 320);
    $("back").disabled = !history.length;
  }
  function parent() { if (current !== root) navigate(entities[current].parent || root); }
  function chooseRegion(size) {
    if (moving || pinnedRegion) return;
    let next = root, best = -1;
    const focus = focusPoint || { x: size.w / 2, y: size.h / 2 };
    for (const [id, rect] of positions) {
      if (!entities[id].children.length) continue;
      const r = screen(rect);
      const coversCenter = r.x <= focus.x && r.x + r.w >= focus.x
        && r.y <= focus.y && r.y + r.h >= focus.y;
      const retained = id === current;
      if (coversCenter && r.w >= size.w * (retained ? .48 : .68)
        && r.h >= size.h * (retained ? .40 : .53) && rect.depth > best) {
        next = id; best = rect.depth;
      }
    }
    if (next === current) { candidate = null; return; }
    if (next !== candidate) {
      candidate = next; candidateSince = performance.now();
      clearTimeout(semanticTimer); semanticTimer = setTimeout(requestPaint, 130);
    } else if (performance.now() - candidateSince >= 110 && performance.now() - scaleChangedAt >= 120) {
      if (within(next, current)) remember(gesturePose || { camera, current, selected });
      current = next; gesturePose = { camera, current, selected }; candidate = null; updateCrumbs(); updateSidebar();
    }
  }
  function visibleScene(owner, opacity, nodes, size) {
    const placement = placements.get(owner), layout = data.layouts[owner];
    if (!layout) return;
    for (const id of entities[owner].children) {
      const rect = positions.get(id); if (!rect) continue;
      const r = screen(rect); if (!intersects(r, size) || r.w < 12 || r.h < 9) continue;
      const expansion = entities[id].children.length ? ramp(Math.min(r.w, r.h), 160, 310) : 0;
      nodes.push({ ...rect, screen: r, opacity, expansion, entity: entities[id] });
      if (expansion > .01) visibleScene(id, opacity * expansion, nodes, size);
    }
  }
  // The relationship frontier depends on settled magnification, never on viewport
  // intersection. Panning changes only the camera; offscreen entities keep their IDs.
  function resolvedEndpoint(id, scale) {
    let result = null, opacity = 1;
    for (const owner of ancestors(id).slice(1)) {
      const rect = positions.get(owner); if (!rect) continue;
      if (rect.w * scale >= 62 && rect.h * scale >= 27) {
        const t = result ? ramp(opacity, .15, .85) : 1;
        if (t > 0) {
          const prior = result ? result.rect : rect;
          result = { id: owner, rect: blendRect(prior, rect, t) };
        }
      }
      opacity *= entities[owner].children.length ? ramp(Math.min(rect.w, rect.h) * scale, 160, 310) : 1;
    }
    return result;
  }
  function blendRect(a, b, t) {
    return { x: a.x+(b.x-a.x)*t, y: a.y+(b.y-a.y)*t, w: a.w+(b.w-a.w)*t, h: a.h+(b.h-a.h)*t };
  }
  function frontierEndpoint(id, now) {
    const target = frontierTo.get(id), prior = frontierFrom.get(id);
    if (!target || !prior || reduced.matches) return target;
    const t = ramp(now-frontierStarted, 0, 180);
    return { id: target.id, rect: blendRect(prior.rect, target.rect, t) };
  }
  function lockFrontier() {
    const now = performance.now(); lastPanAt = now;
    if (frontierPending) { clearTimeout(frontierTimer); frontierTimer = setTimeout(requestPaint, 160); }
    if (connectionScale === null || (connectionScale === camera.k && now-frontierStarted >= 180)) return;
    frontierPending = true;
    frontierTo = new Map([...endpointIds].map(id => [id, frontierEndpoint(id, now)]));
    frontierFrom = frontierTo; frontierStarted = -Infinity; connectionScale = camera.k;
    clearTimeout(frontierTimer); frontierTimer = setTimeout(requestPaint, 160);
  }
  function updateFrontier(now) {
    if (connectionScale === null || (!moving && !gesturing && now-scaleChangedAt >= 120
      && now-lastPanAt >= 120 && (connectionScale !== camera.k || frontierPending))) {
      frontierFrom = new Map([...endpointIds].map(id => [id, frontierEndpoint(id, now)]));
      connectionScale = camera.k; frontierStarted = now; frontierPending = false;
      frontierTo = new Map([...endpointIds].map(id => [id, resolvedEndpoint(id, connectionScale)]));
    }
    if (!reduced.matches && now-frontierStarted < 180) requestPaint();
  }
  function outsideRelationships(owner) {
    const grouped = new Map();
    for (const relation of data.relationships) {
      const sourceInside = within(relation.source, owner), targetInside = within(relation.target, owner);
      if (sourceInside === targetInside) continue;
      const id = sourceInside ? relation.target : relation.source;
      if (within(id, current)) continue;
      const item = grouped.get(id) || { id, outgoing: 0, incoming: 0 };
      item[sourceInside ? "outgoing" : "incoming"] += relation.count; grouped.set(id, item);
    }
    return [...grouped.values()].sort((a,b) => (b.outgoing+b.incoming)-(a.outgoing+a.incoming) || a.id.localeCompare(b.id));
  }
  function updateOutside() {
    const owner = selected || current;
    const signature = `${current}|${owner}|${outsideOpen}|${!!selected}`;
    if (signature === outsideSignature) return;
    outsideSignature = signature;
    const items = outsideRelationships(owner);
    $("outside-toggle").hidden = !items.length;
    $("outside-toggle").textContent = `Outside ${selected ? "selection" : "this region"} · ${items.length}`;
    const expanded = items.length > 0 && outsideOpen;
    $("outside-toggle").setAttribute("aria-expanded", expanded);
    $("outside-panel").hidden = !expanded;
    $("outside-title").textContent = selected ? `Connections outside ${entities[selected].label}` : `Connections outside ${entities[current].label}`;
    $("outside-list").replaceChildren(...items.map(item => {
      const button = entityButton(item.id,
        [item.outgoing ? `Uses · ${item.outgoing}` : "", item.incoming ? `Used by · ${item.incoming}` : ""].filter(Boolean).join(" / "), id => navigate(id, true, true));
      if (items.some(other => other.id !== item.id && entities[other.id].label === entities[item.id].label)) {
        const e = entities[item.id];
        button.querySelector("strong").textContent = `${entities[e.parent].label}.${e.label}`;
      }
      return button;
    }));
  }
  function boundary(rect, toward) {
    const cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
    const dx = toward.x - cx, dy = toward.y - cy;
    const t = 1 / Math.max(Math.abs(dx) / Math.max(1, rect.w / 2), Math.abs(dy) / Math.max(1, rect.h / 2), 1e-9);
    return { x: cx + dx * t, y: cy + dy * t };
  }
  // Preserve engine routes for sibling endpoints. During refinement, warp the
  // reference path toward the interpolated endpoint cards; restore exact SVG
  // geometry once settled. Camera translation never changes the route itself.
  const referenceRoutes = new Map();
  if (data.build.mapLayout === "reference") {
    for (const [owner, layout] of Object.entries(data.layouts)) {
      for (const edge of layout.edges) referenceRoutes.set(JSON.stringify([edge.source, edge.target]), { owner, paths: edge.paths });
    }
  }
  function cachedRoute(path) {
    if (path.geometry) return path.geometry;
    const element = document.createElementNS("http://www.w3.org/2000/svg", "path"); element.setAttribute("d", path.d);
    const length = element.getTotalLength();
    const points = Array.from({ length: 49 }, (_, i) => {
      const p = element.getPointAtLength(length * i / 48); return { x: p.x, y: p.y };
    });
    const last = element.getPointAtLength(length), before = element.getPointAtLength(Math.max(0,length-1));
    path.geometry = { points, angle: Math.atan2(last.y-before.y,last.x-before.x)*180/Math.PI };
    return path.geometry;
  }
  function referenceGeometry(edge, reference) {
    const pose = placements.get(reference.owner);
    const source = screen(positions.get(edge.source.id)), target = screen(positions.get(edge.target.id));
    function same(a,b) { return ["x","y","w","h"].every(key => Math.abs(a[key]-b[key]) < .002); }
    return reference.paths.map((path, index) => {
      const geometry = cachedRoute(path), [scale, tx, ty] = path.transform;
      const k = camera.k * pose.k * scale;
      const x = camera.x+camera.k*(pose.x+pose.k*tx), y = camera.y+camera.k*(pose.y+pose.k*ty);
      const points = geometry.points.map(p => ({ x:x+k*p.x, y:y+k*p.y }));
      const first = points[0], last = points[points.length-1];
      if (same(source,edge.source.rect) && same(target,edge.target.rect))
        return { key:edge.key+":"+index, d:path.d, transform:`translate(${x},${y}) scale(${k})`, tip:last, angle:geometry.angle, routing:"reference" };
      function anchor(p, original, rect) {
        return { x:rect.x+(p.x-original.x)/original.w*rect.w, y:rect.y+(p.y-original.y)/original.h*rect.h };
      }
      const a = anchor(first, source, edge.source.rect), b = anchor(last, target, edge.target.rect);
      const warped = points.map((p,i) => {
        const t = i/(points.length-1);
        return { x:p.x+(a.x-first.x)*(1-t)+(b.x-last.x)*t, y:p.y+(a.y-first.y)*(1-t)+(b.y-last.y)*t };
      });
      const tip = warped[warped.length-1], before = warped[warped.length-2];
      return { key:edge.key+":"+index, d:warped.map((p,i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" "),
        transform:null, tip, angle:Math.atan2(tip.y-before.y,tip.x-before.x)*180/Math.PI, routing:"blended" };
    });
  }
  function connectionsFor(now) {
    const cache = new Map(), grouped = new Map(), relevant = selected || hovered;
    const resolve = id => {
      if (!cache.has(id)) {
        const endpoint = frontierEndpoint(id, now);
        cache.set(id, endpoint ? { id: endpoint.id, rect: screen(endpoint.rect) } : null);
      }
      return cache.get(id);
    };
    for (const relation of data.relationships) {
      // The map shows the focused region's complete local structure. Outside
      // dependencies are explicit in the dock, not stretched to viewport edges.
      if (!within(relation.source, current) || !within(relation.target, current)) continue;
      const source = resolve(relation.source), target = resolve(relation.target);
      if (!source || !target || source.id === target.id) continue;
      if (within(source.id, target.id) || within(target.id, source.id)) continue;
      const active = relevant && (within(relation.source, relevant) || within(relation.target, relevant));
      if (!active && (!allConnections || connectionStrength === 0)) continue;
      const key = JSON.stringify([source.id, target.id]);
      let edge = grouped.get(key);
      if (!edge) { edge = { key, source, target, active: false, count: 0 }; grouped.set(key, edge); }
      edge.active ||= !!active; edge.count += relation.count;
    }
    const edges = [];
    for (const edge of grouped.values()) {
      const reference = referenceRoutes.get(edge.key);
      if (reference) {
        for (const route of referenceGeometry(edge, reference)) edges.push({ ...edge, ...route,
          color:edge.active ? "#087f75" : (data.build.colorArrows ? entities[edge.source.id].color : "#64748b"),
          opacity:edge.active ? .92 : connectionStrength*(relevant ? .5 : 1), cross:false });
        continue;
      }
      const sr = edge.source.rect, tr = edge.target.rect;
      const sc = { x: sr.x+sr.w/2, y: sr.y+sr.h/2 }, tc = { x: tr.x+tr.w/2, y: tr.y+tr.h/2 };
      const a = boundary(sr, tc), b = boundary(tr, sc);
      if (Math.hypot(a.x-b.x, a.y-b.y) < 8) continue;
      const dx = b.x-a.x, dy = b.y-a.y, bend = Math.min(90, Math.hypot(dx,dy)*.24);
      const horizontal = Math.abs(dx) >= Math.abs(dy);
      const c1 = horizontal ? { x: a.x+Math.sign(dx)*bend, y: a.y } : { x: a.x, y: a.y+Math.sign(dy)*bend };
      const c2 = horizontal ? { x: b.x-Math.sign(dx)*bend, y: b.y } : { x: b.x, y: b.y-Math.sign(dy)*bend };
      edges.push({ ...edge, d: `M${a.x},${a.y}C${c1.x},${c1.y} ${c2.x},${c2.y} ${b.x},${b.y}`,
        tip: b, angle: Math.atan2(b.y-c2.y,b.x-c2.x)*180/Math.PI,
        color: edge.active ? "#087f75" : (data.build.colorArrows ? entities[edge.source.id].color : "#64748b"),
        opacity: edge.active ? .92 : connectionStrength * (relevant ? .5 : 1),
        cross: entities[edge.source.id].parent !== entities[edge.target.id].parent });
    }
    return edges.sort((a,b) => Number(a.active)-Number(b.active));
  }
  function requestPaint() { if (!frame) frame = requestAnimationFrame(paint); }
  function paint(time) {
    frame = 0; const started = performance.now(), size = bounds();
    if (lastPaint && time - lastPaint < 120) { metrics.frames.push(time - lastPaint); if (metrics.frames.length > 240) metrics.frames.shift(); }
    lastPaint = time; chooseRegion(size);
    const nodes = []; visibleScene(root, 1, nodes, size);
    updateFrontier(performance.now());
    const edges = connectionsFor(performance.now());
    updateOutside();
    world.attr("transform", camera.toString());
    // Paint containment backgrounds first, routes second, and labels above both.
    world.selectAll("path.node-card").data(nodes, d => d.id).join("path")
      .attr("class", d => "node-card" + (selected === d.id || current === d.id ? " selected" : ""))
      .attr("data-entity", d => d.id).attr("data-kind", d => d.entity.kind).attr("d", cardPath)
      .attr("fill", d => tint(typeColor(d.entity), d.expansion > .8 ? .025 : .10))
      .attr("stroke", d => selected === d.id || current === d.id ? "#087f75" : tint(typeColor(d.entity), .6))
      .attr("opacity", d => d.opacity).attr("role", "button").attr("aria-label", d => d.entity.name)
      .on("click", (event, d) => { event.stopPropagation(); select(d.id); })
      .on("dblclick", (event, d) => { event.preventDefault(); event.stopPropagation(); navigate(d.id); })
      .on("mouseenter", (_, d) => { if (!gesturing) { hovered = d.id; requestPaint(); } })
      .on("mouseleave", () => { hovered = null; requestPaint(); }).order();
    labels.selectAll("path.edge-route").data(edges, d => d.key).join("path").attr("class", "edge-route")
      .attr("data-source", d => d.source.id).attr("data-target", d => d.target.id)
      .attr("data-count", d => d.count).attr("data-active", d => d.active).attr("data-routing", d => d.routing || "dynamic")
      .attr("d", d => d.d).attr("transform", d => d.transform || null).attr("stroke", d => d.color).attr("stroke-width", d => d.active ? 2 : 1.2)
      .attr("stroke-dasharray", d => d.cross ? "5 4" : null).attr("opacity", d => d.opacity).order();
    labels.selectAll("path.edge-head").data(edges, d => d.key).join("path")
      .attr("class", "edge-head").attr("d", "M -6 -3 L 0 0 L -6 3")
      .attr("transform", d => `translate(${d.tip.x},${d.tip.y}) rotate(${d.angle})`)
      .attr("fill", "none").attr("stroke", d => d.color).attr("stroke-width", 1.5).attr("opacity", d => d.opacity).order();
    // Screen-space text stays crisp and readable even at deep fractional coordinates.
    const readable = nodes.filter(d => d.screen.w > 62 && d.screen.h > 27 && d.opacity > .15);
    const groups = labels.selectAll("g.node-label").data(readable, d => d.id).join(enter => {
      const group = enter.append("g").attr("class", "node-label");
      group.append("rect").attr("class", "label-backdrop");
      group.append("text").attr("class", "type-badge"); group.append("text").attr("class", "name"); group.append("text").attr("class", "meta");
      return group;
    }).attr("transform", d => `translate(${d.screen.x + 12},${d.screen.y + (d.entity.children.length ? 20 : d.screen.h / 2 - 2)})`)
      .attr("opacity", d => d.opacity * ramp(d.screen.w, 62, 100)).order();
    groups.select("rect").attr("x", -5).attr("y", -15)
      .attr("width", d => Math.min(d.screen.w - 14, Math.max(80, d.entity.label.length * 7 + 42)))
      .attr("height", d => d.screen.h > 52 ? 39 : 23).attr("rx", 5)
      .attr("fill", d => tint(typeColor(d.entity), .025)).attr("opacity", .96);
    groups.select("text.type-badge").attr("fill", d => typeColor(d.entity)).attr("font-size", 12).attr("font-weight", 750).text(d => icon(d.entity));
    groups.select("text.name").attr("x", 20).attr("fill", "#263d49").attr("font-size", 13).attr("font-weight", 600)
      .text(d => truncate(d.entity.label, Math.floor((d.screen.w - 43) / 6.4)));
    groups.select("text.meta").attr("x", 20).attr("y", 16).attr("font-size", 10).attr("fill", "#7a8c97")
      .text(d => d.screen.h > 52 ? `${kindLabel(d.entity)}${d.entity.children.length ? ` · ${d.entity.children.length} inside` : ""}` : "");
    metrics.renderedNodes = nodes.length;
    metrics.paints.push(performance.now() - started); if (metrics.paints.length > 240) metrics.paints.shift();
    $("zoom-value").textContent = `${Math.round(camera.k / fitTransform(regionRect(current)).k * 100)}%`;
    updateMinimap();
    if (metrics.usableAt === null) metrics.usableAt = performance.now();
  }
  function truncate(value, length) { return value.length > length ? value.slice(0, Math.max(1, length - 1)) + "…" : value; }
  function entityButton(id, subtitle, onClick) {
    const e = entities[id], button = document.createElement("button"); button.className = "entity-row";
    button.dataset.entity = id; button.title = e.name; button.setAttribute("aria-label", e.name);
    const badge = document.createElement("span"); badge.className = "kind-icon"; badge.textContent = icon(e);
    badge.dataset.kind = e.kind; badge.style.background = tint(typeColor(e), .12); badge.style.color = typeColor(e);
    const text = document.createElement("span"); text.className = "entity-text";
    const strong = document.createElement("strong"); strong.textContent = e.label; text.append(strong);
    const small = document.createElement("small"); small.textContent = subtitle || kindLabel(e); text.append(small);
    button.append(badge, text);
    if (e.children.length) { const arrow = document.createElement("span"); arrow.className = "chevron"; arrow.textContent = "›"; button.append(arrow); }
    button.addEventListener("click", () => onClick ? onClick(id) : e.children.length ? navigate(id) : select(id));
    button.addEventListener("dblclick", () => { if (!e.children.length && !onClick) navigate(id); });
    button.addEventListener("keydown", event => { if (event.key === "Enter" && !onClick) { event.preventDefault(); navigate(id); } });
    return button;
  }
  function select(id) { selected = id; outsideOpen = false; updateSidebar(); requestPaint(); }
  for (const edge of data.relationships) {
    if (!incoming.has(edge.target)) incoming.set(edge.target, []);
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    incoming.get(edge.target).push(edge); outgoing.get(edge.source).push(edge);
  }
  function relationshipsFor(id, direction) {
    const result = new Map(), index = direction === "out" ? outgoing : incoming;
    function visit(owner) {
      for (const edge of index.get(owner) || []) {
        const target = direction === "out" ? edge.target : edge.source;
        if (target !== id && within(target, id)) continue;
        const item = result.get(target) || { id: target, kinds: new Set(), count: 0 };
        item.kinds.add(edge.kind); item.count += edge.count; result.set(target, item);
      }
      entities[owner].children.forEach(visit);
    }
    visit(id); return [...result.values()].sort((a, b) => b.count - a.count || a.id.localeCompare(b.id));
  }
  function updateSidebar() {
    $("list-heading").textContent = current === root ? "EXPLORE" : entities[current].kind.toUpperCase();
    $("child-count").textContent = entities[current].children.length;
    $("children").replaceChildren(...entities[current].children.map(id => {
      const e = entities[id]; const button = entityButton(id, `${kindLabel(e)}${e.children.length ? ` · ${e.children.length} inside` : ""}`);
      button.classList.toggle("active", id === selected); return button;
    }));
    $("inspector").hidden = !selected;
    if (!selected) return;
    const e = entities[selected];
    $("selection-kind").textContent = kindLabel(e); $("selection-name").textContent = e.name;
    $("source").textContent = e.file ? `${e.file}${e.line ? `:${e.line}` : ""}` : "";
    $("enter-selection").textContent = e.children.length ? "Enter region" : "Locate on map";
    const uses = relationshipsFor(selected, "out"), usedBy = relationshipsFor(selected, "in");
    $("uses").querySelector("span").textContent = uses.length;
    $("used-by").querySelector("span").textContent = usedBy.length;
    $("uses").setAttribute("aria-pressed", relationDirection === "out");
    $("used-by").setAttribute("aria-pressed", relationDirection === "in");
    const items = relationDirection === "out" ? uses : usedBy;
    $("relationships").replaceChildren(...items.map(item => entityButton(item.id,
      `${within(item.id, current) ? "" : "Outside · "}${entities[item.id].name} · ${[...item.kinds].join(", ")}`, id => navigate(id, true, true))));
    if (!items.length) {
      const p = document.createElement("p"); p.className = "relationship-note";
      p.textContent = `No ${relationDirection === "out" ? "outgoing" : "incoming"} relationships in this scan.`;
      $("relationships").append(p);
    }
  }
  function updateCrumbs() {
    const fragment = document.createDocumentFragment();
    ancestors(current).forEach((id, index) => {
      if (index) { const divider = document.createElement("span"); divider.textContent = "/"; fragment.append(divider); }
      const button = document.createElement("button"); button.textContent = entities[id].label;
      button.addEventListener("click", () => navigate(id)); fragment.append(button);
    });
    $("breadcrumbs").replaceChildren(fragment);
    $("level-hint").textContent = current === root ? "Your code, from above" : `${entities[current].kind} · ${entities[current].children.length} inside`;
  }
  // Arrowheads render in screen space at every zoom.
  const miniScale = Math.min(164 / rootRect.w, 94 / rootRect.h);
  const miniX = 90 - (rootRect.x + rootRect.w / 2) * miniScale;
  const miniY = 55 - (rootRect.y + rootRect.h / 2) * miniScale;
  d3.select("#mini-nodes").selectAll("rect").data([...positions.values()].filter(r => r.depth < 2)).join("rect")
    .attr("x", r => r.x * miniScale + miniX).attr("y", r => r.y * miniScale + miniY)
    .attr("width", r => r.w * miniScale).attr("height", r => r.h * miniScale)
    .attr("fill", r => tint(typeColor(entities[r.id]), .10)).attr("stroke", r => tint(typeColor(entities[r.id]), .35)).attr("stroke-width", .6).attr("rx", 2);
  function updateMinimap() {
    const size = bounds();
    d3.select("#mini-viewport").attr("x", -camera.x / camera.k * miniScale + miniX)
      .attr("y", -camera.y / camera.k * miniScale + miniY)
      .attr("width", size.w / camera.k * miniScale).attr("height", size.h / camera.k * miniScale);
  }
  $("minimap").addEventListener("click", event => {
    const rect = $("minimap").getBoundingClientRect(), size = bounds(); remember(); lockFrontier(); pinnedRegion = true; candidate = null; focusPoint = null;
    const x = ((event.clientX - rect.left) * 180 / rect.width - miniX) / miniScale;
    const y = ((event.clientY - rect.top) * 110 / rect.height - miniY) / miniScale;
    applyCamera(d3.zoomIdentity.translate(size.w / 2 - camera.k * x, size.h / 2 - camera.k * y).scale(camera.k), 300);
  });
  function panWheel(event) { return !event.ctrlKey && event.deltaMode === 0 && (Math.abs(event.deltaX) > 0 || Math.abs(event.deltaY) < 40); }
  const zoom = d3.zoom().scaleExtent([.00001, 1e12]).interpolate(d3.interpolateZoom.rho(.4))
    .filter(event => event.type === "wheel" ? !panWheel(event) : event.type !== "dblclick" && !event.button)
    .wheelDelta(event => -clamp(event.deltaY, -160, 160) * (event.deltaMode === 1 ? .03 : .002) * (event.ctrlKey ? 4 : 1))
    .on("start.map", event => { if (event.sourceEvent) {
      gesturePose = { camera, current, selected }; svg.interrupt(); moving = false; gesturing = true; hovered = null;
    } })
    .on("zoom.map", event => {
      const changedScale = Math.abs(Math.log(event.transform.k / camera.k)) > 1e-10;
      camera = event.transform;
      if (changedScale) {
        scaleChangedAt = performance.now(); clearTimeout(frontierTimer);
        frontierTimer = setTimeout(requestPaint, 140);
      }
      if (event.sourceEvent) {
        pinnedRegion = !changedScale;
        if (!changedScale) { candidate = null; lockFrontier(); }
        const input = event.sourceEvent, rect = svg.node().getBoundingClientRect();
        focusPoint = input.type === "wheel" ? { x: input.clientX - rect.left, y: input.clientY - rect.top } : null;
      }
      requestPaint();
    }).on("end.map", () => { gesturing = false; requestPaint(); });
  svg.call(zoom).on("dblclick.zoom", null);
  svg.node().addEventListener("wheel", event => {
    if (!panWheel(event)) return; event.preventDefault(); lockFrontier(); svg.interrupt(); moving = false; pinnedRegion = true; candidate = null; focusPoint = null;
    svg.call(zoom.transform, d3.zoomIdentity.translate(camera.x - event.deltaX, camera.y - event.deltaY).scale(camera.k));
    hovered = null; gesturing = true; clearTimeout(panTimer);
    panTimer = setTimeout(() => { gesturing = false; requestPaint(); }, 160);
  }, { passive: false });
  svg.on("click.clear", event => { if (event.target === svg.node()) { selected = null; updateSidebar(); requestPaint(); } });
  function relativeZoom(factor) { pinnedRegion = false; focusPoint = null; svg.interrupt(); svg.transition().duration(reduced.matches ? 0 : 140).call(zoom.scaleBy, factor); }
  $("zoom-in").onclick = () => relativeZoom(1.35); $("zoom-out").onclick = () => relativeZoom(1 / 1.35);
  $("back").onclick = goBack; $("back").disabled = true;
  $("fit").onclick = () => navigate(current);
  $("enter-selection").onclick = () => { if (selected) navigate(selected); };
  $("connections").onclick = () => {
    allConnections = !allConnections; $("connections").setAttribute("aria-pressed", allConnections);
    $("connections").querySelector("span").textContent = allConnections ? "on" : "off"; requestPaint();
  };
  $("connection-opacity").addEventListener("input", event => {
    connectionStrength = Number(event.target.value) / 100;
    $("connection-value").textContent = `${event.target.value}%`;
    allConnections = connectionStrength > 0;
    $("connections").setAttribute("aria-pressed", allConnections);
    $("connections").querySelector("span").textContent = allConnections ? "on" : "off";
    requestPaint();
  });
  $("outside-toggle").onclick = () => {
    outsideOpen = !outsideOpen;
    if (outsideOpen) {
      document.body.classList.remove("panel-hidden"); $("panel-toggle").setAttribute("aria-expanded", true);
    }
    updateOutside();
    if (outsideOpen) $("outside-panel").scrollIntoView({ block: "nearest" });
    requestPaint();
  };
  $("outside-close").onclick = () => { outsideOpen = false; requestPaint(); };
  $("uses").onclick = () => { relationDirection = "out"; updateSidebar(); };
  $("used-by").onclick = () => { relationDirection = "in"; updateSidebar(); };
  $("search").addEventListener("input", event => {
    const query = event.target.value.trim().toLowerCase(); $("search-results").hidden = !query;
    const result = Object.values(entities).filter(e => e.id !== root && e.name.toLowerCase().includes(query))
      .sort((a, b) => a.name.length - b.name.length || a.name.localeCompare(b.name)).slice(0, 24);
    $("search-results").replaceChildren(...result.map(e => entityButton(e.id, e.name, id => {
      $("search").value = ""; $("search-results").hidden = true; navigate(id); $("canvas").focus();
    })));
    if (query && !result.length) { const p = document.createElement("p"); p.className = "relationship-note"; p.textContent = "No matching entities"; $("search-results").append(p); }
  });
  document.addEventListener("keydown", event => {
    if (event.target.closest("input,textarea,select") || event.target.isContentEditable) {
      if (event.key === "Escape") { $("search").value = ""; $("search-results").hidden = true; $("canvas").focus(); }
      return;
    }
    if (event.ctrlKey || event.metaKey) return;
    if (event.key === "/") { event.preventDefault(); document.body.classList.remove("panel-hidden"); $("panel-toggle").setAttribute("aria-expanded", true); $("search").focus(); }
    else if (event.key === "Escape") { event.preventDefault(); parent(); }
    else if (event.key === "Enter" && selected) { event.preventDefault(); navigate(selected); }
    else if (event.key === "Home") { event.preventDefault(); navigate(home); }
    else if (event.key.toLowerCase() === "f") { event.preventDefault(); navigate(current); }
    else if (event.key === "ArrowLeft" && event.altKey) { event.preventDefault(); goBack(); }
    else if (["+", "=", "-"].includes(event.key)) { event.preventDefault(); relativeZoom(event.key === "-" ? 1 / 1.35 : 1.35); }
    else if (event.key.startsWith("Arrow")) {
      event.preventDefault(); svg.interrupt(); lockFrontier(); hovered = null; pinnedRegion = true; candidate = null; focusPoint = null;
      const dx = event.key === "ArrowLeft" ? 55 : event.key === "ArrowRight" ? -55 : 0;
      const dy = event.key === "ArrowUp" ? 55 : event.key === "ArrowDown" ? -55 : 0;
      svg.call(zoom.transform, d3.zoomIdentity.translate(camera.x + dx, camera.y + dy).scale(camera.k));
    }
  });
  $("panel-toggle").onclick = () => {
    const hidden = document.body.classList.toggle("panel-hidden"); $("panel-toggle").setAttribute("aria-expanded", !hidden);
  };
  if (matchMedia("(max-width:720px)").matches) { document.body.classList.add("panel-hidden"); $("panel-toggle").setAttribute("aria-expanded", false); }
  let previousSize = bounds();
  new ResizeObserver(() => {
    const size = bounds(); svg.interrupt(); lockFrontier(); pinnedRegion = true; candidate = null;
    if (previousSize.w && previousSize.h) svg.call(zoom.transform,
      d3.zoomIdentity.translate(camera.x + (size.w - previousSize.w) / 2,
        camera.y + (size.h - previousSize.h) / 2).scale(camera.k));
    previousSize = size; requestPaint();
  }).observe($("canvas"));
  $("summary").textContent = `${Object.keys(entities).length - 1} entities · ${data.relationships.length} relationships`;
  if (data.diagnostics.length) { $("diagnostics").hidden = false; $("diagnostics").textContent = `${data.diagnostics.length} scan or layout notes. Some relationships may be incomplete.`; }
  $("empty").hidden = entities[root].children.length !== 0;
  updateSidebar(); updateCrumbs();
  // Skip a dominant namespace wrapper on first entry, while retaining it in breadcrumbs.
  function descendantCount(id) { return 1 + entities[id].children.reduce((sum, child) => sum + descendantCount(child), 0); }
  const principal = entities[root].children.find(id => entities[id].kind === "package"
    && entities[id].children.length >= 3 && descendantCount(id) > Object.keys(entities).length * .6);
  const home = principal || root;
  current = home; pinnedRegion = true; updateSidebar(); updateCrumbs();
  const initial = fitTransform(regionRect(home)); 
  $("overview").onclick = () => navigate(home); $("brand").onclick = event => { event.preventDefault(); navigate(home); };
  zoom.scaleExtent([fitTransform(rootRect).k * .2, 1e12]); svg.call(zoom.transform, initial); requestPaint();
  window.__ARCHER_MAP_STATE__ = () => ({ current, selected, camera: { k: camera.k, x: camera.x, y: camera.y },
    visible: [...document.querySelectorAll(".node-card")].map(n => n.dataset.entity), history: history.length,
    connectionScale, frontier: [...frontierTo].map(([id, endpoint]) => [id, endpoint ? endpoint.id : null]) });
})();
