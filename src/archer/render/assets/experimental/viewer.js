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
    } else if (performance.now() - candidateSince >= 110) {
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
  // Resolve canonical relationships against the readable frontier, independent of
  // the current breadcrumb. Blend endpoints into children as their detail appears.
  function resolvedEndpoint(id, nodeMap) {
    let result = null;
    for (const owner of ancestors(id)) {
      let node = nodeMap.get(owner);
      if (!node && positions.has(owner)) {
        const r = screen(positions.get(owner)), parentNode = nodeMap.get(entities[owner].parent);
        if (!intersects(r, bounds()) && parentNode && parentNode.expansion > .01)
          node = { screen: r, opacity: parentNode.opacity * parentNode.expansion, external: true };
      }
      if (!node || node.screen.w < 62 || node.screen.h < 27) continue;
      const r = node.screen, t = result ? ramp(node.opacity, .15, .85) : 1;
      if (t <= 0) continue;
      const prior = result ? result.rect : r;
      result = { id: owner, external: node.external || false, rect: { x: prior.x + (r.x - prior.x) * t, y: prior.y + (r.y - prior.y) * t,
        w: prior.w + (r.w - prior.w) * t, h: prior.h + (r.h - prior.h) * t } };
    }
    if (result) return result;
    // An offscreen destination still has an honest, navigable boundary port.
    for (const owner of ancestors(id).slice(1)) {
      const rect = positions.get(owner); if (!rect) continue;
      const r = screen(rect);
      if (r.w >= 62 && r.h >= 27) return { id: owner, rect: r, external: true };
    }
    return null;
  }
  function boundary(rect, toward) {
    const cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
    const dx = toward.x - cx, dy = toward.y - cy;
    const t = 1 / Math.max(Math.abs(dx) / Math.max(1, rect.w / 2), Math.abs(dy) / Math.max(1, rect.h / 2), 1e-9);
    return { x: cx + dx * t, y: cy + dy * t };
  }
  function connectionsFor(nodes, size) {
    const nodeMap = new Map(nodes.map(n => [n.id, n])), cache = new Map(), grouped = new Map();
    const resolve = id => { if (!cache.has(id)) cache.set(id, resolvedEndpoint(id, nodeMap)); return cache.get(id); };
    const relevant = selected || hovered;
    for (const relation of data.relationships) {
      const source = resolve(relation.source), target = resolve(relation.target);
      if (!source || !target || source.id === target.id || (source.external && target.external)) continue;
      // Containment is shown by nesting, never by dependency arrows.
      if (within(source.id, target.id) || within(target.id, source.id)) continue;
      const active = relevant && (within(relation.source, relevant) || within(relation.target, relevant));
      if (!active && (!allConnections || connectionStrength === 0)) continue;
      const key = JSON.stringify([source.id, target.id]);
      let edge = grouped.get(key);
      if (!edge) { edge = { key, source, target, active: false, count: 0, kinds: new Set() }; grouped.set(key, edge); }
      edge.active ||= !!active; edge.count += relation.count; edge.kinds.add(relation.kind);
    }
    const ports = new Map(), edges = [];
    function clipped(point, endpoint, other, active) {
      const margin = 42, bottom = size.h - 82;
      if (!endpoint.external && point.x >= margin && point.x <= size.w-margin && point.y >= 72 && point.y <= bottom) return point;
      const cx = clamp(other.x, margin, size.w-margin), cy = clamp(other.y, 72, bottom);
      const dx = point.x-cx, dy = point.y-cy;
      let t = 1;
      if (dx > 0) t = Math.min(t, (size.w-margin-cx)/dx);
      if (dx < 0) t = Math.min(t, (margin-cx)/dx);
      if (dy > 0) t = Math.min(t, (bottom-cy)/dy);
      if (dy < 0) t = Math.min(t, (72-cy)/dy);
      const result = { x: clamp(cx+dx*t, margin, size.w-margin), y: clamp(cy+dy*t, 72, bottom) };
      if (!ports.has(endpoint.id)) ports.set(endpoint.id, { ...result, id: endpoint.id, active });
      else ports.get(endpoint.id).active ||= active;
      return result;
    }
    for (const edge of grouped.values()) {
      const sr = edge.source.rect, tr = edge.target.rect;
      const sc = { x: sr.x+sr.w/2, y: sr.y+sr.h/2 }, tc = { x: tr.x+tr.w/2, y: tr.y+tr.h/2 };
      let a = boundary(sr, tc), b = boundary(tr, sc);
      a = clipped(a, edge.source, tc, edge.active); b = clipped(b, edge.target, sc, edge.active);
      edge.a = a; edge.b = b;
    }
    // Keep boundary names readable and reserve one landing point per outside region.
    const rails = { left: [], right: [], top: [], bottom: [] };
    for (const port of ports.values()) {
      const distances = { left: Math.abs(port.x-42), right: Math.abs(port.x-(size.w-42)), top: Math.abs(port.y-72), bottom: Math.abs(port.y-(size.h-82)) };
      const side = Object.keys(distances).sort((a,b) => distances[a]-distances[b])[0];
      rails[side].push(port);
    }
    for (const [side, entries] of Object.entries(rails)) {
      const vertical = side === "left" || side === "right", axis = vertical ? "y" : "x";
      const lo = vertical ? 110 : 92, hi = vertical ? size.h-104 : size.w-92;
      const gap = Math.min(vertical ? 32 : 154, (hi-lo)/Math.max(1,entries.length-1));
      entries.sort((a,b) => a[axis]-b[axis] || a.id.localeCompare(b.id));
      entries.forEach((port,index) => {
        port[axis] = clamp(port[axis], lo+index*gap, hi-(entries.length-index-1)*gap);
        if (index) port[axis] = Math.max(port[axis], entries[index-1][axis]+gap);
        if (vertical) port.x = side === "left" ? 82 : size.w-82;
        else port.y = side === "top" ? 72 : size.h-82;
      });
    }
    for (const edge of grouped.values()) {
      let a = edge.a, b = edge.b;
      const portRect = port => ({ x: port.x-72, y: port.y-12, w: 144, h: 24 });
      if (ports.has(edge.source.id)) a = boundary(portRect(ports.get(edge.source.id)), b);
      if (ports.has(edge.target.id)) b = boundary(portRect(ports.get(edge.target.id)), a);
      if (Math.hypot(a.x-b.x, a.y-b.y) < 8) continue;
      const dx = b.x-a.x, dy = b.y-a.y, bend = Math.min(90, Math.hypot(dx,dy)*.24);
      const horizontal = Math.abs(dx) >= Math.abs(dy);
      const c1 = horizontal ? { x: a.x+Math.sign(dx)*bend, y: a.y } : { x: a.x, y: a.y+Math.sign(dy)*bend };
      const c2 = horizontal ? { x: b.x-Math.sign(dx)*bend, y: b.y } : { x: b.x, y: b.y-Math.sign(dy)*bend };
      edges.push({ ...edge, d: `M${a.x},${a.y}C${c1.x},${c1.y} ${c2.x},${c2.y} ${b.x},${b.y}`,
        tip: b, angle: Math.atan2(b.y-c2.y,b.x-c2.x)*180/Math.PI,
        color: edge.active ? "#087f75" : (data.build.colorArrows ? entities[edge.source.id].color : "#64748b"), opacity: edge.active ? .92 : connectionStrength * (relevant ? .5 : 1),
        cross: edge.source.external || edge.target.external || entities[edge.source.id].parent !== entities[edge.target.id].parent });
    }
    return { edges: edges.sort((a,b) => Number(a.active)-Number(b.active)), ports: [...ports.values()] };
  }
  function requestPaint() { if (!frame) frame = requestAnimationFrame(paint); }
  function paint(time) {
    frame = 0; const started = performance.now(), size = bounds();
    if (lastPaint && time - lastPaint < 120) { metrics.frames.push(time - lastPaint); if (metrics.frames.length > 240) metrics.frames.shift(); }
    lastPaint = time; chooseRegion(size);
    const nodes = []; visibleScene(root, 1, nodes, size);
    const { edges, ports } = connectionsFor(nodes, size);
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
      .on("mouseenter", (_, d) => { hovered = d.id; requestPaint(); })
      .on("mouseleave", () => { hovered = null; requestPaint(); }).order();
    labels.selectAll("path.edge-route").data(edges, d => d.key).join("path").attr("class", "edge-route")
      .attr("data-source", d => d.source.id).attr("data-target", d => d.target.id)
      .attr("data-count", d => d.count).attr("data-active", d => d.active)
      .attr("d", d => d.d).attr("stroke", d => d.color).attr("stroke-width", d => d.active ? 2 : 1.2)
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
    const portGroups = labels.selectAll("g.boundary-port").data(ports, d => d.id).join(enter => {
      const group = enter.append("g").attr("class", "boundary-port").attr("role", "button").attr("tabindex", 0);
      group.append("rect").attr("rx", 5); group.append("text"); group.append("title"); return group;
    }).attr("transform", d => `translate(${clamp(d.x, 82, size.w-82)},${d.y})`)
      .attr("data-entity", d => d.id).attr("aria-label", d => `Follow ${entities[d.id].name}`)
      .on("click", (event,d) => { event.stopPropagation(); navigate(d.id, true, true); })
      .on("keydown", (event,d) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); navigate(d.id, true, true); } }).order();
    portGroups.select("rect").attr("x", -72).attr("y", -12).attr("width", 144).attr("height", 24)
      .attr("fill", "#fff").attr("stroke", d => d.active ? "#087f75" : "#c8d3dc");
    portGroups.select("text").attr("text-anchor", "middle").attr("y", 4).attr("font-size", 10)
      .attr("fill", d => d.active ? "#087f75" : "#526676").text(d => `${icon(entities[d.id])} ${truncate(entities[d.id].label, 19)}`);
    portGroups.select("title").text(d => entities[d.id].name);
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
  function select(id) { selected = id; updateSidebar(); requestPaint(); }
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
      `${entities[item.id].name} · ${[...item.kinds].join(", ")}`, id => navigate(id, true, true))));
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
    const rect = $("minimap").getBoundingClientRect(), size = bounds(); remember();
    const x = ((event.clientX - rect.left) * 180 / rect.width - miniX) / miniScale;
    const y = ((event.clientY - rect.top) * 110 / rect.height - miniY) / miniScale;
    applyCamera(d3.zoomIdentity.translate(size.w / 2 - camera.k * x, size.h / 2 - camera.k * y).scale(camera.k), 300);
  });
  function panWheel(event) { return !event.ctrlKey && event.deltaMode === 0 && (Math.abs(event.deltaX) > 0 || Math.abs(event.deltaY) < 40); }
  const zoom = d3.zoom().scaleExtent([.00001, 1e12]).interpolate(d3.interpolateZoom.rho(.4))
    .filter(event => event.type === "wheel" ? !panWheel(event) : event.type !== "dblclick" && !event.button)
    .wheelDelta(event => -clamp(event.deltaY, -160, 160) * (event.deltaMode === 1 ? .03 : .002) * (event.ctrlKey ? 4 : 1))
    .on("start.map", event => { if (event.sourceEvent) {
      gesturePose = { camera, current, selected }; svg.interrupt(); moving = false;
    } })
    .on("zoom.map", event => {
      camera = event.transform;
      if (event.sourceEvent) {
        pinnedRegion = false;
        const input = event.sourceEvent, rect = svg.node().getBoundingClientRect();
        focusPoint = input.type === "wheel" ? { x: input.clientX - rect.left, y: input.clientY - rect.top } : null;
      }
      requestPaint();
    });
  svg.call(zoom).on("dblclick.zoom", null);
  svg.node().addEventListener("wheel", event => {
    if (!panWheel(event)) return; event.preventDefault(); svg.interrupt(); moving = false; pinnedRegion = false; focusPoint = null;
    svg.call(zoom.transform, d3.zoomIdentity.translate(camera.x - event.deltaX, camera.y - event.deltaY).scale(camera.k));
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
      event.preventDefault(); svg.interrupt(); pinnedRegion = false; focusPoint = null;
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
    const size = bounds(); svg.interrupt();
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
    visible: [...document.querySelectorAll(".node-card")].map(n => n.dataset.entity), history: history.length });
})();
