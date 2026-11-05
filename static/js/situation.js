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
    const HIT_PX = 22;                 // pin tap radius on screen (44px across: a fingertip)
    const CLUSTER_PX = 26;             // at world scale, pins closer than this on screen are one
    // Map type, [desktop px, phone px, average letter width in ems]: areas in widely spaced
    // capitals, cities in the news in plain type, reference cities smaller and grey.
    const TYPE = { area: [9, 7.5, 0.84], city: [10.5, 9, 0.56], ref: [9, 7.5, 0.55] };
    const DOT_PX = 3;                  // close up, a city in the news: its dot's radius on screen

    let world = null;
    let data = null;
    // What you saw last time, on this device: {story id: its headline}. A story you
    // haven't seen is new, one whose headline has moved on since is updated. The
    // marks hold for the visit; leaving the page (or opening the story) clears them.
    const SEEN_KEY = 'situation.seen';
    const store = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem(k)); localStorage.setItem(k, JSON.stringify(v)); } catch (_) { return null; } };
    let before = store(SEEN_KEY);       // null: the first visit, when nothing is marked
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
    const STORY_W = 60;                // an opened story's places are shown at least this wide: in their country, with its neighbours
    const MIN_W = 6;                   // the closest zoom (a few hundred km across)
    let view = null;                   // the user's { x, y, w, h }; null = the automatic view
    let frame = null;                  // the view being drawn: labels stay inside it
    const details = new Map();         // iso → the country's region file once loaded
    const loading = new Set();
    const boxes = new Map();           // iso → [x0, y0, x1, y1] of its outline on the world map
    let drawing = false;

    function drawMap() {
        const svg = $('map');
        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
        svg.innerHTML = '';
        el('path', { d: world.outline, class: 'sphere' }, svg);
        const land = el('g', { id: 'land' }, svg);
        for (const c of world.countries) {
            const p = el('path', { d: c.d, class: 'land', 'data-iso': c.iso }, land);
            const t = el('title', {}, p);
            t.textContent = c.name;
        }
        for (const id of ['detail', 'refs', 'track', 'signals', 'pins']) el('g', { id }, svg);
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

    /** The map filling its panel: the world cropped to the panel's shape (no empty
     * bands), centred on today's pins, and wider only when they don't all fit. */
    function autoView() {
        const { aspect } = screen();
        const W = world.width;
        const H = world.height;
        const places = data ? countries() : [];
        const xs = places.map((c) => c.top.x);
        const ys = places.map((c) => c.top.y);
        const mid = (v, lo, hi) => (v.length ? (Math.min(...v) + Math.max(...v)) / 2 : (lo + hi) / 2);
        // As much of the world as fills the panel edge to edge.
        let w = Math.min(W, H / aspect);
        let h = w * aspect;
        if (places.length) {
            // ...or wider, to take in every pin with a margin.
            // (a margin of an eighth of the view on each side: room for a pin's label and its country)
            const need = Math.max((Math.max(...xs) - Math.min(...xs)) / 0.75, (Math.max(...ys) - Math.min(...ys)) / 0.75 / aspect);
            if (need > w) { w = Math.min(need, W); h = w * aspect; }
        }
        const cx = mid(xs, 0, W);
        const cy = places.length ? mid(ys, 0, H) : H * 0.42;   // land sits north of the middle
        return { x: Math.min(Math.max(cx - w / 2, 0), Math.max(0, W - w)), y: Math.min(Math.max(cy - h / 2, 0), Math.max(0, H - h)), w, h };
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
            frame = v;
            $('map').setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`);
            const upp = v.w / (screen().r.width || 1);            // map units per screen pixel
            const close = v.w < DETAIL_W;
            const theatre = document.querySelector('.theatre');
            theatre.classList.toggle('zoomed', !!view);
            document.querySelector('.theatre').classList.toggle('close', close);
            drawDetail(v, close);
            // The news first, then the week's places, then cities for orientation: each gives way to the last.
            const taken = drawPins(upp, close);
            drawSignals(upp, taken);
            drawLegend();
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

    /** A country's detailed outline and regions on the map, built once (null until its file loads). */
    function detailGroup(iso) {
        const layer = $('detail');
        const d = details.get(iso);
        if (!layer || !d) return null;
        let g = layer.querySelector(`g[data-iso="${iso}"]`);
        if (!g) {
            g = el('g', { 'data-iso': iso }, layer);
            el('path', { d: d.outline, class: 'land detail-land', 'data-iso': iso }, g);
            const regions = el('g', { class: 'regions' }, g);
            for (const r of d.regions) {
                const p = el('path', { d: r.d, class: 'region', 'data-name': r.name }, regions);
                el('title', {}, p).textContent = r.name;
            }
        }
        return g;
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
                detailGroup(c.iso);
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
        // Fewer, bigger cities the further out: enough to know where you are, and only
        // in the countries in the news (the rest of the map stays quiet).
        const minPop = v.w < 15 ? 100000 : v.w < 60 ? 500000 : v.w < 120 ? 1000000 : 3000000;
        const hot = new Set(data ? countries().map((c) => c.iso) : []);
        const named = new Set([...document.querySelectorAll('#pins .tag')].map((t) => t.textContent.toLowerCase()));
        const within = (x, y) => x > v.x && x < v.x + v.w && y > v.y && y < v.y + v.h;
        // Places the news or the week's track already name.
        const onMap = new Set([...document.querySelectorAll('#pins .tag, #track .track-label')].map((t) => t.textContent));
        for (const [iso, d] of details) {
            if (!inView(d.bbox, v) || (hot.size && !hot.has(iso))) continue;
            for (const c of d.cities) {
                if (c.pop < minPop || !within(c.x, c.y) || onMap.has(c.name)) continue;
                if (taken.some(([tx, ty, tw, th]) => c.x >= tx && c.x <= tx + tw && c.y >= ty && c.y <= ty + th)) continue;
                const spot = labelSpot(c.x, c.y, c.name, taken, upp, 3, false, 'ref');
                if (!spot) continue;                              // no room for its name: no dot either
                el('circle', { cx: c.x, cy: c.y, r: 1.6 * upp, class: 'city' }, layer);
                const t = el('text', { x: spot.x, y: spot.y, class: 'city-label', 'text-anchor': spot.anchor, 'data-iso': iso }, layer);
                t.style.fontSize = `${TYPE.ref[narrow.matches ? 1 : 0] * upp}px`;
                t.textContent = c.name;
            }
        }
        if (v.w >= LABEL_W) return;
        // Region names after the cities, wherever they fit.
        for (const [iso, d] of details) {
            if (!inView(d.bbox, v) || (hot.size && !hot.has(iso))) continue;
            for (const r of d.labels) {
                if (!within(r.x, r.y) || named.has(r.name.toLowerCase())) continue;   // a region in the news is named already
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
            label: `${countryName(iso)}${stories.length > 1 ? ` +${stories.length - 1}` : ''}`,
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
            ...p, place: p.name, label: p.name, top: p.stories[0], where: p.kind,   // city or region
            kind: p.stories.some((s) => s.tier === 'major') ? 'flash' : 'priority',
        }));
        const placed = new Set(spots.map((p) => p.iso));
        return [...spots, ...countries(unplaced).filter((c) => !placed.has(c.iso))];
    }

    // A region the news names, as its outline on the zoomed map ({ path, box }): the
    // outline that holds the region's point (names differ between the gazetteer and the map).
    const areas = new Map();
    function areaFor(m) {
        if ((m.where || m.kind) !== 'region') return null;
        const key = `${m.name}|${m.iso}`;
        if (areas.has(key)) return areas.get(key);
        const g = detailGroup(m.iso);
        if (!g) return null;                                 // not loaded yet: ask again next time
        const hidden = g.style.display === 'none';
        g.style.display = '';                                // laid out, to measure
        const point = $('map').createSVGPoint();             // not a DOMPoint: Brave takes only an SVGPoint here
        point.x = m.x;
        point.y = m.y;
        const path = [...g.querySelectorAll('.region')].find((p) => p.isPointInFill?.(point));
        const b = path?.getBBox();
        if (hidden) g.style.display = 'none';
        if (!path) return null;                              // maybe not laid out yet: ask again next time
        const found = { path, box: [b.x, b.y, b.x + b.width, b.y + b.height] };
        areas.set(key, found);
        return found;
    }

    /** Today's stories on the map; returns the room their dots and labels take.
     * Close up, a region in the news is tinted as an area and a city is a dot. */
    function drawPins(upp, close) {
        const pins = $('pins');
        if (!pins || !data) return [];
        pins.innerHTML = '';
        document.querySelectorAll('.land, .region').forEach((p) => p.classList.remove('hot-flash', 'hot-priority', 'selected'));
        for (const c of countries()) {
            document.querySelectorAll(`.land[data-iso="${c.iso}"]`).forEach((p) => p.classList.add(`hot-${c.kind}`));
        }
        const marks = close ? places() : cluster(countries(), upp);
        // No pins: a country in the news is tinted and named on itself. Close up, a city
        // (which has no outline to tint) is a small dot with its name beside it.
        // Areas (countries, regions) are named on themselves in spaced capitals; cities beside their dot.
        const dotted = (m) => close && !!m.where && !areaFor(m);
        const style = (m) => (dotted(m) ? 'city' : 'area');
        const dot = DOT_PX * upp;
        const taken = [...overlays(upp), ...marks.filter(dotted).map((m) => [m.x - dot, m.y - dot, 2 * dot, 2 * dot])];
        const spots = marks.map((m) => labelSpot(m.x, m.y, m.label, taken, upp, dotted(m) ? DOT_PX + 4 : 0, !dotted(m), style(m)));
        for (const [i, m] of [...marks.entries()].reverse()) {     // biggest drawn last, on top
            const ids = m.stories.map((s) => s.id);
            const picked = filter && (filter.ids ? ids.some((id) => filter.ids.has(id)) : filter.isos?.has(m.iso));
            const selected = picked || ids.includes(openId) ? ' selected' : '';
            const area = close ? areaFor(m) : null;
            if (area) {
                area.path.classList.add(`hot-${m.kind}`, ...(selected ? ['selected'] : []));
                area.path.parentNode.appendChild(area.path);   // drawn last: its neighbours' borders don't cover half its edge
            }
            // picked or opened: the country itself is outlined (there's no pin to light up)
            if (selected && !close) (m.isos || [m.iso]).forEach((iso) => document.querySelectorAll(`.land[data-iso="${iso}"]`).forEach((p) => p.classList.add('selected')));
            const g = el('g', {
                class: `pin ${m.kind}${selected}`, 'data-iso': m.iso, tabindex: 0, role: 'button',
                'aria-label': `${m.place}: ${m.stories.length} ${m.stories.length > 1 ? 'stories' : 'story'}`,
            }, pins);
            el('circle', { cx: m.x, cy: m.y, r: HIT_PX * upp, class: 'hit' }, g);
            if (dotted(m)) el('circle', { cx: m.x, cy: m.y, r: dot, class: 'core' }, g);
            el('title', {}, g).textContent = m.stories.map((s) => s.headline).join('\n');
            const spot = spots[i];
            if (spot) {
                const tag = el('text', { x: spot.x, y: spot.y, class: `tag ${m.kind} ${style(m)}`, 'text-anchor': spot.anchor }, g);
                tag.style.fontSize = `${TYPE[style(m)][narrow.matches ? 1 : 0] * upp}px`;
                tag.textContent = m.label;
            }
            const pick = () => {
                if (m.name) return showStories(m.name, ids);
                if (m.isos) { showPlace(m.place, m.isos); return zoomTo(m.isos); }   // a group: zoom to them all
                return showPlace(m.place, [m.iso], { zoom: m.iso });
            };
            g.addEventListener('click', (e) => { if (!dragged) pick(); e.stopPropagation(); });
            g.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
            });
            g.addEventListener('mouseenter', () => highlight(m.iso, true));
            g.addEventListener('mouseleave', () => highlight(m.iso, false));
        }
        return taken;
    }

    /** At world scale, pins that would touch are one: the biggest story's country
     * leads it ("UA +4"), the list shows them all, and a tap zooms to them. */
    function cluster(marks, upp) {
        const out = [];
        for (const m of marks) {                             // biggest first
            const near = out.find((c) => Math.hypot(c.x - m.x, c.y - m.y) < CLUSTER_PX * upp);
            if (!near) { out.push({ ...m, stories: [...m.stories], members: [m] }); continue; }
            near.members.push(m);
            near.stories.push(...m.stories);
            if (m.kind === 'flash') near.kind = 'flash';
        }
        for (const c of out.filter((g) => g.members.length > 1)) {
            c.isos = c.members.map((m) => m.iso);
            c.label = `${countryName(c.iso)} +${c.members.length - 1}`;
            c.place = c.members.map((m) => m.place).join(', ');
        }
        return out;
    }

    /** Where a label goes: right of its point, else left, above or below, wherever
     * it doesn't cover a label already placed; none if all are taken. */
    function labelSpot(x, y, text, taken, upp, gapPx = 9, centred = false, kind = 'area') {
        const [size, width] = [TYPE[kind][narrow.matches ? 1 : 0], TYPE[kind][2]];
        const w = text.length * size * width * upp;
        const h = size * 1.15 * upp;
        const gap = gapPx * upp;
        const options = [
            ...(centred ? [{ x, y: y + h * 0.35, anchor: 'middle', box: [x - w / 2, y - h / 2] }] : []),   // on the place itself
            { x: x + gap, y: y + h * 0.35, anchor: 'start', box: [x + gap, y - h / 2] },
            { x: x - gap, y: y + h * 0.35, anchor: 'end', box: [x - gap - w, y - h / 2] },
            { x, y: y - gap, anchor: 'middle', box: [x - w / 2, y - gap - h] },
            { x, y: y + gap + h * 0.8, anchor: 'middle', box: [x - w / 2, y + gap] },
        ];
        const clear = ([bx, by]) => !taken.some(([tx, ty, tw, th]) => bx < tx + tw && tx < bx + w && by < ty + th && ty < by + h);
        // inside the map, clear of its edges, if any side allows it (Greenland's label goes below, not off the top)
        const pad = 4 * upp;
        const inside = ([bx, by]) => !frame || (bx >= frame.x + pad && by >= frame.y + pad
            && bx + w <= frame.x + frame.w - pad && by + h <= frame.y + frame.h - pad);
        const spot = options.find((o) => clear(o.box) && inside(o.box)) || options.find((o) => clear(o.box));
        if (spot) taken.push([...spot.box, w, h]);
        return spot || null;
    }

    /** The map's buttons and legend, in map units: labels keep out from under them. */
    function overlays(upp) {
        const r = $('map').getBoundingClientRect();
        if (!frame || !r.width) return [];
        return [...document.querySelectorAll('.theatre .zoom .btn, .theatre .legend')]
            .map((n) => n.getBoundingClientRect())
            .filter((b) => b.width && b.height)
            .map((b) => [frame.x + (b.left - r.left - 4) * upp, frame.y + (b.top - r.top - 4) * upp, (b.width + 8) * upp, (b.height + 8) * upp]);
    }

    function highlight(iso, on) {
        document.querySelectorAll(`.pin[data-iso="${iso}"], .item[data-iso="${iso}"]`).forEach((n) => n.classList.toggle('active', on));
    }

    /** Zoom to a country's mainland (not its far-off islands and territories). */
    async function zoomTo(isos, still = () => true) {
        const go = (bs) => setView(fit([Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])),
            Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))]), true);
        const outlines = isos.map(box).filter(Boolean);
        if (outlines.length) go(outlines);                    // there at once, on the world map's outline...
        const d = await Promise.all(isos.map(loadDetail));
        const bs = d.map((x, i) => x?.focus || box(isos[i])).filter(Boolean);
        if (bs.length && still()) go(bs);                     // ...then on the mainland, once its detail is in
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
        // Tapping a country (not a pin) shows its card and its stories, and goes to
        // it unless the map is already close.
        svg.addEventListener('click', (e) => {
            if (dragged || e.target.closest('.pin')) return;
            const land = e.target.closest('[data-iso]');
            const c = land && world.countries.find((x) => x.iso === land.dataset.iso);
            if (!c) return;
            showPlace(c.name, [c.iso], { zoom: current().w < DETAIL_W ? null : c.iso });
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

    /** "2h ago", with the full local date and time on hover. */
    function stamp(iso, prefix = '') {
        if (!iso) return '';
        const when = new Date(iso);
        const full = when.toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
        const rel = ago(iso) === '0m' ? 'just now' : `${ago(iso)} ago`;
        return `<time datetime="${esc(iso)}" title="${esc(prefix ? `${prefix} ${full}` : full)}">${prefix ? `${prefix.toLowerCase()} ` : ''}${rel}</time>`;
    }

    /** "14:02 local": when the latest report came, on the clocks where it happens;
     * left out where the clocks read the same as yours. */
    function localTime(iso, zone) {
        if (!iso || !zone) return '';
        const opts = { hour: '2-digit', minute: '2-digit', hour12: false };
        try {
            const there = new Date(iso).toLocaleTimeString([], { ...opts, timeZone: zone });
            if (there === new Date(iso).toLocaleTimeString([], opts)) return '';
            return `<span class="local" title="The latest report, in local time there (${esc(zone.replace(/_/g, ' '))})">${there} local</span>`;
        } catch (_) {
            return '';
        }
    }

    /** A country's everyday English name ("United States", not "United States of America"). */
    const regionNames = (() => { try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch (_) { return null; } })();
    function countryName(iso, fallback = iso) {
        try { return regionNames?.of(iso) || fallback; } catch (_) { return fallback; }
    }

    /** The box round a country's biggest piece of land (the US without Alaska and the
     * Aleutians, which cross the date line), from its map path. */
    function mainland(d) {
        let best = null;
        for (const part of d.split('M').slice(1)) {
            const n = part.match(/-?[\d.]+/g)?.map(Number) || [];
            const xs = n.filter((_, i) => i % 2 === 0);
            const ys = n.filter((_, i) => i % 2 === 1);
            const b = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
            if (!best || (b[2] - b[0]) * (b[3] - b[1]) > (best[2] - best[0]) * (best[3] - best[1])) best = b;
        }
        return best;
    }

    /** The lead story's locator: its country, tinted, among its neighbours. */
    function locatorHtml(s, kind) {
        const shape = world?.countries.find((c) => c.iso === s.iso);
        const b = shape && mainland(shape.d);
        if (!b) return '';
        const pad = Math.max(b[2] - b[0], b[3] - b[1], 24) * 0.6;
        const v = [b[0] - pad, b[1] - pad, b[2] - b[0] + 2 * pad, b[3] - b[1] + 2 * pad];
        const side = Math.max(v[2], v[3]);
        const [x, y] = [v[0] - (side - v[2]) / 2, v[1] - (side - v[3]) / 2];
        const near = world.countries.filter((c) => {
            const o = box(c.iso);
            return o && o[0] < x + side && o[2] > x && o[1] < y + side && o[3] > y;
        });
        const paths = near.map((c) => `<path d="${c.d}" class="${c.iso === s.iso ? `here ${kind}` : ''}"/>`).join('');
        return `<svg class="locator" viewBox="${x} ${y} ${side} ${side}" aria-hidden="true">${paths}</svg>`;
    }

    function itemHtml(s) {
        const kind = s.tier === 'major' ? 'flash' : 'priority';
        const sources = s.sources.map((a) => {
            const link = webLink(a.url);
            const title = esc(a.title);
            const times = [stamp(a.published), stamp(a.updated, 'Updated')].filter(Boolean).join(' · ');
            return `<li><span class="outlet">${esc(a.outlet)}${times ? ` · ${times}` : ''}</span>${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${title}</a>` : title}</li>`;
        }).join('');
        const fresh = before && !(s.id in before) ? 'New' : before && before[s.id] !== s.headline ? 'Updated' : '';
        // The top story leads the list: a bigger headline, and where it is on a small map of its own.
        const top = data.stories.find((x) => x.tier === 'major') || data.stories[0];
        const lead = !filter && s.id === top?.id;
        // Kept for when it's opened: the time there, and Wikipedia's notice.
        const signal = (data.signals || []).find((g) => g.story_id === s.id);
        const facts = [localTime(s.newest, s.zone), s.itn ? '<span>In Wikipedia\'s In the news</span>' : '',
            signal?.lead ? `<span>${esc(signal.source)} saw it ${signal.lead < 90 ? `${signal.lead} min` : `${Math.round(signal.lead / 60)}h`} before the first report</span>` : ''].filter(Boolean);
        return `
            <article class="item ${kind}${lead ? ' lead' : ''}${openId === s.id ? ' open' : ''}" data-id="${s.id}" data-iso="${esc(s.iso || '')}" tabindex="0">
                ${lead ? locatorHtml(s, kind) : ''}
                <div class="meta">
                    ${s.running ? '<span class="live" title="A running story: many events that are part of one thing">Live</span>' : ''}
                    ${fresh ? `<span class="fresh" title="Since your last visit">${fresh}</span>` : ''}
                    ${s.iso ? `<span class="where" title="${esc(s.place)}">${esc(countryName(s.iso, s.place))}</span>` : ''}
                    <span class="sep">${ago(s.newest)}</span>
                    <span class="sep">${s.outlets} outlets</span>
                </div>
                <h3 class="headline">${esc(s.headline)}</h3>
                ${facts.length ? `<p class="facts">${facts.join('<span aria-hidden="true"> · </span>')}</p>` : ''}
                ${s.latest ? `<p class="latest-line"><span class="latest-tag">Latest · ${esc(ago(s.latest.at))} · ${esc(s.latest.outlet)}</span> ${esc(s.latest.title)}</p>` : ''}
                ${s.running ? developmentsHtml(s) : ''}
                <ul class="sources">${sources}</ul>
            </article>`;
    }

    /** A running story's biggest developments, the most carried first: three
     * at a glance, all five once it's open. */
    function developmentsHtml(s) {
        const rows = (s.developments || []).map((d, i) => `<li${i >= 3 ? ' class="more"' : ''}><span class="n">${d.outlets} src</span>${esc(d.headline)}</li>`).join('');
        const rest = s.events - (s.developments || []).length;
        return rows ? `<ol class="developments">${rows}</ol>${rest > 0 ? `<p class="developments-rest">and ${rest} smaller ${rest === 1 ? 'report' : 'reports'}</p>` : ''}` : '';
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

    // ---------- signals: disaster alerts and strong earthquakes (signals.py) ----------

    const KIND = { EQ: 'Earthquake', TC: 'Cyclone', VO: 'Eruption', FL: 'Flood' };
    const shortDay = (iso) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

    /** When: an earthquake's or eruption's moment; a cyclone's or flood's span. */
    function signalWhen(g) {
        if (g.kind === 'EQ' || g.kind === 'VO') return `${ago(g.started)} ago`;
        return new Date(g.ended) > Date.now() ? `since ${shortDay(g.started)}` : `${shortDay(g.started)}–${shortDay(g.ended)}`;
    }

    function signalsHtml(list) {
        if (!list.length) return '';
        const rows = list.map((g) => {
            const story = g.story_id != null && data.stories.find((s) => s.id === g.story_id);
            const news = story ? `<p class="signal-news">In the news: <span>${esc(story.headline)}</span></p>` : '';
            const lead = g.lead ? `<span class="sep">seen ${g.lead < 90 ? `${g.lead} min` : `${Math.round(g.lead / 60)}h`} before the news</span>` : '';
            return `<article class="item signal ${g.level}" data-signal="${esc(g.id)}" tabindex="0" role="button">
                <div class="meta">
                    <span class="kind">${KIND[g.kind]}</span>
                    ${g.iso ? `<span class="where">${esc(countryName(g.iso, g.place || g.iso))}</span>` : ''}
                    <span class="sep">${signalWhen(g)}</span>
                    <span class="sep">${esc(g.source)} ${g.level}</span>
                    ${lead}
                </div>
                <h3 class="headline">${esc(g.title)}</h3>
                ${g.place && g.place !== countryName(g.iso || '', '') ? `<p class="facts-line">${esc(g.place)}</p>` : ''}
                ${news}
                ${g.url ? `<p class="signal-link"><a href="${esc(g.url)}" target="_blank" rel="noopener">${esc(g.source)} report</a></p>` : ''}
            </article>`;
        }).join('');
        return `<div class="section-head signal"><span>Signals</span><span class="n">${list.length}</span></div>${rows}`;
    }

    /** Signals on the map: a thin double ring where it is, its short name beside it, quieter than the news. */
    /** The legend names only what's on the map: no signal swatch on a day without signals. */
    function drawLegend() {
        const shown = new Set(data ? countries().map((c) => c.kind) : []);
        if (data?.signals?.length) shown.add('signal');
        const legend = document.querySelector('.theatre .legend');
        legend.querySelectorAll('[data-kind]').forEach((n) => { n.hidden = !shown.has(n.dataset.kind); });
        legend.hidden = !shown.size;
    }

    function drawSignals(upp, taken = []) {
        const layer = $('signals');
        if (!layer) return;
        layer.innerHTML = '';
        for (const g of data?.signals || []) {
            const node = el('g', { class: `signal-mark ${g.level}`, 'data-signal': g.id, role: 'button', tabindex: 0,
                'aria-label': `${g.title}${g.place ? `, ${g.place}` : ''}` }, layer);
            el('circle', { cx: g.x, cy: g.y, r: HIT_PX * upp, class: 'hit' }, node);
            el('circle', { cx: g.x, cy: g.y, r: 3 * upp, class: 'ring' }, node);
            el('circle', { cx: g.x, cy: g.y, r: 7 * upp, class: 'ring outer' }, node);
            el('title', {}, node).textContent = `${g.title}${g.place ? ` · ${g.place}` : ''}`;
            const name = g.kind === 'EQ' && g.magnitude ? `M${g.magnitude.toFixed(1)}` : g.title.replace(/^Cyclone /, '').replace(/ eruption$/, '');
            const spot = labelSpot(g.x, g.y, name, taken, upp, 11);
            if (spot) {
                const t = el('text', { x: spot.x, y: spot.y, class: 'tag area signal-tag', 'text-anchor': spot.anchor }, node);
                t.style.fontSize = `${TYPE.area[narrow.matches ? 1 : 0] * upp}px`;
                t.textContent = name;
            }
            node.addEventListener('click', (e) => { if (!dragged) showSignal(g.id); e.stopPropagation(); });
        }
    }

    /** A signal picked, on the map or in the list: the map goes to it, and its story opens if the news has one. */
    function showSignal(id) {
        const g = data.signals.find((x) => x.id === id);
        if (!g) return;
        setView(fit([g.x, g.y, g.x, g.y], STORY_W), true);
        if (g.story_id != null && data.stories.some((s) => s.id === g.story_id)) toggle(g.story_id, g.story_id);
        else document.querySelector(`.item[data-signal="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // ---------- a country's card ----------

    const cards = new Map();           // iso → the card, or a promise of it

    function loadCard(iso) {
        if (cards.has(iso)) return;
        cards.set(iso, fetch(url(`/api/country/${iso}`), { credentials: 'same-origin' })
            .then((r) => (r.ok ? r.json() : null)).catch(() => null)
            .then((card) => {
                cards.set(iso, card);
                if (filter?.country === iso) drawFeed();
            }));
    }

    function cardHtml(iso) {
        const card = cards.get(iso);
        if (!card || card instanceof Promise) return '';
        const now = card.zone ? new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: card.zone }) : '';
        const facts = [
            card.capital && ['Capital', esc(card.capital)],
            card.leader && ['Leader', esc(card.leader)],
            now && ['Local time', `${now} <span class="muted">${esc(card.zone.split('/').pop().replace(/_/g, ' '))}</span>`],
            ['In the news', card.last
                ? `${card.stories_30d} ${card.stories_30d === 1 ? 'story' : 'stories'} in 30 days · last ${ago(card.last.at)} ago`
                : 'Nothing at headline level in 30 days'],
        ].filter(Boolean);
        return `<div class="country-card"><div class="country-name">${esc(card.name)}</div>
            <dl>${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl></div>`;
    }

    let lastShown = null;   // what the list showed last: a change fades it in

    function drawFeed() {
        const inPlace = (s) => !filter || (filter.ids ? filter.ids.has(s.id) : filter.isos.has(s.iso));
        const stories = data.stories.filter(inPlace);
        const flash = stories.filter((s) => s.tier === 'major');
        const priority = stories.filter((s) => s.tier !== 'major');
        let html = '';
        if (filter) {
            html += `<div class="filter"><span>Showing <b>${esc(filter.label)}</b></span>
                <button type="button" class="btn filter-clear">Show all</button></div>`;
            if (filter.country) html += cardHtml(filter.country);
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
        html += signalsHtml((data.signals || []).filter((g) => !filter || (!filter.ids && filter.isos.has(g.iso))));
        if (!filter) html += situationsHtml(data.ongoing.filter((o) => o.topic));
        const shown = filter ? filter.label : '';
        if (shown !== lastShown) {
            $('feed').classList.remove('swap');
            void $('feed').offsetWidth;                          // restart the fade
            $('feed').classList.add('swap');
            lastShown = shown;
        }
        $('feed').innerHTML = html;
        render();
    }

    let syncing = false;

    function drawStatus() {
        const fresh = data?.fetched ? ago(data.fetched) : null;
        $('sync').textContent = syncing ? 'Updating…' : failure === 'signed-out' ? 'Signed out'
            : failure ? 'Offline' : fresh ? (fresh === '0m' ? 'Updated just now' : `Updated ${fresh} ago`) : 'Not updated yet';
        const silent = data?.sources?.silent || [];
        const stale = data && (!data.fetched || Date.now() - new Date(data.fetched) > STALE_MS);
        $('syncButton').classList.toggle('warn', !!failure || stale || silent.length > 0);
        $('syncButton').title = [
            silent.length ? `Quiet for 6 hours or more: ${silent.join(', ')}` : data?.sources ? `All ${data.sources.total} feeds answering` : '',
            'Tap to fetch the news now',
        ].filter(Boolean).join('. ');
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
        const story = openId != null && data.stories.find((s) => s.id === openId);
        if (story && before) {                                   // read now: not new any more
            before[story.id] = story.headline;
            document.querySelector(`.item[data-id="${story.id}"] .fresh`)?.remove();
        }
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

    /** The places a story names in its country, or else the country itself. It goes there at
     * once, and takes in the regions' whole outlines when the country's detail has loaded. */
    async function showOnMap(story) {
        const here = (story.spots || []).filter((p) => p.iso === story.iso);
        const still = () => openId === story.id;              // not closed or changed while loading
        if (here.length) {
            const go = () => {
                // A region's whole outline, a city's point.
                const boxes = here.map((p) => {
                    const area = areaFor(p);
                    return area ? area.box : [p.x, p.y, p.x, p.y];
                });
                setView(fit([Math.min(...boxes.map((b) => b[0])), Math.min(...boxes.map((b) => b[1])),
                    Math.max(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[3]))], STORY_W), true);
            };
            go();
            if (here.some((p) => p.kind === 'region' && !areaFor(p))) {
                await loadDetail(story.iso);
                if (still()) go();
            }
        } else if (story.iso) {
            // too small for the world map's outlines (Hong Kong, the Vatican): its point first
            if (!box(story.iso) && story.x != null) setView(fit([story.x, story.y, story.x, story.y], STORY_W), true);
            zoomTo([story.iso], still);
        }
    }

    function showPlace(label, isos, { zoom = null, track = null } = {}) {
        const country = isos.length === 1 && !track ? isos[0] : null;
        if (country) loadCard(country);
        filter = { label, isos: new Set(isos), track, country };
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
        if (narrow.matches) {
            // The map stays on top: bring the list's start up to just under it, if it's scrolled past.
            const map = document.querySelector('.theatre');
            const start = intel.getBoundingClientRect().top + window.scrollY - map.offsetHeight - (parseFloat(getComputedStyle(map).top) || 0);
            if (window.scrollY > start) window.scrollTo({ top: start, behavior: 'smooth' });
        }
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
        const signal = e.target.closest('.item[data-signal]');
        if (signal) return showSignal(signal.dataset.signal);
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
        const item = e.target.closest('.item[data-id], .item[data-situation], .item[data-signal]');
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
        if (data || failure) drawStatus();
    }

    /** Save what's on screen as seen, for the next visit (only today's stories are kept). */
    function remember() {
        if (data) store(SEEN_KEY, Object.fromEntries(data.stories.map((s) => [s.id, s.headline])));
    }

    // Leaving the page ends the visit: what was on screen is no longer news when you come back.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden' && data) before = store(SEEN_KEY);
        else if (data) drawFeed();
    });

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
                remember();
                drawFeed();
            }
        } catch (_) {
            failure = 'offline';
        }
        if (!data && failure) {
            $('feed').innerHTML = `<div class="empty">${failure === 'signed-out' ? 'Signed out.' : 'Can’t reach the server.'}
                <button type="button" class="btn retry">Try again</button></div>`;
        }
        drawStatus();
    }

    /** Fetch the news now (the server joins a fetch already running, and skips
     * one if the last was a minute or two ago), then show what it found. */
    async function syncNow() {
        if (syncing) return;
        syncing = true;
        $('syncButton').setAttribute('aria-busy', 'true');
        drawStatus();
        try {
            const res = await fetch(url('/api/sync'), { method: 'POST', credentials: 'same-origin' });
            if (res.status === 401) failure = 'signed-out';
        } catch (_) {
            failure = 'offline';
        }
        syncing = false;
        $('syncButton').removeAttribute('aria-busy');
        await refresh();
    }

    async function loadMap() {
        try {
            world = await (await fetch(url('/static/map/world.json'))).json();
            drawMap();
            startGestures();
            render();
            if (data) drawFeed();   // the lead's locator needs the map
        } catch (_) {
            $('map').outerHTML = '<p class="map-missing">The map didn’t load. The stories are all in the list.</p>';
        }
    }

    // Back (the phone's gesture, or the browser's) closes an open headline,
    // then the place or situation the list is showing, before leaving the page.
    // History keeps one entry per open layer, put back in line after every tap.
    const layers = [   // topmost first: [is it open?, close it]
        [() => openId != null, () => toggle(openId)],
        [() => filter != null, () => clearFilter()],
    ];
    let historyDepth = 0, ignoredPops = 0;
    function syncHistory() {
        const open = layers.filter(([isOpen]) => isOpen()).length;
        while (historyDepth < open) history.pushState({ layer: ++historyDepth }, '');
        if (historyDepth > open) { ignoredPops++; history.go(open - historyDepth); historyDepth = open; }
    }
    window.addEventListener('popstate', () => {
        if (ignoredPops) { ignoredPops--; return; }
        historyDepth = Math.max(0, historyDepth - 1);
        layers.find(([isOpen]) => isOpen())?.[1]();
        setTimeout(syncHistory, 0);
    });
    ['click', 'keydown', 'touchend'].forEach((ev) => document.addEventListener(ev, () => setTimeout(syncHistory, 0), true));

    function start() {
        $('syncButton').addEventListener('click', syncNow);
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
