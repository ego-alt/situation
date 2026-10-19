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
    let filter = null;                 // { label, isos: Set } when the list shows one place
    let failure = null;                // 'offline' | 'signed-out' while refreshes fail

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

    function drawMap() {
        const svg = $('map');
        svg.setAttribute('viewBox', `0 0 ${world.width} ${world.height}`);
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
        el('g', { id: 'pins' }, svg);
    }

    /** Today's stories grouped by country, biggest first: one pin each. */
    function countries() {
        const byIso = new Map();
        for (const s of data.stories) {
            if (s.x == null || !s.iso) continue;
            if (!byIso.has(s.iso)) byIso.set(s.iso, []);
            byIso.get(s.iso).push(s);
        }
        return [...byIso.entries()].map(([iso, stories]) => ({
            iso, stories, top: stories[0], place: stories[0].place || iso,
            kind: stories.some((s) => s.tier === 'major') ? 'flash' : 'priority',
        }));
    }

    /** On a phone, zoom to the part of the world with pins in it. */
    function frame(svg, places) {
        const W = world.width;
        const H = world.height;
        const box = svg.getBoundingClientRect();
        if (!narrow.matches || !places.length || !box.width) return [0, 0, W, H];
        const xs = places.map((c) => c.top.x);
        const ys = places.map((c) => c.top.y);
        const aspect = box.height / box.width;
        let w = Math.max(Math.max(...xs) - Math.min(...xs) + 120, 560);
        let h = w * aspect;
        if (w >= W || h >= H) return [0, 0, W, H];
        const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
        const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
        const x0 = Math.min(Math.max(cx - w / 2, 0), W - w);
        const y0 = Math.min(Math.max(cy - h / 2, 0), H - h);
        return [x0, y0, w, h];
    }

    function drawPins() {
        const pins = $('pins');
        const svg = $('map');
        if (!pins || !data) return;
        pins.innerHTML = '';
        document.querySelectorAll('.land').forEach((p) => p.classList.remove('hot-flash', 'hot-priority'));
        const places = countries();
        const view = frame(svg, places);
        svg.setAttribute('viewBox', view.join(' '));
        // Map units per screen pixel, so pins and labels keep a usable size at any zoom.
        const box = svg.getBoundingClientRect();
        const upp = box.width ? 1 / Math.min(box.width / view[2], box.height / view[3]) : 1;
        // Boxes labels must not cover: every dot, then each label placed (biggest stories first).
        const dot = MIN_DOT_PX * upp;
        const labels = places.map((c) => [c.top.x - dot, c.top.y - dot, 2 * dot, 2 * dot]);
        const spots = new Map(places.map((c) => [c.iso, labelSpot(c, labels, upp)]));
        for (const c of [...places].reverse()) {            // biggest drawn last, on top
            const { iso, top, kind, stories } = c;
            document.querySelectorAll(`.land[data-iso="${iso}"]`).forEach((p) => p.classList.add(`hot-${kind}`));
            const r = Math.max(Math.min(9, 2.5 + top.outlets * 0.6), MIN_DOT_PX * upp);
            const label = `${iso} ${stories.length > 1 ? `· ${stories.length}` : ''}`.trim();
            const selected = filter?.isos.has(iso) ? ' selected' : '';
            const g = el('g', {
                class: `pin ${kind}${selected}`, 'data-iso': iso, tabindex: 0, role: 'button',
                'aria-label': `${c.place}: ${stories.length} ${stories.length > 1 ? 'stories' : 'story'}`,
            }, pins);
            el('circle', { cx: top.x, cy: top.y, r: Math.max(HIT_PX * upp, r), class: 'hit' }, g);
            el('circle', { cx: top.x, cy: top.y, r, class: 'ring' }, g);
            el('circle', { cx: top.x, cy: top.y, r: r * 0.55, class: 'core' }, g);
            const title = el('title', {}, g);
            title.textContent = stories.map((s) => s.headline).join('\n');
            const spot = spots.get(iso);
            if (spot) {
                const tag = el('text', { x: spot.x, y: spot.y, class: `tag ${kind}`, 'text-anchor': spot.anchor }, g);
                tag.style.fontSize = `${10 * upp}px`;
                tag.style.strokeWidth = `${3 * upp}px`;
                tag.textContent = label;
            }
            const pick = () => showPlace(c.place, [iso]);
            g.addEventListener('click', pick);
            g.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
            });
            g.addEventListener('mouseenter', () => highlight(iso, true));
            g.addEventListener('mouseleave', () => highlight(iso, false));
        }
    }

    /** Where a pin's label goes: right of it, else left, above or below,
     * wherever it doesn't cover a label already placed; none if all are taken. */
    function labelSpot(c, taken, upp) {
        const { iso, top, kind, stories } = c;
        if (!(narrow.matches || kind === 'flash' || stories.length > 1)) return null;
        const text = `${iso} ${stories.length > 1 ? `· ${stories.length}` : ''}`.trim();
        const w = text.length * 6.6 * upp;
        const h = 10 * upp;
        const gap = 9 * upp;
        const options = [
            { x: top.x + gap, y: top.y + h * 0.35, anchor: 'start', box: [top.x + gap, top.y - h / 2] },
            { x: top.x - gap, y: top.y + h * 0.35, anchor: 'end', box: [top.x - gap - w, top.y - h / 2] },
            { x: top.x, y: top.y - gap, anchor: 'middle', box: [top.x - w / 2, top.y - gap - h] },
            { x: top.x, y: top.y + gap + h * 0.8, anchor: 'middle', box: [top.x - w / 2, top.y + gap] },
        ];
        const clear = ([x, y]) => !taken.some(([tx, ty, tw, th]) => x < tx + tw && tx < x + w && y < ty + th && ty < y + h);
        const spot = options.find((o) => clear(o.box));
        if (spot) taken.push([...spot.box, w, h]);
        return spot || null;
    }

    function highlight(iso, on) {
        document.querySelectorAll(`.pin[data-iso="${iso}"], .item[data-iso="${iso}"]`).forEach((n) => n.classList.toggle('active', on));
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
        const inPlace = (s) => !filter || filter.isos.has(s.iso);
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
        document.querySelectorAll('.pin').forEach((p) => p.classList.toggle('selected', !!filter && filter.isos.has(p.dataset.iso)));
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

    function toggle(id, open) {
        openId = open ?? openId !== id ? id : null;
        document.querySelectorAll('.item[data-id]').forEach((n) => n.classList.toggle('open', Number(n.dataset.id) === openId));
    }

    function showPlace(label, isos) {
        filter = { label, isos: new Set(isos) };
        drawFeed();
        const intel = document.querySelector('.intel');
        if (narrow.matches) intel.scrollIntoView({ block: 'start', behavior: 'smooth' });   // the list is under the map
        else intel.scrollTop = 0;
    }

    function clearFilter() {
        filter = null;
        drawFeed();
    }

    $('feed').addEventListener('click', (e) => {
        if (e.target.closest('a')) return;
        if (e.target.closest('.filter-clear')) return clearFilter();
        if (e.target.closest('.retry')) return refresh();
        const situation = e.target.closest('.item[data-situation]');
        if (situation) {
            const o = data.ongoing.filter((x) => x.topic)[Number(situation.dataset.situation)];
            if (o) showPlace(o.topic, o.isos);
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
                drawPins();
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
            drawPins();
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
            resizing = setTimeout(drawPins, 150);
        });
    }
    start();
})();
