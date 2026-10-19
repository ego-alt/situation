/* Situation: draw the world, pin today's stories on it, list them beside it. */
(() => {
    const ROOT = window.APP_ROOT || '';
    const url = (p) => `${ROOT}${p}`;
    const NS = 'http://www.w3.org/2000/svg';
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    let world = null;
    let data = null;
    let openId = null;

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

    function drawPins() {
        const pins = $('pins');
        if (!pins || !data) return;
        pins.innerHTML = '';
        document.querySelectorAll('.land').forEach((p) => p.classList.remove('hot-flash', 'hot-priority'));
        const stories = [...data.stories].filter((s) => s.x != null).reverse();   // biggest drawn last, on top
        for (const s of stories) {
            const kind = s.tier === 'major' ? 'flash' : 'priority';
            document.querySelectorAll(`.land[data-iso="${s.iso}"]`).forEach((p) => {
                if (!p.classList.contains('hot-flash')) p.classList.add(`hot-${kind}`);
            });
            const r = Math.min(9, 2.5 + s.outlets * 0.6);
            const g = el('g', { class: `pin ${kind}`, 'data-id': s.id, tabindex: 0 }, pins);
            el('circle', { cx: s.x, cy: s.y, r: r, class: 'ring' }, g);
            el('circle', { cx: s.x, cy: s.y, r: r * 0.55, class: 'core' }, g);
            const title = el('title', {}, g);
            title.textContent = s.headline;
            if (kind === 'flash' && s.iso) {
                const tag = el('text', { x: s.x + r + 3, y: s.y + 3, class: 'tag' }, g);
                tag.textContent = s.iso;
            }
            g.addEventListener('mouseenter', () => highlight(s.id, true));
            g.addEventListener('mouseleave', () => highlight(s.id, false));
            g.addEventListener('click', () => {
                const item = document.querySelector(`.item[data-id="${s.id}"]`);
                if (item) { toggle(s.id, true); item.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
            });
        }
    }

    function highlight(id, on) {
        document.querySelectorAll(`[data-id="${id}"]`).forEach((n) => n.classList.toggle('active', on));
    }

    // ---------- intel panel ----------

    function itemHtml(s) {
        const kind = s.tier === 'major' ? 'flash' : 'priority';
        const sources = s.sources.map((a) => `
            <li><span class="outlet">${esc(a.outlet)}</span><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></li>`).join('');
        return `
            <article class="item ${kind}${openId === s.id ? ' open' : ''}" data-id="${s.id}" tabindex="0">
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

    function drawFeed() {
        const flash = data.stories.filter((s) => s.tier === 'major');
        const priority = data.stories.filter((s) => s.tier !== 'major');
        const situations = data.ongoing.filter((o) => o.topic);
        let html = section('flash', 'Flash', flash) + section('priority', 'Priority', priority);
        if (!data.stories.length) {
            html = '<div class="empty">Quiet. Nothing at headline level in the last 24 hours.</div>';
        }
        if (situations.length) {
            html += `<div class="section-head"><span>Active situations</span><span class="n">${situations.length}</span></div>
                <div class="situations">${situations.map((o) => `
                    <div class="item"><div class="topic">${esc(o.topic)}</div>
                    ${o.latest ? `<p class="latest">${esc(o.latest)}</p>` : ''}</div>`).join('')}</div>`;
        }
        if (data.earlier.length) {
            html += section('', 'Earlier this week', data.earlier);
        }
        $('feed').innerHTML = html;
        $('countMajor').textContent = flash.length;
        $('countHeadline').textContent = priority.length;
        $('sync').textContent = data.fetched ? `${ago(data.fetched)} ago` : 'never';
    }

    function toggle(id, open) {
        openId = open ?? openId !== id ? id : null;
        document.querySelectorAll('.item[data-id]').forEach((n) => n.classList.toggle('open', Number(n.dataset.id) === openId));
    }

    $('feed').addEventListener('click', (e) => {
        if (e.target.closest('a')) return;
        const item = e.target.closest('.item[data-id]');
        if (item) toggle(Number(item.dataset.id));
    });
    $('feed').addEventListener('keydown', (e) => {
        const item = e.target.closest('.item[data-id]');
        if (item && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle(Number(item.dataset.id)); }
    });
    $('feed').addEventListener('mouseover', (e) => {
        const item = e.target.closest('.item[data-id]');
        document.querySelectorAll('.pin.active').forEach((n) => n.classList.remove('active'));
        if (item) document.querySelector(`.pin[data-id="${item.dataset.id}"]`)?.classList.add('active');
    });

    // ---------- clocks + refresh ----------

    function tick() {
        const now = new Date();
        $('clockUtc').textContent = now.toISOString().slice(11, 16) + 'Z';
        $('clockLocal').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'LOCAL';
        $('localZone').textContent = zone.split('/').pop().replace('_', ' ').toUpperCase().slice(0, 9);
    }

    async function refresh() {
        try {
            const res = await fetch(url('/api/situation'));
            if (!res.ok) throw new Error(res.status);
            data = await res.json();
            drawFeed();
            drawPins();
        } catch (_) {
            $('sync').textContent = 'offline';
        }
    }

    async function start() {
        tick();
        setInterval(tick, 15000);
        world = await (await fetch(url('/static/map/world.json'))).json();
        drawMap();
        await refresh();
        setInterval(refresh, 60000);
    }
    start();
})();
