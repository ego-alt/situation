/* Situation: label pairs, so the grouping and ranking can be measured (labels.py). */
(() => {
    const ROOT = window.APP_ROOT || '';
    const url = (p) => `${ROOT}${p}`;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const CHOICES = {
        same: [['same', 'Same story', '1'], ['different', 'Different stories', '2'], ['unsure', 'Not sure', '3']],
        rank: [['a', 'A matters more', '1'], ['b', 'B matters more', '2'], ['equal', 'About equal', '3']],
    };
    let current = null;
    let busy = false;

    function when(iso) {
        if (!iso) return '';
        return new Date(iso).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
    }

    function showCounts(c) {
        if (!c) return;
        $('countToday').textContent = c.today;
        $('countTotal').textContent = c.total;
    }

    function article(side, a) {
        return `<div class="label-card"><div class="label-side">${side}</div>
            <div class="label-meta">${esc(a.outlet)} · ${esc(when(a.published))}</div>
            <div class="label-title">${esc(a.title)}</div>
            ${a.summary ? `<p class="label-summary">${esc(a.summary)}…</p>` : ''}</div>`;
    }

    function story(side, s) {
        return `<div class="label-card"><div class="label-side">${side}</div>
            <div class="label-title">${esc(s.headline)}</div>
            <div class="label-meta">${s.outlets.length} outlets: ${esc(s.outlets.join(', '))}</div>
            <ul class="label-titles">${s.titles.map((t) => `<li>${esc(t)}</li>`).join('')}</ul></div>`;
    }

    function render(q) {
        current = q;
        if (!q) {
            $('question').innerHTML = '<div class="empty">Nothing left to ask about these articles. New ones come with the next snapshot, every 6 hours.</div>';
            return;
        }
        const ask = q.kind === 'same' ? 'Are these about the same story?' : 'Which matters more, for a world briefing today?';
        const cards = q.kind === 'same' ? article('A', q.a) + article('B', q.b) : story('A', q.a) + story('B', q.b);
        $('question').innerHTML = `<h2 class="label-ask">${ask}</h2><div class="label-pair">${cards}</div>
            <div class="label-choices">${CHOICES[q.kind].map(([value, text, key]) =>
                `<button type="button" data-answer="${value}"><span class="k">${key}</span> ${text}</button>`).join('')}</div>`;
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

    async function answer(value) {
        if (!current || busy) return;
        busy = true;
        document.querySelectorAll('.label-choices button').forEach((b) => { b.disabled = true; });
        try {
            const r = await fetch(url('/api/label'), {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...current, answer: value }),
            });
            if (!r.ok) throw new Error(r.status);
            showCounts((await r.json()).counts);
            await next();
        } catch (e) {
            document.querySelectorAll('.label-choices button').forEach((b) => { b.disabled = false; });
        } finally {
            busy = false;
        }
    }

    document.addEventListener('click', (e) => {
        const b = e.target.closest('[data-answer]');
        if (b) answer(b.dataset.answer);
        if (e.target.id === 'retry') next();
    });
    document.addEventListener('keydown', (e) => {
        if (!current || e.metaKey || e.ctrlKey || e.altKey) return;
        const hit = CHOICES[current.kind].find(([, , key]) => key === e.key);
        if (hit) answer(hit[0]);
    });
    next();
})();
