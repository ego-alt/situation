/* Situation: check stories as grouped and placed, and rank pairs, so the
 * models can be measured and trained (labels.py, learn.py). */
(() => {
    const ROOT = window.APP_ROOT || '';
    const url = (p) => `${ROOT}${p}`;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const NOWHERE = 'none';
    const MEMBER_KEYS = '1234567890';
    const NEARBY_KEYS = 'qwert';

    const RANK = [['a', 'A matters more', '1'], ['b', 'B matters more', '2'], ['equal', 'About equal', '3']];
    let current = null;
    let busy = false;
    // A story question's answers so far: articles taken out, near misses put in, the place.
    let out = new Set();
    let joined = new Set();
    let where = null;

    function when(iso) {
        if (!iso) return '';
        return new Date(iso).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
    }

    function showCounts(c) {
        if (!c) return;
        $('countToday').textContent = c.today;
        $('countTotal').textContent = c.total;
    }

    // ---------- a story: one screen, many answers ----------

    function row(a, group, key) {
        const on = group === 'members' ? out.has(a.url) : joined.has(a.url);
        return `<li><button type="button" class="label-row ${group}" data-url="${esc(a.url)}" data-group="${group}" aria-pressed="${on}">
            <span class="k">${key || ''}</span>
            <span class="label-row-text"><span class="label-meta">${esc(a.outlet)} · ${esc(when(a.published))}</span>
            <span class="label-title">${esc(a.title)}</span></span></button></li>`;
    }

    function placeChips(q) {
        const options = q.place.options;
        const listed = new Set(options.map(([iso]) => iso));
        const chip = (value, text) => `<button type="button" class="label-chip" data-where="${esc(value)}" aria-pressed="${where === value}">${esc(text)}</button>`;
        const elsewhere = where && where !== NOWHERE && !listed.has(where);
        const all = (window.COUNTRIES || []).map(([iso, name]) => `<option value="${esc(iso)}"${where === iso ? ' selected' : ''}>${esc(name)}</option>`).join('');
        return `${options.map(([iso, name]) => chip(iso, iso === q.place.guess ? `${name} (its pin)` : name)).join('')}
            ${chip(NOWHERE, 'Nowhere in particular')}
            <label class="label-chip label-select${elsewhere ? ' on' : ''}"><span>${elsewhere ? esc((window.COUNTRIES || []).find(([iso]) => iso === where)?.[1] || where) : 'Somewhere else'}</span>
                <select id="elsewhere" aria-label="Somewhere else"><option value="">…</option>${all}</select></label>
            ${chip('', 'Not sure')}`;
    }

    function renderStory(q) {
        const members = q.members.map((a, i) => row(a, 'members', MEMBER_KEYS[i])).join('');
        const nearby = q.nearby.map((a, i) => row(a, 'nearby', NEARBY_KEYS[i])).join('');
        $('question').innerHTML = `
            <h2 class="label-ask">${esc(q.headline)}</h2>
            <p class="label-hint">Would you want these under one headline? Tap any that don’t belong.</p>
            <ul class="label-rows">${members}</ul>
            ${nearby ? `<p class="label-hint">Left out. Tap any that belong under it too.</p><ul class="label-rows">${nearby}</ul>` : ''}
            <p class="label-hint">Where is it happening?</p>
            <div class="label-places" id="places">${placeChips(q)}</div>
            <div class="label-choices">
                <button type="button" class="primary" data-done><span class="k">⏎</span> Done</button>
                <button type="button" data-skip><span class="k">S</span> Skip</button>
            </div>`;
    }

    function toggleRow(button) {
        const set = button.dataset.group === 'members' ? out : joined;
        const u = button.dataset.url;
        set.has(u) ? set.delete(u) : set.add(u);
        button.setAttribute('aria-pressed', set.has(u));
    }

    function setWhere(value) {
        where = value;
        $('places').innerHTML = placeChips(current);
    }

    // ---------- two stories: which matters more ----------

    function story(side, s) {
        return `<div class="label-card"><div class="label-side">${side}</div>
            <div class="label-title">${esc(s.headline)}</div>
            <div class="label-meta">${s.outlets.length} outlets: ${esc(s.outlets.join(', '))}</div>
            <ul class="label-titles">${s.titles.map((t) => `<li>${esc(t)}</li>`).join('')}</ul></div>`;
    }

    function renderRank(q) {
        $('question').innerHTML = `<h2 class="label-ask">Which matters more, for a world briefing today?</h2>
            <div class="label-pair">${story('A', q.a)}${story('B', q.b)}</div>
            <div class="label-choices">${RANK.map(([value, text, key]) =>
                `<button type="button" data-answer="${value}"><span class="k">${key}</span> ${text}</button>`).join('')}
                <button type="button" data-skip><span class="k">S</span> Skip</button></div>`;
    }

    function render(q) {
        current = q;
        out = new Set();
        joined = new Set();
        where = q?.kind === 'story' ? (q.place.guess || NOWHERE) : null;
        if (!q) {
            $('question').innerHTML = '<div class="empty">Nothing left to ask about these articles. New ones come with the next snapshot, every 6 hours.</div>';
            return;
        }
        (q.kind === 'story' ? renderStory : renderRank)(q);
        window.scrollTo({ top: 0 });
    }

    async function next() {
        try {
            const r = await fetch(url('/api/label'), { headers: { Accept: 'application/json' } });
            if (!r.ok) throw new Error(r.status);
            const body = await r.json();
            showCounts(body.counts);
            render(body.question);
        } catch (e) {
            $('question').innerHTML = '<div class="empty">Couldn’t load a question. <button type="button" class="retry" id="retry">Try again</button></div>';
        }
    }

    async function send(answer) {
        if (!current || busy) return;
        busy = true;
        const buttons = document.querySelectorAll('.label-choices button');
        buttons.forEach((b) => { b.disabled = true; });
        try {
            const r = await fetch(url('/api/label'), {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...current, ...answer }),
            });
            if (!r.ok) throw new Error(r.status);
            showCounts((await r.json()).counts);
            busy = false;
            await next();
        } catch (e) {
            buttons.forEach((b) => { b.disabled = false; });
        } finally {
            busy = false;
        }
    }

    const done = () => send({ answer: 'checked', out: [...out], in: [...joined], where: where === '' ? null : where });

    document.addEventListener('click', (e) => {
        const t = e.target;
        if (t.closest('[data-skip]')) return next();
        if (t.closest('[data-done]')) return done();
        const answer = t.closest('[data-answer]');
        if (answer) return send({ answer: answer.dataset.answer });
        const r = t.closest('.label-row');
        if (r) return toggleRow(r);
        const chip = t.closest('[data-where]');
        if (chip) return setWhere(chip.dataset.where);
        if (t.id === 'retry') next();
    });
    document.addEventListener('change', (e) => {
        if (e.target.id === 'elsewhere' && e.target.value) setWhere(e.target.value);
    });
    document.addEventListener('keydown', (e) => {
        if (!current || e.metaKey || e.ctrlKey || e.altKey || e.target.tagName === 'SELECT') return;
        const key = e.key.toLowerCase();
        if (key === 's') return next();
        if (current.kind === 'rank') {
            const hit = RANK.find(([, , k]) => k === key);
            if (hit) send({ answer: hit[0] });
            return;
        }
        if (e.key === 'Enter' && !e.target.closest('button')) return done();
        const group = MEMBER_KEYS.includes(key) ? 'members' : NEARBY_KEYS.includes(key) ? 'nearby' : null;
        if (!group) return;
        const i = (group === 'members' ? MEMBER_KEYS : NEARBY_KEYS).indexOf(key);
        const button = document.querySelectorAll(`.label-row.${group}`)[i];
        if (button) toggleRow(button);
    });
    next();
})();
