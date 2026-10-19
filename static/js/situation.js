/* Situation: draw the world, pin today's stories on it, list them beside it. */
(() => {
    const ROOT = window.APP_ROOT || '';
    const url = (p) => `${ROOT}${p}`;
    const NS = 'http://www.w3.org/2000/svg';
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    // Only ordinary web links become links (the server checks too).
    const webLink = (u) => (/^https?:\/\//i.test(String(u || '')) ? u : null);
    const narrow = window.matchMedia('(max-width: 860px)');

    const STALE_MS = 30 * 60 * 1000;   // older news than this gets a notice
    const HIT_PX = 11;                 // pin tap radius on screen (22px across)
    const MIN_DOT_PX = 5;              // smallest pin radius drawn, on screen

    let world = null;
    let data = null;
    let openId = null;
    let filter = null;                 // { label, isos?: Set, ids?: Set, track?: [] } when the list shows one place
    let failure = null;                // 'offline' | 'signed-out' while refreshes fail
    // While a headline is open and the map went to it, where the map was before;
    // null once the user moves the map themselves (then closing leaves it be).
    let following = null;

    function el(tag, attrs = {}, parent) {
        const node = document.createElementNS(NS, tag);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        if (parent) parent.appendChild(node);
        return node;
    }

    function ago(iso) {
        if (!iso) return '';
        const mins = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
        if (mins < 60) return `${mins}m`;
        const h = Math.round(mins / 60);
        return h < 48 ? `${h}h` : `${Math.round(h / 24)}d`;
    }

    const clockTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

    // ---------- map ----------
    //
    // The view is a viewBox in map units (the world is world.width across). Pinch, scroll,
    // drag, double-tap and the buttons move it. Closer than DETAIL_W, each country in view
    // loads its regions and biggest cities, and stories show as the places they name.

    const DETAIL_W = 260;              // closer than this: regions, cities, places in the news
    const LABEL_W = 30;                // closer still: region names
    const MIN_W = 6;                   // the closest zoom (a few hundred km across)
    let view = null;                   // the user's { x, y, w, h }; null = the automatic view
    const details = new Map();         // iso → the country's region file once loaded
    const loading = new Set();
    const boxes = new Map();           // iso → [x0, y0, x1, y1] of its outline on the world map
    let drawing = false;

    function drawMap() {
        const svg = $('map');
        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
        svg.innerHTML = '';
        el('path', { d: world.outline, class: 'sphere' }, svg);
        el('path', { d: world.graticule, class: 'grid' }, svg);
        const land = el('g', { id: 'land' }, svg);
        for (const c of world.countries) {
            const p = el('path', { d: c.d, class: 'land', 'data-iso': c.iso }, land);
            const t = el('title', {}, p);
            t.textContent = c.name;
        }
        for (const id of ['detail', 'refs', 'track', 'pins']) el('g', { id }, svg);
    }

    function box(iso) {
        if (!boxes.has(iso)) {
            const p = document.querySelector(`#land .land[data-iso="${iso}"]`);
            if (!p) return null;
            const b = p.getBBox();
            boxes.set(iso, [b.x, b.y, b.x + b.width, b.y + b.height]);
        }
        return boxes.get(iso);
    }

    /** Screen pixels to map units, and the shape of the map on screen. */
    function screen() {
        const r = $('map').getBoundingClientRect();
        return { r, aspect: r.width ? r.height / r.width : world.height / world.width };
    }

    /** The whole world, or on a phone the part of it with today's pins. */
    function autoView() {
        const { aspect } = screen();
        const W = world.width;
        // All of it: full width in a tall panel, full height in a wide one.
        const whole = aspect >= world.height / W
            ? { x: 0, y: (world.height - W * aspect) / 2, w: W, h: W * aspect }
            : { x: (W - world.height / aspect) / 2, y: 0, w: world.height / aspect, h: world.height };
        const places = data ? countries() : [];
        if (!narrow.matches || !places.length) return whole;
        const xs = places.map((c) => c.top.x);
        const ys = places.map((c) => c.top.y);
        return fit([Math.min(...xs) - 60, Math.min(...ys) - 30, Math.max(...xs) + 60, Math.max(...ys) + 30], 560);
    }

    const current = () => view || autoView();

    /** The view showing a box, with a margin, no closer than `minW` across. */
    function fit([x0, y0, x1, y1], minW = 14) {
        const { aspect } = screen();
        let w = Math.max((x1 - x0) * 1.5, ((y1 - y0) * 1.5) / aspect, minW);
        w = Math.min(w, world.width);
        const h = w * aspect;
        return clamp({ x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - h / 2, w, h });
    }

    function clamp(v) {
        const { aspect } = screen();
        const w = Math.min(Math.max(v.w, MIN_W), world.width);
        const h = w * aspect;
        const x = Math.min(Math.max(v.x, -w * 0.25), world.width - w * 0.75);
        const y = Math.min(Math.max(v.y, -h * 0.25), world.height - h * 0.75);
        return { x, y, w, h };
    }

    let tween = 0;                     // the running animation; a newer view cancels it

    let goal = null;                   // where the map is headed (the view, once any animation ends)

    function setView(v, animate = false) {
        const to = v && clamp(v);
        goal = to;
        const mine = ++tween;
        if (!animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            view = to;
            return render();
        }
        const from = current();
        const end = to || autoView();
        const t0 = performance.now();
        const step = (t) => {
            if (mine !== tween) return;
            const k = Math.min(1, Math.max(0, (t - t0) / 350));   // a frame's time can precede t0
            const e = 1 - (1 - k) ** 3;
            view = { x: from.x + (end.x - from.x) * e, y: from.y + (end.y - from.y) * e,
                w: from.w + (end.w - from.w) * e, h: from.h + (end.h - from.h) * e };
            render();
            if (k < 1) requestAnimationFrame(step);
            else { view = to; render(); }
        };
        requestAnimationFrame(step);
    }

    function zoomAt(mx, my, factor) {
        const v = current();
        const w = Math.min(Math.max(v.w / factor, MIN_W), world.width);
        const h = w * (v.h / v.w);
        setView({ x: mx - (mx - v.x) * (w / v.w), y: my - (my - v.y) * (h / v.h), w, h });
    }

    function toMap(clientX, clientY) {
        const v = current();
        const { r } = screen();
        return [v.x + ((clientX - r.left) / r.width) * v.w, v.y + ((clientY - r.top) / r.height) * v.h];
    }

    function render() {
        if (drawing || !world) return;
        drawing = true;
        requestAnimationFrame(() => {
            drawing = false;
            const v = current();
            $('map').setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`);
            const upp = v.w / (screen().r.width || 1);            // map units per screen pixel
            const close = v.w < DETAIL_W;
            const theatre = document.querySelector('.theatre');
            theatre.classList.toggle('zoomed', !!view);
            document.querySelector('.theatre').classList.toggle('close', close);
            drawDetail(v, close);
            // The news first, then the week's places, then cities for orientation: each gives way to the last.
            const taken = drawPins(upp, close);
            drawTrack(upp, taken);
            drawRefs(v, upp, close, taken);
        });
    }

    function inView(b, v) {
        return b && b[0] < v.x + v.w && b[2] > v.x && b[1] < v.y + v.h && b[3] > v.y;
    }

    async function loadDetail(iso) {
        if (details.has(iso) || loading.has(iso)) return details.get(iso);
        loading.add(iso);
        try {
            const res = await fetch(url(`/static/map/regions/${iso}.json`));
            if (res.ok) details.set(iso, await res.json());
        } catch (_) { /* the world map still shows */ }
        loading.delete(iso);
        render();
        return details.get(iso);
    }

    /** Detailed outlines and regions for the countries in view, built once each. */
    function drawDetail(v, close) {
        const layer = $('detail');
        const shown = new Set();
        if (close) {
            for (const c of world.countries) {
                if (!inView(box(c.iso), v)) continue;
                const d = details.get(c.iso);
                if (!d) { loadDetail(c.iso); continue; }
                shown.add(c.iso);
                let g = layer.querySelector(`g[data-iso="${c.iso}"]`);
                if (!g) {
                    g = el('g', { 'data-iso': c.iso }, layer);
                    el('path', { d: d.outline, class: 'land detail-land', 'data-iso': c.iso }, g);
                    const regions = el('g', { class: 'regions' }, g);
                    for (const r of d.regions) {
                        const p = el('path', { d: r.d, class: 'region' }, regions);
                        el('title', {}, p).textContent = r.name;
                    }
                }
            }
        }
        layer.querySelectorAll(':scope > g').forEach((g) => { g.style.display = shown.has(g.dataset.iso) ? '' : 'none'; });
        document.querySelectorAll('#land .land').forEach((p) => p.classList.toggle('covered', shown.has(p.dataset.iso)));
    }

    /** Reference cities, and region names when close, for orientation. */
    function drawRefs(v, upp, close, taken = []) {
        const layer = $('refs');
        layer.innerHTML = '';
        if (!close) return;
        // Fewer, bigger cities the further out: enough to know where you are.
        const minPop = v.w < 15 ? 0 : v.w < 60 ? 250000 : v.w < 120 ? 600000 : 1500000;
        const within = (x, y) => x > v.x && x < v.x + v.w && y > v.y && y < v.y + v.h;
        // Places the news or the week's track already name.
        const onMap = new Set([...document.querySelectorAll('#pins .tag, #track .track-label')].map((t) => t.textContent));
        for (const [iso, d] of details) {
            if (!inView(d.bbox, v)) continue;
            for (const c of d.cities) {
                if (c.pop < minPop || !within(c.x, c.y) || onMap.has(c.name)) continue;
                if (taken.some(([tx, ty, tw, th]) => c.x >= tx && c.x <= tx + tw && c.y >= ty && c.y <= ty + th)) continue;
                el('circle', { cx: c.x, cy: c.y, r: 1.6 * upp, class: 'city' }, layer);
                const spot = labelSpot(c.x, c.y, c.name, taken, upp, 3);
                if (!spot) continue;
                const t = el('text', { x: spot.x, y: spot.y, class: 'city-label', 'text-anchor': spot.anchor, 'data-iso': iso }, layer);
                t.style.fontSize = `${9.5 * upp}px`;
                t.style.strokeWidth = `${2.5 * upp}px`;
                t.textContent = c.name;
            }
        }
        if (v.w >= LABEL_W) return;
        // Region names after the cities, wherever they fit.
        for (const [, d] of details) {
            if (!inView(d.bbox, v)) continue;
            for (const r of d.labels) {
                if (!within(r.x, r.y)) continue;
                const w = r.name.length * 6 * upp;
                const h = 9 * upp;
                const at = [r.x - w / 2, r.y - h / 2, w, h];
                if (taken.some(([tx, ty, tw, th]) => at[0] < tx + tw && tx < at[0] + w && at[1] < ty + th && ty < at[1] + h)) continue;
                taken.push(at);
                const t = el('text', { x: r.x, y: r.y + h * 0.35, class: 'region-label', 'text-anchor': 'middle' }, layer);
                t.style.fontSize = `${8.5 * upp}px`;
                t.textContent = r.name;
            }
        }
    }

    /** An active situation's past week: every place named there, bigger the more
     * often, fading with age. Shown while that situation is picked. */
    function drawTrack(upp, taken = []) {
        const layer = $('track');
        layer.innerHTML = '';
        if (!filter?.track?.length) return;
        const now = Date.now();
        const named = new Set(data.stories.flatMap((s) => (s.spots || []).map((p) => p.name)));
        for (const t of filter.track) {
            const r = (3 + Math.sqrt(t.n) * 2.2) * upp;
            taken.push([t.x - r, t.y - r, 2 * r, 2 * r]);
        }
        for (const [i, t] of filter.track.entries()) {
            const hours = (now - new Date(t.last)) / 3600000;
            const fresh = Math.max(0.25, 1 - hours / 168);
            const r = (3 + Math.sqrt(t.n) * 2.2) * upp;
            const g = el('g', { class: 'track-spot' }, layer);
            el('circle', { cx: t.x, cy: t.y, r, 'fill-opacity': 0.35 * fresh, 'stroke-opacity': fresh }, g);
            el('title', {}, g).textContent = `${t.name}: named in ${t.n} article${t.n > 1 ? 's' : ''} this week, last ${ago(t.last)} ago`;
            if (i < 8 && !named.has(t.name)) {                  // today's pin already says its name
                const spot = labelSpot(t.x, t.y, t.name, taken, upp, r / upp + 3);
                if (spot) {
                    const label = el('text', { x: spot.x, y: spot.y, class: 'track-label', 'text-anchor': spot.anchor }, g);
                    label.style.fontSize = `${10 * upp}px`;
                    label.style.strokeWidth = `${3 * upp}px`;
                    label.textContent = t.name;
                }
            }
        }
    }

    /** Today's stories grouped by country, biggest first: one pin each. */
    function countries(stories = data.stories) {
        const byIso = new Map();
        for (const s of stories) {
            if (s.x == null || !s.iso) continue;
            if (!byIso.has(s.iso)) byIso.set(s.iso, []);
            byIso.get(s.iso).push(s);
        }
        return [...byIso.entries()].map(([iso, stories]) => ({
            iso, stories, x: stories[0].x, y: stories[0].y, top: stories[0], place: stories[0].place || iso,
            kind: stories.some((s) => s.tier === 'major') ? 'flash' : 'priority',
            label: `${iso} ${stories.length > 1 ? `· ${stories.length}` : ''}`.trim(),
        }));
    }

    /** Close up: the places today's stories name, each with its stories; stories that
     * name no place inside a country keep their country pin. */
    function places() {
        const byPlace = new Map();
        const unplaced = [];
        for (const s of data.stories) {
            if (!s.spots?.length) { unplaced.push(s); continue; }
            for (const p of s.spots) {
                const key = `${p.name}|${p.iso}`;
                if (!byPlace.has(key)) byPlace.set(key, { ...p, stories: [] });
                byPlace.get(key).stories.push(s);
            }
        }
        const spots = [...byPlace.values()].map((p) => ({
            ...p, place: p.name, label: p.name, top: p.stories[0],
            kind: p.stories.some((s) => s.tier === 'major') ? 'flash' : 'priority',
        }));
        return [...spots, ...countries(unplaced)];
    }

    /** Today's stories on the map; returns the room their dots and labels take. */
    function drawPins(upp, close) {
        const pins = $('pins');
        if (!pins || !data) return [];
        pins.innerHTML = '';
        document.querySelectorAll('.land').forEach((p) => p.classList.remove('hot-flash', 'hot-priority'));
        for (const c of countries()) {
            document.querySelectorAll(`.land[data-iso="${c.iso}"]`).forEach((p) => p.classList.add(`hot-${c.kind}`));
        }
        const marks = close ? places() : countries();
        // Boxes labels must not cover: every dot, then each label placed (biggest stories first).
        const dot = MIN_DOT_PX * upp;
        const taken = marks.map((m) => [m.x - dot, m.y - dot, 2 * dot, 2 * dot]);
        const spots = marks.map((m) => (narrow.matches || close || m.kind === 'flash' || m.stories.length > 1
            ? labelSpot(m.x, m.y, m.label, taken, upp) : null));
        for (const [i, m] of [...marks.entries()].reverse()) {     // biggest drawn last, on top
            const r = close ? MIN_DOT_PX * upp * (m.stories.length > 1 ? 1.3 : 1)
                : Math.max(Math.min(9, 2.5 + m.top.outlets * 0.6), MIN_DOT_PX * upp);
            const ids = m.stories.map((s) => s.id);
            const picked = filter && (filter.ids ? ids.some((id) => filter.ids.has(id)) : filter.isos?.has(m.iso));
            const selected = picked || ids.includes(openId) ? ' selected' : '';
            const g = el('g', {
                class: `pin ${m.kind}${selected}`, 'data-iso': m.iso, tabindex: 0, role: 'button',
                'aria-label': `${m.place}: ${m.stories.length} ${m.stories.length > 1 ? 'stories' : 'story'}`,
            }, pins);
            el('circle', { cx: m.x, cy: m.y, r: Math.max(HIT_PX * upp, r), class: 'hit' }, g);
            el('circle', { cx: m.x, cy: m.y, r, class: 'ring' }, g);
            el('circle', { cx: m.x, cy: m.y, r: r * 0.55, class: 'core' }, g);
            el('title', {}, g).textContent = m.stories.map((s) => s.headline).join('\n');
            const spot = spots[i];
            if (spot) {
                const tag = el('text', { x: spot.x, y: spot.y, class: `tag ${m.kind}`, 'text-anchor': spot.anchor }, g);
                tag.style.fontSize = `${10 * upp}px`;
                tag.style.strokeWidth = `${3 * upp}px`;
                tag.textContent = m.label;
            }
            const pick = () => (m.name
                ? showStories(m.name, ids)
                : showPlace(m.place, [m.iso], { zoom: m.iso }));
            g.addEventListener('click', (e) => { if (!dragged) pick(); e.stopPropagation(); });
            g.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
            });
            g.addEventListener('mouseenter', () => highlight(m.iso, true));
            g.addEventListener('mouseleave', () => highlight(m.iso, false));
        }
        return taken;
    }

    /** Where a label goes: right of its point, else left, above or below, wherever
     * it doesn't cover a label already placed; none if all are taken. */
    function labelSpot(x, y, text, taken, upp, gapPx = 9) {
        const w = text.length * 6.2 * upp;
        const h = 10 * upp;
        const gap = gapPx * upp;
        const options = [
            { x: x + gap, y: y + h * 0.35, anchor: 'start', box: [x + gap, y - h / 2] },
            { x: x - gap, y: y + h * 0.35, anchor: 'end', box: [x - gap - w, y - h / 2] },
            { x, y: y - gap, anchor: 'middle', box: [x - w / 2, y - gap - h] },
            { x, y: y + gap + h * 0.8, anchor: 'middle', box: [x - w / 2, y + gap] },
        ];
        const clear = ([bx, by]) => !taken.some(([tx, ty, tw, th]) => bx < tx + tw && tx < bx + w && by < ty + th && ty < by + h);
        const spot = options.find((o) => clear(o.box));
        if (spot) taken.push([...spot.box, w, h]);
        return spot || null;
    }

    function highlight(iso, on) {
        document.querySelectorAll(`.pin[data-iso="${iso}"], .item[data-iso="${iso}"]`).forEach((n) => n.classList.toggle('active', on));
    }

    /** Zoom to a country's mainland (not its far-off islands and territories). */
    async function zoomTo(isos) {
        const d = await Promise.all(isos.map(loadDetail));
        const bs = d.map((x, i) => x?.focus || box(isos[i])).filter(Boolean);
        if (!bs.length) return;
        setView(fit([Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])),
            Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))]), true);
    }

    // Gestures: drag to pan, pinch or scroll to zoom, double-tap to zoom in.
    const pointers = new Map();
    let dragged = false;
    let gesture = null;

    function startGestures() {
        const svg = $('map');
        svg.addEventListener('wheel', (e) => {
            e.preventDefault();
            following = null;
            const [mx, my] = toMap(e.clientX, e.clientY);
            zoomAt(mx, my, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
        }, { passive: false });
        svg.addEventListener('dblclick', (e) => {
            following = null;
            const [mx, my] = toMap(e.clientX, e.clientY);
            zoomAt(mx, my, 2);
        });
        svg.addEventListener('pointerdown', (e) => {
            pointers.set(e.pointerId, [e.clientX, e.clientY]);
            dragged = false;
            gesture = { start: [e.clientX, e.clientY], view: current(), pts: new Map(pointers) };
        });
        window.addEventListener('pointermove', (e) => {
            if (!pointers.has(e.pointerId) || !gesture) return;
            pointers.set(e.pointerId, [e.clientX, e.clientY]);
            const { r } = screen();
            const v0 = gesture.view;
            if (pointers.size === 1) {
                const dx = e.clientX - gesture.start[0];
                const dy = e.clientY - gesture.start[1];
                if (!dragged && Math.hypot(dx, dy) < 5) return;
                dragged = true;
                following = null;
                setView({ ...v0, x: v0.x - (dx / r.width) * v0.w, y: v0.y - (dy / r.height) * v0.h });
            } else if (pointers.size === 2) {
                const [a, b] = [...pointers.values()];
                const [a0, b0] = [...gesture.pts.values()].length === 2 ? [...gesture.pts.values()] : [a, b];
                if (gesture.pts.size !== 2) gesture.pts = new Map(pointers);
                dragged = true;
                following = null;
                const d0 = Math.hypot(a0[0] - b0[0], a0[1] - b0[1]) || 1;
                const factor = Math.hypot(a[0] - b[0], a[1] - b[1]) / d0;
                const mid0 = [(a0[0] + b0[0]) / 2 - r.left, (a0[1] + b0[1]) / 2 - r.top];
                const mid = [(a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top];
                const w = Math.min(Math.max(v0.w / factor, MIN_W), world.width);
                const h = w * (v0.h / v0.w);
                // The map point under the fingers' first midpoint stays under their midpoint now.
                const mx = v0.x + (mid0[0] / r.width) * v0.w;
                const my = v0.y + (mid0[1] / r.height) * v0.h;
                setView({ x: mx - (mid[0] / r.width) * w, y: my - (mid[1] / r.height) * h, w, h });
            }
        });
        const end = (e) => {
            pointers.delete(e.pointerId);
            gesture = pointers.size ? { start: [...pointers.values()][0], view: current(), pts: new Map(pointers) } : null;
        };
        window.addEventListener('pointerup', end);
        window.addEventListener('pointercancel', end);
        document.querySelector('.zoom').addEventListener('click', (e) => {
            const b = e.target.closest('button');
            if (!b) return;
            const v = current();
            following = null;                                    // the map is the user's now
            if (b.dataset.zoom === 'world') return setView(null, true);
            zoomAt(v.x + v.w / 2, v.y + v.h / 2, b.dataset.zoom === 'in' ? 2 : 0.5);
        });
    }

    // ---------- intel panel ----------

    function itemHtml(s) {
        const kind = s.tier === 'major' ? 'flash' : 'priority';
        const sources = s.sources.map((a) => {
            const link = webLink(a.url);
            const title = esc(a.title);
            return `<li><span class="outlet">${esc(a.outlet)}</span>${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${title}</a>` : title}</li>`;
        }).join('');
        return `
            <article class="item ${kind}${openId === s.id ? ' open' : ''}" data-id="${s.id}" data-iso="${esc(s.iso || '')}" tabindex="0">
                <div class="meta">
                    ${s.iso ? `<span class="place" title="${esc(s.place)}">${esc(s.iso)}</span>` : ''}
                    <span>${ago(s.newest)}</span>
                    <span>${s.outlets} src</span>
                    ${s.itn ? '<span class="wiki" title="In Wikipedia\'s In the news">wiki itn</span>' : ''}
                </div>
                <h3 class="headline">${esc(s.headline)}</h3>
                <ul class="sources">${sources}</ul>
            </article>`;
    }

    function section(kind, label, stories) {
        if (!stories.length) return '';
        return `<div class="section-head ${kind}"><span>${label}</span><span class="n">${stories.length}</span></div>
            ${stories.map(itemHtml).join('')}`;
    }

    function situationsHtml(situations) {
        if (!situations.length) return '';
        const rows = situations.map((o, i) => {
            const latest = o.latest ? `<p class="latest">${esc(o.latest)}</p>`
                : `<p class="latest quiet">${o.singles ? 'Only single-outlet reports today.' : 'No new reports today.'}</p>`;
            return `<div class="item situation" data-situation="${i}" tabindex="0" role="button">
                <div class="topic">${esc(o.topic)}</div>${latest}</div>`;
        }).join('');
        return `<div class="section-head"><span>Active situations</span><span class="n">${situations.length}</span></div>
            <div class="situations">${rows}</div>`;
    }

    function drawFeed() {
        const inPlace = (s) => !filter || (filter.ids ? filter.ids.has(s.id) : filter.isos.has(s.iso));
        const stories = data.stories.filter(inPlace);
        const flash = stories.filter((s) => s.tier === 'major');
        const priority = stories.filter((s) => s.tier !== 'major');
        let html = '';
        if (filter) {
            html += `<div class="filter"><span>Showing <b>${esc(filter.label)}</b></span>
                <button type="button" class="filter-clear">Show all</button></div>`;
        }
        html += section('flash', 'Flash', flash) + section('priority', 'Priority', priority);
        if (!stories.length) {
            html += filter
                ? '<div class="empty">Nothing at headline level here today.</div>'
                : '<div class="empty">Quiet. Nothing at headline level in the last 24 hours.</div>';
        }
        if (!filter && data.cleared > data.stories.length) {
            html += `<div class="capped">${data.cleared - data.stories.length} more cleared the bar today; showing the top ${data.stories.length}.</div>`;
        }
        if (!filter) {
            html += situationsHtml(data.ongoing.filter((o) => o.topic));
            if (data.earlier.length) html += section('', 'Earlier this week', data.earlier);
        }
        $('feed').innerHTML = html;
        render();
        $('countMajor').textContent = data.counts.major;
        $('countHeadline').textContent = data.counts.headline;
    }

    function drawStatus() {
        $('sync').textContent = failure === 'signed-out' ? 'signed out'
            : failure ? 'offline' : data?.fetched ? `${ago(data.fetched)} ago` : 'never';
        const src = data?.sources;
        if (src) {
            $('sources').textContent = `${src.ok}/${src.total}`;
            $('sourcesReadout').classList.toggle('warn', src.silent.length > 0);
            $('sourcesReadout').title = src.silent.length
                ? `Nothing for 6 hours or more from: ${src.silent.join(', ')}`
                : 'Every feed is answering';
        }
        // Say plainly when what's on screen isn't current.
        let notice = '';
        const from = data?.fetched ? ` News from ${clockTime(data.fetched)}.` : '';
        if (failure === 'signed-out') notice = `Signed out, so this isn't updating.${from}`;
        else if (failure) notice = `Can't reach the server, so this isn't updating.${from}`;
        else if (data && (!data.fetched || Date.now() - new Date(data.fetched) > STALE_MS)) {
            notice = data.fetched ? `No new news since ${clockTime(data.fetched)}: fetching seems stuck.` : 'No news fetched yet.';
        }
        $('notice').textContent = notice;
        $('notice').hidden = !notice;
    }

    /** Open or close a headline. Opening one takes the map to it and closing it takes
     * the map back; the list stays as it is. */
    function toggle(id, open) {
        openId = open ?? openId !== id ? id : null;
        document.querySelectorAll('.item[data-id]').forEach((n) => n.classList.toggle('open', Number(n.dataset.id) === openId));
        const story = openId != null && [...data.stories, ...data.earlier].find((s) => s.id === openId);
        if (story) {
            following ??= { view: goal };                        // keep the first view, across several headlines
            showOnMap(story);
        } else if (following) {
            setView(following.view, true);
            following = null;
        } else {
            render();
        }
    }

    /** The places a story names in its country, or else the country itself. */
    function showOnMap(story) {
        const here = (story.spots || []).filter((p) => p.iso === story.iso);
        if (here.length) {
            const xs = here.map((p) => p.x);
            const ys = here.map((p) => p.y);
            setView(fit([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], 12), true);
        } else if (story.iso) {
            zoomTo([story.iso]);
        }
    }

    function showPlace(label, isos, { zoom = null, track = null } = {}) {
        filter = { label, isos: new Set(isos), track };
        following = null;
        showFiltered();
        if (track?.length) {
            setView(fit([Math.min(...track.map((t) => t.x)), Math.min(...track.map((t) => t.y)),
                Math.max(...track.map((t) => t.x)), Math.max(...track.map((t) => t.y))], 20), true);
        } else if (zoom) {
            zoomTo([zoom]);
        }
    }

    function showStories(label, ids) {
        filter = { label, ids: new Set(ids) };
        showFiltered();
    }

    function showFiltered() {
        drawFeed();
        const intel = document.querySelector('.intel');
        if (narrow.matches) intel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });   // the list is under the map
        else intel.scrollTop = 0;
    }

    /** Back to everything: the whole list, and the whole world. */
    function clearFilter() {
        filter = null;
        following = null;
        drawFeed();
        setView(null, true);
    }

    $('feed').addEventListener('click', (e) => {
        if (e.target.closest('a')) return;
        if (e.target.closest('.filter-clear')) return clearFilter();
        if (e.target.closest('.retry')) return refresh();
        const situation = e.target.closest('.item[data-situation]');
        if (situation) {
            const o = data.ongoing.filter((x) => x.topic)[Number(situation.dataset.situation)];
            if (o) showPlace(o.topic, o.isos, { zoom: o.isos[0], track: o.track });
            return;
        }
        const item = e.target.closest('.item[data-id]');
        if (item) toggle(Number(item.dataset.id));
    });
    $('feed').addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const item = e.target.closest('.item[data-id], .item[data-situation]');
        if (!item) return;
        e.preventDefault();
        item.click();
    });
    $('feed').addEventListener('mouseover', (e) => {
        const item = e.target.closest('.item[data-iso]');
        document.querySelectorAll('.pin.active').forEach((n) => n.classList.remove('active'));
        if (item?.dataset.iso) document.querySelector(`.pin[data-iso="${item.dataset.iso}"]`)?.classList.add('active');
    });

    // ---------- clocks + refresh ----------

    function tick() {
        const now = new Date();
        $('clockUtc').textContent = now.toISOString().slice(11, 16) + 'Z';
        $('clockLocal').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'LOCAL';
        $('localZone').textContent = zone.split('/').pop().replace('_', ' ').toUpperCase().slice(0, 9);
        if (data || failure) drawStatus();
    }

    async function refresh() {
        try {
            const res = await fetch(url('/api/situation'), { credentials: 'same-origin' });
            if (res.status === 401) {
                failure = 'signed-out';   // signed_out.js offers to sign in again
            } else if (!res.ok) {
                throw new Error(res.status);
            } else {
                data = await res.json();
                failure = null;
                drawFeed();
            }
        } catch (_) {
            failure = 'offline';
        }
        if (!data && failure) {
            $('feed').innerHTML = `<div class="empty">${failure === 'signed-out' ? 'Signed out.' : 'Can’t reach the server.'}
                <button type="button" class="retry">Try again</button></div>`;
        }
        drawStatus();
    }

    async function loadMap() {
        try {
            world = await (await fetch(url('/static/map/world.json'))).json();
            drawMap();
            startGestures();
            render();
        } catch (_) {
            $('map').outerHTML = '<p class="map-missing">The map didn’t load. The stories are all in the list.</p>';
        }
    }

    function start() {
        tick();
        setInterval(tick, 15000);
        // The stories don't wait for the map: the list is what matters.
        loadMap();
        refresh();
        setInterval(refresh, 60000);
        let resizing = null;
        window.addEventListener('resize', () => {
            clearTimeout(resizing);
            resizing = setTimeout(render, 150);
        });
    }
    start();
})();
