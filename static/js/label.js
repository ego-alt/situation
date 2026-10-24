/* Situation: your judgements of what matters (labels.py): which of two stories
 * matters more, and whether a story belongs on the page at all. */
(() => {
    const ROOT = window.APP_ROOT || '';
    const url = (p) => `${ROOT}${p}`;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const CHOICES = {
        rank: [['a', 'A matters more', '1'], ['b', 'B matters more', '2'], ['equal', 'About equal', '3']],
        show: [['yes', 'Yes, show it', '1'], ['no', 'No', '2'], ['borderline', 'Borderline', '3']],
    };
    const ASK = { rank: 'Which matters more, for a world briefing today?', show: 'Would you want this on your page today?' };
    let current = null;
    let busy = false;

    function showCounts(c) {
        if (!c) return;
        $('countToday').textContent = c.today;
        $('countTotal').textContent = c.total;
    }

    function story(side, s) {
        return `<div class="label-card">${side ? `<div class="label-side">${side}</div>` : ''}
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
        const cards = q.kind === 'rank' ? `<div class="label-pair">${story('A', q.a)}${story('B', q.b)}</div>`
            : `<div class="label-pair label-single">${story('', q.story)}</div>`;
        $('question').innerHTML = `<h2 class="label-ask">${ASK[q.kind]}</h2>${cards}
            <div class="label-choices">${CHOICES[q.kind].map(([value, text, key]) =>
                `<button type="button" data-answer="${value}"><span class="k">${key}</span> ${text}</button>`).join('')}
                <button type="button" data-skip><span class="k">S</span> Skip</button></div>`;
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
                body: JSON.stringify({ ...current, answer }),
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

    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-skip]')) return next();
        const answer = e.target.closest('[data-answer]');
        if (answer) return send(answer.dataset.answer);
        if (e.target.id === 'retry') next();
    });
    document.addEventListener('keydown', (e) => {
        if (!current || e.metaKey || e.ctrlKey || e.altKey) return;
        const key = e.key.toLowerCase();
        if (key === 's') return next();
        const hit = CHOICES[current.kind].find(([, , k]) => k === key);
        if (hit) send(hit[0]);
    });
    next();
})();
