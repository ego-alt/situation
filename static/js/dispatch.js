/* Dispatch: one week's issue, laid out as a front page. The neuroscience comes first: the
 * cover story leads, the papers with a commentary come next and larger, the rest fill the
 * columns; then the week's poem, the week's question and the holidays coming up. Everything is read here: a
 * story opens in place, and only an opened one links out to the publisher. */
(() => {
    const ROOT = window.APP_ROOT || '';
    const url = (p) => `${ROOT}${p}`;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const webLink = (u) => (/^https?:\/\//i.test(String(u || '')) ? u : null);
    // Titles and abstracts come from the server already escaped, with only <i>, <sub> and <sup> kept.
    const marked = (s) => String(s ?? '');
    const plain = (html) => { const el = document.createElement('div'); el.innerHTML = html; return el.textContent; };

    // Each row below the lead takes the next of these shapes, so the page changes rhythm as it goes.
    const SHAPES = [[6, 3, 3], [4, 4, 4], [3, 6, 3], [7, 5], [4, 8], [3, 3, 6], [5, 7]];
    const MODULES = 9;       // past this many, the rest of a long week is a list
    const ROW_MAX = 600;     // the most a row of modules runs to before its text is cut
    const MIN_LINES = 4;     // a story shows at least this much of its abstract
    const narrow = window.matchMedia('(max-width: 860px)');

    let data = null;
    let failure = null;

    const day = (iso, opts = { day: 'numeric', month: 'short' }) => new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-GB', { ...opts, timeZone: 'UTC' });

    function ago(iso) {
        if (!iso) return 'never';
        const mins = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
        if (mins < 60) return mins ? `${mins}m ago` : 'just now';
        const h = Math.round(mins / 60);
        return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
    }

    /** "26 Oct – 1 Nov 2026", the year in its own span */
    function span(start, end) {
        const sameMonth = start.slice(0, 7) === end.slice(0, 7);
        const from = day(start, sameMonth ? { day: 'numeric' } : { day: 'numeric', month: 'short' });
        return `${esc(from)} – ${esc(day(end))}<span class="folio-year"> ${end.slice(0, 4)}</span>`;
    }

    /** First and last author: who did it and whose lab it came from. */
    function byline(names) {
        if (!names.length) return '';
        const list = names.length > 1 ? `${names[0]} … ${names[names.length - 1]}` : names[0];
        const text = names.length === 2 ? `${names[0]} & ${names[1]}` : list;
        const all = names.length > 2 ? ` title="${esc(`${names.length} authors: ${names.slice(0, 40).join(', ')}${names.length > 40 ? ` and ${names.length - 40} more` : ''}`)}"` : '';
        return `<p class="byline"${all}>${esc(text)}</p>`;
    }

    function figure(p, large) {
        const src = webLink(large ? p.figure_large || p.figure : p.figure);
        if (!src) return '';
        const big = !large && webLink(p.figure_large);
        const r = large && ratios.get(p.doi);
        return `<figure class="fig"${r ? ` style="--r: ${r.toFixed(3)}"` : ''}><img src="${esc(src)}"${big ? ` data-large="${esc(big)}"` : ''} alt="Figure from “${esc(plain(p.title))}”"
            loading="${large ? 'eager' : 'lazy'}" decoding="async" referrerpolicy="no-referrer" onerror="this.closest('figure').remove()"></figure>`;
    }

    /** The issue's cover, at the head of the rail beside the story it shows. */
    function cover(p) {
        const src = webLink(p.cover_image);
        return src ? `<figure class="rail-cover"><img src="${esc(src)}" alt="This week’s cover of ${esc(p.journal)}" decoding="async" referrerpolicy="no-referrer"
            onerror="this.closest('figure').remove()"><figcaption><span class="rail-cover-k">On the cover</span>
            <span class="rail-cover-note">This week’s ${esc(p.journal)} shows the lead story.</span></figcaption></figure>` : '';
    }

    const summary = (c) => (c.summary ? `<p class="standfirst-sum">${marked(c.summary)}</p>` : '');

    /** The commentary's own headline and summary, under the paper's: what the field made of it. */
    function standfirst(p) {
        return p.commentary.map((c) => `<div class="standfirst"><p class="standfirst-head"><span class="standfirst-k">${esc(c.label)}</span> ${marked(c.title)}</p>${summary(c)}</div>`).join('');
    }

    /** A link out to the publisher, or nothing when there's nowhere safe to go. */
    function away(href, label, cls = '') {
        const link = webLink(href);
        return link ? `<a${cls ? ` class="${cls}"` : ''} href="${esc(link)}" target="_blank" rel="noopener">${esc(label)} <span aria-hidden="true">↗</span></a>` : '';
    }

    /** The way out, shown only in an opened story: everything else is read here. */
    function links(p) {
        const all = [away(p.url, `Paper · ${p.journal}`), p.free && away(p.free.url, `Free · ${p.free.label}`, 'free'),
            ...p.commentary.map((c) => away(c.url, c.label))].filter(Boolean);
        return all.length ? `<p class="links">${all.join('')}</p>` : '';
    }

    function kicker(p) {
        // An earlier paper, in this issue for the commentary on it that came out this week.
        const earlier = p.earlier ? `<span>From ${esc(day(p.published, { day: 'numeric', month: 'short' }))}</span>` : '';
        return `<p class="kicker"><span class="journal">${esc(p.journal)}</span> ${p.cover ? '<span class="mark-cover">Cover story</span>' : ''}${earlier}</p>`;
    }

    /** One story. `size` is lead, rail, line, or mod: a module `w` of 12 columns wide. A module
     * leads with its figure, puts its headline over it (mid widths), floats it in the corner for
     * the text to wrap (wide), or goes without on a desktop (the rail, a narrow slot with a wide
     * figure). On a phone every story but the lead shows its figure as a thumbnail. The title is
     * the button that opens the story in place. */
    function story(p, size, w = 0) {
        const lead = size === 'lead';
        const mod = size === 'mod';
        const art = figure(p, lead);
        const bare = size === 'rail' || (mod && w === 3 && ratio(p) > 1.6);
        const headFirst = mod && (w === 5 || w === 6);
        const head = `<div class="story-head">
                ${kicker(p)}
                <h2 class="story-title"><button type="button" class="story-open" aria-expanded="false">${marked(p.title)}</button></h2>
                ${byline(p.authors)}
                ${standfirst(p)}
            </div>`;
        const kind = mod ? `s-mod w${w}${w >= 7 ? ' side' : ''}` : `s-${size}`;
        const marks = `${art && !bare ? ' has-fig' : ''}${art && bare ? ' bare' : ''}${headFirst ? ' head-first' : ''}${p.commentary.length ? ' has-note' : ''}`;
        return `<article class="story ${kind}${marks}" data-doi="${esc(p.doi)}">
            <div class="flow">${lead || headFirst || size === 'line' ? head + art : art + head}${p.abstract ? `<p class="abstract">${marked(p.abstract)}</p>` : ''}</div>
            ${links(p)}
        </article>`;
    }

    // Each figure's width over height, so the widest goes in the widest slot.
    const ratios = new Map();
    const ratio = (p) => ratios.get(p.doi) ?? (p.figure ? 1.3 : 0);

    /** Each figure's proportions, for the desktop's slots. A phone only needs the lead's, to keep
     * its place while it loads, so it fetches just that (the large one it shows). */
    function measure(papers) {
        const list = narrow.matches ? papers.slice(0, 1) : papers;
        const todo = list.filter((p) => webLink(p.figure) && !ratios.has(p.doi)).map((p) => new Promise((done) => {
            const img = new Image();
            img.referrerPolicy = 'no-referrer';
            img.onload = () => { ratios.set(p.doi, img.naturalWidth / img.naturalHeight); done(); };
            img.onerror = done;
            img.src = narrow.matches ? webLink(p.figure_large) || p.figure : p.figure;
        }));
        return Promise.race([Promise.all(todo), new Promise((done) => setTimeout(done, 1500))]);
    }

    /** The lead with a rail beside it, then rows of modules in changing shapes, then in a
     * long week the remainder as a list. The papers come in order: the cover story, those
     * with a commentary, the rest. */
    function compose(papers) {
        const [lead, ...queue] = papers;
        const art = cover(lead);
        // The rail runs as long as the lead: two stories, one less with the cover at its head or,
        // on a desktop, beside a lead without a figure (its text is short).
        const rail = queue.splice(0, 2 - !!art - (!narrow.matches && !ratio(lead)));
        const rows = [rail.length || art ? `<div class="row row-front">${story(lead, 'lead')}<div class="rail">${art}${rail.map((p) => story(p, 'rail')).join('')}</div></div>`
            : `<div class="row">${story(lead, 'lead')}</div>`];
        // A list of one isn't worth it: one over the limit goes in the modules.
        const modules = queue.splice(0, queue.length > MODULES + 1 ? MODULES : queue.length);
        for (let s = 0; modules.length; s++) {
            let shape = SHAPES[s % SHAPES.length];
            if (modules.length < shape.length) shape = modules.length === 1 ? [12] : [7, 5];
            const items = modules.splice(0, shape.length).sort((a, b) => ratio(b) - ratio(a));
            const slots = shape.map((w, i) => ({ w, i })).sort((a, b) => b.w - a.w);
            const placed = [];
            slots.forEach((slot, j) => { placed[slot.i] = story(items[j], 'mod', slot.w); });
            rows.push(`<div class="row row-mod">${placed.join('')}</div>`);
        }
        if (queue.length) {
            rows.push(`<section class="rest" aria-label="The rest of the week">
                <h2 class="section-head"><span>The rest of the week</span></h2>
                <div class="rest-list">${queue.map((p) => story(p, 'line')).join('')}</div></section>`);
        }
        return rows.join('');
    }


    // How tall a story's own content is (the grid stretches the story itself to its row).
    const natural = (el) => [...el.children].filter((c) => c.offsetHeight).pop().getBoundingClientRect().bottom - el.getBoundingClientRect().top;

    /** The line height of a story's abstract, and how far down its abstract starts. */
    function lines(el) {
        const abstract = el.querySelector('.flow .abstract');
        return abstract && { top: abstract.offsetTop, line: parseFloat(getComputedStyle(abstract).lineHeight) };
    }

    /** How tall a story needs to be to show MIN_LINES of its abstract (or all of it, if shorter). */
    function need(el) {
        const l = lines(el);
        if (!l) return natural(el);
        const flow = el.querySelector('.flow');
        return Math.min(natural(el), natural(el) - flow.offsetHeight + l.top + MIN_LINES * l.line);
    }

    /** Cut a story's text at a whole line so the story ends at `height`; the last line fades. */
    function clamp(el, height) {
        const l = lines(el);
        if (!l || natural(el) <= height + 1) return;
        const flow = el.querySelector('.flow');
        const room = height - (natural(el) - flow.offsetHeight);
        const n = Math.max(2, Math.floor((room - l.top) / l.line));
        flow.style.maxHeight = `${l.top + n * l.line}px`;
        flow.classList.add('cut');
    }

    /** Even out each row on a desktop: every story's text runs to the foot of its row, so no
     * column ends with a gap. The row is as tall as its widest story (or the lead), but never so
     * short that a story beside it shows less than a few lines. */
    function fit() {
        const rows = [...document.querySelectorAll('#edition .row')];
        for (const flow of document.querySelectorAll('#edition .flow')) { flow.style.maxHeight = ''; flow.classList.remove('cut'); }
        if (narrow.matches) return;
        for (const row of rows) {
            const lead = row.querySelector('.s-lead');
            if (lead) {
                const rail = [...row.querySelectorAll('.rail .story')];
                if (!rail.length) continue;
                const gap = rail.length > 1 ? rail[1].getBoundingClientRect().top - rail[0].getBoundingClientRect().bottom : 0;
                const above = rail[0].getBoundingClientRect().top - lead.getBoundingClientRect().top;   // the cover, if any
                const each = Math.max((natural(lead) - above - gap * (rail.length - 1)) / rail.length, ...rail.map(need));
                rail.forEach((el) => clamp(el, each));
                continue;
            }
            const stories = [...row.children].filter((el) => !el.classList.contains('open'));
            const widest = stories.reduce((a, b) => (b.offsetWidth > a.offsetWidth ? b : a), stories[0]);
            if (!widest) continue;
            const height = Math.min(Math.max(natural(widest), ...stories.map(need)), ROW_MAX);
            stories.forEach((el) => clamp(el, height));
        }
    }

    /** An issue as it came out. The newest also says when the next one comes out. */
    function drawEdition() {
        const e = data.edition;
        const next = new Date(data.next_out).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
        if (!e) {
            $('folio').textContent = 'The first issue is being set';
            $('edition').innerHTML = `<p class="press-empty">The first issue comes out ${esc(next)}.</p>`;
        } else {
            $('folio').innerHTML = `<span>No. ${e.number}</span><span>${span(e.start, e.end)}</span>${e.next ? '' : `<span>Next ${esc(next)}</span>`}`;
            $('edition').innerHTML = data.stories.length ? compose(data.stories) : '<p class="press-empty">Nothing this week.</p>';
            fit();
            // The lead's figure sets the front row's height once it's in.
            $('edition').querySelector('.s-lead img')?.addEventListener('load', fit);
        }
        step('prev', e?.prev);
        step('next', e?.next);
        $('turn').hidden = !e?.prev && !e?.next;
        document.title = e ? `Situation · Dispatch No. ${e.number}` : 'Situation · Dispatch';
    }

    /** The steps to the edition before or after: in the folio, and again at the foot. */
    function step(dir, id) {
        for (const button of document.querySelectorAll(`.folio-step[data-dir="${dir}"]`)) {
            button.disabled = !id;
            button.dataset.edition = id || '';
            button.querySelector('.folio-step-label').textContent = id ? `No. ${Number(id.slice(6))}` : '';
            button.setAttribute('aria-label', id ? `${dir === 'prev' ? 'Previous' : 'Next'} edition, No. ${Number(id.slice(6))}` : 'No more editions');
        }
    }

    function drawStatus() {
        const fresh = data?.fetched ? ago(data.fetched) : null;
        $('updated').textContent = failure === 'signed-out' ? 'Signed out' : failure ? 'Offline'
            : fresh ? `Updated ${fresh}` : 'Not updated yet';
        const failing = data?.sources?.failing || [];
        $('paperStatus').classList.toggle('warn', !!failure || failing.length > 0);
        $('paperStatus').title = failing.length ? `Couldn’t read: ${failing.join(', ')}`
            : data?.sources ? `All ${data.sources.total} journals answered` : '';
        // Said once: with nothing to show, the page itself says it (and the sign-in bar, when there is one).
        let notice = '';
        if (data && failure === 'signed-out') notice = document.querySelector('.signed-out-bar') ? '' : 'Signed out, so this isn’t updating.';
        else if (data && failure) notice = 'Can’t reach the server, so this isn’t updating.';
        else if (failing.length) notice = `Couldn’t read ${failing.join(' or ')} this time, so a paper or two may be missing.`;
        $('notice').textContent = notice;
        $('notice').hidden = !notice;
    }

    /** Fetch and draw an edition: `push` puts it in the history; `y` puts back where you were. */
    async function load(edition, { push = false, y = null } = {}) {
        try {
            const res = await fetch(url(`/api/dispatch${edition ? `?edition=${encodeURIComponent(edition)}` : ''}`), { credentials: 'same-origin' });
            if (res.status === 401) failure = 'signed-out';
            else if (!res.ok) throw new Error(res.status);
            else {
                data = await res.json();
                failure = null;
                await measure(data.stories);
                drawEdition();
                drawPoem();
                drawForecast();
                drawHolidays();
                const id = data.edition?.id;
                if (push && id) history.pushState({ edition: id }, '', url(`/dispatch/${id}`));
                else if (edition && fromPath() !== edition) history.replaceState({ edition: id }, '', url(`/dispatch/${edition}`));
                window.scrollTo({ top: push ? 0 : y ?? window.scrollY });
            }
        } catch (_) {
            failure = 'offline';
        }
        if (!data && failure) {
            const why = failure === 'offline' ? 'Can’t reach the server.' : document.querySelector('.signed-out-bar') ? '' : 'Signed out.';
            $('edition').innerHTML = `<p class="press-empty">${why} <button type="button" class="btn retry">Try again</button></p>`;
        }
        drawStatus();
    }

    // ---------- the forecast ----------
    // The week's question about the world (forecast.py), set in the paper's own modules, shown
    // open since they don't open or shut: the question as a story, with what the newsrooms wrote
    // as its standfirst, your answer beside it, then last week's result and your record. The
    // crowd's odds come from the server only once you've answered, or once it can't be answered.

    const pct = (n) => `${n}%`;
    const on = (iso) => new Date(iso).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    const OUTCOME = { yes: 'Yes', no: 'No', void: 'Void: refunded, not scored', closed: 'Awaiting the result' };

    /** What the newsrooms Situation reads wrote on it the week before, like a commentary's standfirst. */
    function reading(news) {
        return news.map((a) => `<div class="standfirst"><p class="standfirst-head"><span class="standfirst-k">${esc(a.outlet)}</span>
            <a href="${esc(webLink(a.url) || '#')}" target="_blank" rel="noopener">${esc(a.title)} <span aria-hidden="true">↗</span></a></p></div>`).join('');
    }

    /** The question as a story: the rule as its abstract, the market as its way out. */
    function asked(q, kicker) {
        const [rule] = (q.rule || '').split(/\n+/);
        return `<article class="story open w8">
            <p class="kicker"><span class="journal">${esc(kicker)}</span><span>${esc(q.subject)}</span><span>Closes ${esc(on(q.closes))}</span></p>
            <h2 class="story-title">${esc(q.title)}</h2>
            ${reading(q.news)}
            ${rule ? `<p class="abstract">${esc(rule)}</p>` : ''}
            <p class="links">${away(q.link, 'The market · Polymarket')}${away(q.source, 'Resolution source')}</p>
        </article>`;
    }

    /** What happened, what you said and what the crowd said, in a sentence. */
    function outcome(q) {
        const scored = q.score ? ` Brier score: you ${q.score.yours.toFixed(2)}, the crowd ${q.score.crowd.toFixed(2)}.` : '';
        const waiting = q.state === 'closed' ? ' Polymarket hasn’t settled it yet; it stays here until it does.' : '';
        const said = q.yours == null ? 'You didn’t answer' : `You said ${pct(q.yours)}`;
        return `${esc(OUTCOME[q.state])}. ${said}; the crowd ${pct(q.crowd)}.${scored}${waiting}`;
    }

    /** Your answer beside the question: the slider, saved when you let go of it, and greyed out
     * once the question closes. What came of it is news for the next issue, not this one. */
    function answer(q) {
        const set = q.yours != null;
        const live = q.state === 'open';
        // One line, so the module keeps its size from week to week; the kicker beside it says when it closes.
        const note = `${live ? (set ? 'Saved' : 'Slide to answer') : 'Closed'}.${set ? ` The crowd said ${pct(q.crowd)}.` : ''}`;
        return `<div class="story open w4 answer" data-edition="${esc(q.edition)}">
            <p class="kicker"><label class="journal" for="fcChance">Your chance of Yes</label></p>
            <output class="story-title" for="fcChance">${set ? pct(q.yours) : '—'}</output>
            <input class="range${set ? '' : ' unset'}" id="fcChance" type="range" min="0" max="100" step="5" value="${set ? q.yours : 50}"
                style="--v: ${set ? q.yours : 50}%" aria-valuetext="${set ? pct(q.yours) : 'not set'}"${live ? '' : ' disabled'}>
            <div class="range-scale" aria-hidden="true"><span>No</span><span>50</span><span>Yes</span></div>
            <p class="byline" role="status">${note}</p></div>`;
    }

    /** How you've done, in sentences: against the crowd, how often what you said came true, by subject. */
    function record(r) {
        if (!r.answered) return '';
        const ahead = r.yours < r.crowd ? 'ahead of' : r.yours > r.crowd ? 'behind' : 'level with';
        const bands = r.bands.filter((b) => b.said).map((b) => `${b.from}–${b.to}%, ${b.happened} of ${b.said}`).join('; ');
        const subjects = r.subjects.map((s) => `${esc(s.subject)} ${s.yours.toFixed(2)} (the crowd ${s.crowd.toFixed(2)})`).join(', ');
        return `<article class="story open w4"><p class="kicker"><span class="journal">Your record</span><span>${r.answered} answered</span></p>
            <p class="abstract">A Brier score of ${r.yours.toFixed(2)}, ${ahead} the crowd’s ${r.crowd.toFixed(2)}: 0 is perfect, and always saying 50% scores 0.25.
            When you said ${bands}, it happened that often. By subject: ${subjects}.</p></article>`;
    }

    /** The public holidays in the two weeks from when the issue comes out: a section only when there are some. */
    function drawHolidays() {
        const h = data?.holidays;
        const el = $('holidays');
        el.hidden = !h?.days.length;
        el.innerHTML = el.hidden ? '' : `<h2 class="section-head"><span>Holidays</span><span class="n">${esc(h.where)} · the next two weeks</span></h2>
            <div class="rest-list">${h.days.map((d) => `<article class="story s-line"><p class="kicker"><span class="journal">${esc(day(d.date, { weekday: 'short', day: 'numeric', month: 'short' }))}</span></p>
                <h2 class="story-title">${esc(d.name)}</h2></article>`).join('')}</div>`;
    }

    /** The week's poem, to learn by heart: set as a story, line by line, in Hong Kong's characters. */
    function drawPoem() {
        const p = data?.poem;
        const el = $('poem');
        el.hidden = !p;
        el.innerHTML = p ? `<h2 class="section-head"><span>The week’s poem</span><span class="n">${esc(p.book)}三百首</span></h2>
            <div class="row"><article class="story open w12" lang="zh-HK">
                <p class="kicker"><span class="journal">${esc(p.author)}</span><span>${esc(p.form)}</span></p>
                <h2 class="story-title">${esc(p.title)}</h2>
                <p class="abstract">${p.lines.map(esc).join('<br>')}</p>
            </article></div>` : '';
    }

    function drawForecast() {
        const f = data?.forecast;
        const el = $('forecast');
        el.hidden = !f;
        if (!f) { el.innerHTML = ''; return; }
        const q = f.question;
        // No question: being chosen as the issue comes out, none was eligible, or (an issue from
        // before there were questions) none was ever asked, and then there's nothing to say.
        const none = f.pending ? 'This week’s question is being chosen.'
            : f.asked ? 'No question this week: nothing big enough closes before the next issue.' : '';
        const results = f.results.map((r, i) => `<article class="story open ${i || !f.record.answered ? 'w12' : 'w8'}">
            <p class="kicker"><span class="journal">${i ? 'Settled late' : 'Last week’s question'}</span><span>${esc(r.subject)}</span></p>
            <h2 class="story-title">${esc(r.title)}</h2><p class="byline">${outcome(r)}</p></article>`);
        const second = [results[0], record(f.record)].filter(Boolean).join('');
        if (!q && !none && !second) { el.hidden = true; el.innerHTML = ''; return; }
        el.innerHTML = `<h2 class="section-head"><span>The week’s question</span><span class="n">From Polymarket</span></h2>
            ${q ? `<div class="row">${asked(q, 'Forecast')}${answer(q)}</div>` : none ? `<p class="press-empty">${none}</p>` : ''}
            ${second ? `<div class="row">${second}</div>` : ''}
            ${results.slice(1).map((r) => `<div class="row">${r}</div>`).join('')}`;
    }

    // Sliding shows your number; letting go saves it.
    $('forecast').addEventListener('input', (e) => {
        if (!e.target.matches('.range')) return;
        e.target.classList.remove('unset');
        e.target.style.setProperty('--v', pct(e.target.value));
        e.target.setAttribute('aria-valuetext', pct(e.target.value));
        e.target.closest('.answer').querySelector('output').textContent = pct(e.target.value);
    });
    $('forecast').addEventListener('change', async (e) => {
        if (!e.target.matches('.range')) return;
        const box = e.target.closest('.answer');
        const status = box.querySelector('[role=status]');
        status.textContent = 'Saving…';
        try {
            const res = await fetch(url(`/api/forecast/${encodeURIComponent(box.dataset.edition)}`), {
                method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chance: Number(e.target.value) }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.error || 'Not saved. Let go again to retry.');
            data.forecast = body;
            drawForecast();
        } catch (err) {
            status.textContent = err.message === 'Failed to fetch' ? 'Offline, so not saved. Try again.' : err.message;
        }
    });

    // ---------- reading ----------

    /** A story opens in place to its whole abstract, its figure at full size and the links out;
     * a tap anywhere on it shuts it again (its row grows). Shut from below, it comes back into view. */
    function toggle(el) {
        const open = el.classList.toggle('open');
        el.querySelector('.story-open').setAttribute('aria-expanded', open);
        const img = el.querySelector('.fig img[data-large]');
        if (open && img) { img.src = img.dataset.large; img.removeAttribute('data-large'); }
        if (!open && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: 'start' });
    }

    // A figure opens on its own, over the page, to be zoomed; Back or Close puts it away.
    const viewer = $('viewer');
    let viewing = false;
    let skipPop = false;

    function view(el) {
        const img = el.querySelector('.fig img');
        $('viewerImg').src = img.dataset.large || img.src;
        $('viewerImg').alt = img.alt;
        $('viewerTitle').textContent = plain(el.querySelector('.story-open').innerHTML);
        $('viewerStage').classList.remove('zoomed');
        viewer.showModal();
        viewing = true;
        history.pushState({ ...history.state, figure: true }, '');
    }

    viewer.addEventListener('close', () => {
        if (!viewing) return;
        viewing = false;
        skipPop = true;
        history.back();
    });
    $('viewerClose').addEventListener('click', () => viewer.close());
    /** A tap zooms in where it lands, another zooms back out to the whole figure. */
    $('viewerImg').addEventListener('click', (e) => {
        const stage = $('viewerStage');
        const box = e.target.getBoundingClientRect();
        const at = [(e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height];
        stage.classList.toggle('zoomed');
        if (stage.classList.contains('zoomed')) {
            stage.scrollTo(at[0] * stage.scrollWidth - stage.clientWidth / 2, at[1] * stage.scrollHeight - stage.clientHeight / 2);
        }
    });

    $('edition').addEventListener('click', (e) => {
        const to = e.target.closest('[data-edition]');
        if (to) { e.preventDefault(); return load(to.dataset.edition, { push: true }); }
        if (e.target.closest('.retry')) return load(fromPath());
        if (e.target.closest('a') || window.getSelection()?.toString()) return;
        const el = e.target.closest('.story');
        if (!el) return;
        if (e.target.closest('.fig') && (el.classList.contains('open') || el.classList.contains('s-lead'))) return view(el);
        toggle(el);
    });
    document.addEventListener('click', (e) => {
        const button = e.target.closest('.folio-step');
        if (button?.dataset.edition) load(button.dataset.edition, { push: true });
    });
    // ← and → turn the page.
    document.addEventListener('keydown', (e) => {
        if (viewing || e.altKey || e.ctrlKey || e.metaKey || /input|textarea|select/i.test(e.target.tagName)) return;
        const button = { ArrowLeft: $('prevEdition'), ArrowRight: $('nextEdition') }[e.key];
        if (button && !button.disabled) button.click();
    });

    // The issue is in the address (/dispatch/2026-W44), so back and forward step through what you
    // read, each where you left it. Leaving for News and coming back soon after does too.
    const fromPath = () => (location.pathname.match(/\/dispatch\/(\d{4}-W\d{2})\/?$/) || [])[1] || null;
    const PLACE = 'situation.dispatch.place';
    const SOON = 30 * 60000;
    history.scrollRestoration = 'manual';
    window.addEventListener('popstate', (e) => {
        if (skipPop) { skipPop = false; return; }
        if (viewing) { viewing = false; viewer.close(); return; }
        load(fromPath(), { y: e.state?.y ?? 0 });
    });
    let saving = 0;
    window.addEventListener('scroll', () => {
        clearTimeout(saving);
        saving = setTimeout(() => {
            if (!data?.edition || viewing) return;
            history.replaceState({ ...history.state, edition: data.edition.id, y: window.scrollY }, '');
            try { sessionStorage.setItem(PLACE, JSON.stringify({ edition: data.edition.id, y: window.scrollY, at: Date.now() })); } catch (_) { /* no storage: start at the top */ }
        }, 200);
    }, { passive: true });

    function place() {
        if (fromPath()) return { edition: fromPath(), y: history.state?.y ?? 0 };
        try {
            const saved = JSON.parse(sessionStorage.getItem(PLACE));
            if (saved && Date.now() - saved.at < SOON) return saved;
        } catch (_) { /* no storage */ }
        return { edition: null, y: history.state?.y ?? 0 };
    }

    const start = place();
    load(start.edition, { y: start.y });
    let resizing = 0;
    window.addEventListener('resize', () => { clearTimeout(resizing); resizing = setTimeout(fit, 150); });
    // Turned wide (a tablet, a window), the desktop's slots need every figure's proportions.
    narrow.addEventListener('change', () => data && !narrow.matches && measure(data.stories).then(() => drawEdition()));
    document.fonts?.ready.then(fit);
    setInterval(drawStatus, 60000);
    setInterval(() => !document.hidden && !viewing && load(data?.edition?.id), 30 * 60000);
})();
