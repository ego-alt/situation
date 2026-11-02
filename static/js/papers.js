/* Papers: one week's neuroscience, laid out as a front page. The cover story leads,
 * the papers with a commentary come next and larger, the rest fill the columns. */
(() => {
    const ROOT = window.APP_ROOT || '';
    const url = (p) => `${ROOT}${p}`;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const webLink = (u) => (/^https?:\/\//i.test(String(u || '')) ? u : null);
    // Titles and abstracts come from the server already escaped, with only <i>, <sub> and <sup> kept.
    const marked = (s) => String(s ?? '');

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

    /** "26 Oct – 1 Nov 2026" */
    function span(start, end) {
        const sameMonth = start.slice(0, 7) === end.slice(0, 7);
        const from = day(start, sameMonth ? { day: 'numeric' } : { day: 'numeric', month: 'short' });
        return `${from} – ${day(end, { day: 'numeric', month: 'short', year: 'numeric' })}`;
    }

    /** First and last author: who did it and whose lab it came from. */
    function byline(names) {
        if (!names.length) return '';
        const list = names.length > 1 ? `${names[0]} … ${names[names.length - 1]}` : names[0];
        const text = names.length === 2 ? `${names[0]} & ${names[1]}` : list;
        const all = names.length > 2 ? ` title="${esc(names.slice(0, 40).join(', ') + (names.length > 40 ? ` and ${names.length - 40} more` : ''))}"` : '';
        return `<p class="byline"${all}>${esc(text)}${names.length > 2 ? ` <span class="n">${names.length} authors</span>` : ''}</p>`;
    }

    function figure(p, large) {
        const src = webLink(large ? p.figure_large || p.figure : p.figure);
        if (!src) return '';
        const alt = p.cover && p.cover_image === p.figure ? 'The issue’s cover' : 'The paper’s first figure';
        return `<figure class="fig"><img src="${esc(src)}" alt="${alt}" loading="${large ? 'eager' : 'lazy'}" decoding="async" referrerpolicy="no-referrer"
            onerror="this.closest('figure').remove()"></figure>`;
    }

    function cover(p) {
        const src = webLink(p.cover_image);
        return src ? `<figure class="fig fig-cover"><img src="${esc(src)}" alt="This week’s cover of ${esc(p.journal)}" decoding="async" referrerpolicy="no-referrer"
            onerror="this.closest('figure').remove()"><figcaption>On the cover</figcaption></figure>` : '';
    }

    // The commentary speaks for itself in the standfirst; only the cover needs a mark.
    const marks = (p) => (p.cover ? '<span class="mark mark-cover">Cover story</span>' : '');

    /** The commentary's own headline, under the paper's: what the field made of it. */
    function standfirst(p) {
        return p.commentary.map((c) => {
            const link = webLink(c.url);
            const title = marked(c.title);
            return `<p class="standfirst"><span class="standfirst-k">${esc(c.label)}</span>${link
                ? `<a href="${esc(link)}" target="_blank" rel="noopener">${title}</a>` : title}</p>`;
        }).join('');
    }

    function links(p) {
        const out = [];
        const paper = webLink(p.url);
        if (paper) out.push(`<a href="${esc(paper)}" target="_blank" rel="noopener">${esc(p.journal)} <span aria-hidden="true">↗</span></a>`);
        const free = p.free && webLink(p.free.url);
        if (free) out.push(`<a class="free" href="${esc(free)}" target="_blank" rel="noopener">Free · ${esc(p.free.label)} <span aria-hidden="true">↗</span></a>`);
        return out.length ? `<p class="links">${out.join('')}</p>` : '';
    }

    function kicker(p) {
        return `<p class="kicker"><span class="journal">${esc(p.journal)}</span><span>${day(p.published)}</span>${marks(p)}</p>`;
    }

    /** One story. `size` is lead, rail, line, or mod: a module `w` of 12 columns wide on the
     * front page, its figure on top, beside the text (wide slots) or left out (a text column). */
    function story(p, size, w = 0) {
        const lead = size === 'lead';
        const side = size === 'mod' && w >= 7;
        const text = size === 'mod' && (!p.figure || (w === 3 && ratio(p) > 1.6));
        const art = lead && p.cover_image ? cover(p) : size === 'rail' || text ? '' : figure(p, lead);
        const head = `<div class="story-head">
                ${kicker(p)}
                <h2 class="story-title">${marked(p.title)}</h2>
                ${byline(p.authors)}
                ${standfirst(p)}
            </div>`;
        // The lead's figure and a wide module's float, so the text wraps around and under them.
        const kind = size === 'mod' ? `s-mod w${w}${side ? ' side' : ''}` : `s-${size}`;
        return `<article class="story ${kind}${art ? ' has-fig' : ''}" data-doi="${esc(p.doi)}" tabindex="0">
            <div class="flow">${lead ? head + art : art + head}${p.abstract ? `<p class="abstract">${marked(p.abstract)}</p>` : ''}</div>
            ${links(p)}
        </article>`;
    }

    // Each row below the lead takes the next of these shapes, so the page changes rhythm as it goes.
    const SHAPES = [[6, 3, 3], [4, 4, 4], [3, 6, 3], [7, 5], [4, 8], [3, 3, 6], [5, 7]];
    const MODULES = 9;   // past this many, the rest of a long week is a list

    // Each figure's width over height, so the widest goes in the widest slot.
    const ratios = new Map();
    const ratio = (p) => ratios.get(p.doi) ?? (p.figure ? 1.3 : 0);

    function measure(papers) {
        const todo = papers.filter((p) => webLink(p.figure) && !ratios.has(p.doi)).map((p) => new Promise((done) => {
            const img = new Image();
            img.referrerPolicy = 'no-referrer';
            img.onload = () => { ratios.set(p.doi, img.naturalWidth / img.naturalHeight); done(); };
            img.onerror = done;
            img.src = p.figure;
        }));
        return Promise.race([Promise.all(todo), new Promise((done) => setTimeout(done, 1500))]);
    }

    /** The lead with a rail beside it, then rows of modules in changing shapes, then in a
     * long week the remainder as a list. */
    function compose(papers) {
        const [lead, ...rest] = papers;
        const majors = rest.filter((p) => p.tier === 'major');
        const minors = rest.filter((p) => p.tier !== 'major');
        const rail = majors.splice(0, 2);
        rail.push(...minors.splice(0, 2 - rail.length));
        const rows = [rail.length ? `<div class="row row-front">${story(lead, 'lead')}<div class="rail">${rail.map((p) => story(p, 'rail')).join('')}</div></div>`
            : `<div class="row">${story(lead, 'lead')}</div>`];
        const queue = [...majors, ...minors];
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
                <h2 class="section-rule"><span>The rest of the week</span><span class="n">${queue.length} ${queue.length === 1 ? 'paper' : 'papers'}</span></h2>
                <div class="rest-list">${queue.map((p) => story(p, 'line')).join('')}</div></section>`);
        }
        return rows.join('');
    }

    const narrow = window.matchMedia('(max-width: 860px)');
    const ROW_MAX = 600;   // the most a row of modules runs to before its text is cut

    // How tall a story's own content is (the grid stretches the story itself to its row).
    const natural = (el) => el.lastElementChild.getBoundingClientRect().bottom - el.getBoundingClientRect().top;

    /** Cut a story's text at a whole line so the story ends at `height`; the last line fades. */
    function clamp(el, height) {
        const flow = el.querySelector('.flow');
        const abstract = flow.querySelector('.abstract');
        if (!abstract || natural(el) <= height + 1) return;
        const room = height - (natural(el) - flow.offsetHeight);
        const line = parseFloat(getComputedStyle(abstract).lineHeight);
        const lines = Math.max(2, Math.floor((room - abstract.offsetTop) / line));
        flow.style.maxHeight = `${abstract.offsetTop + lines * line}px`;
        flow.classList.add('cut');
    }

    /** Even out each row on a desktop: every story's text runs to the foot of its row (the
     * lead's, or the widest module's), so no column ends with a gap. */
    function fit() {
        const rows = [...document.querySelectorAll('#edition .row')];
        for (const flow of document.querySelectorAll('#edition .flow')) { flow.style.maxHeight = ''; flow.classList.remove('cut'); }
        if (narrow.matches) return;
        for (const row of rows) {
            const lead = row.querySelector('.s-lead');
            if (lead) {
                const rail = [...row.querySelectorAll('.rail .story')];
                const gap = rail.length > 1 ? rail[1].getBoundingClientRect().top - rail[0].getBoundingClientRect().bottom : 0;
                rail.forEach((el) => clamp(el, (natural(lead) - gap * (rail.length - 1)) / rail.length));
                continue;
            }
            const stories = [...row.children].filter((el) => !el.classList.contains('open'));
            const widest = stories.reduce((a, b) => (b.offsetWidth > a.offsetWidth ? b : a), stories[0]);
            if (!widest) continue;
            const height = Math.min(natural(widest), ROW_MAX);
            stories.forEach((el) => clamp(el, height));
        }
    }

    function discussedHtml(items) {
        if (!items?.length) return '';
        const rows = items.map((p) => {
            const c = p.commentary[0];
            const paper = webLink(p.url);
            const note = webLink(c.url);
            const back = p.edition ? `<a class="btn" href="${esc(url(`/papers/${p.edition}`))}" data-edition="${esc(p.edition)}">In No. ${Number(p.edition.slice(6))}</a>` : '';
            return `<li class="discussed-item">
                <p class="kicker"><span class="journal">${esc(c.journal)}</span><span>${esc(c.label)}</span></p>
                <p class="discussed-note">${note ? `<a href="${esc(note)}" target="_blank" rel="noopener">${marked(c.title)}</a>` : marked(c.title)}</p>
                <p class="discussed-paper"><span class="standfirst-k">Paper</span>${paper ? `<a href="${esc(paper)}" target="_blank" rel="noopener">${marked(p.title)}</a>` : marked(p.title)}
                    <span class="discussed-when">${esc(p.journal)}, ${day(p.published, { day: 'numeric', month: 'short', year: 'numeric' })}</span> ${back}</p>
            </li>`;
        }).join('');
        return `<section class="discussed" aria-label="Commentary on earlier papers">
            <h2 class="section-rule"><span>Commentary on earlier papers</span></h2><ul>${rows}</ul></section>`;
    }

    function drawEdition() {
        const e = data.edition;
        const n = data.next_edition;
        const out = day(n.out, { weekday: 'long', day: 'numeric', month: 'short' });
        const sofar = `${n.so_far} ${n.so_far === 1 ? 'paper' : 'papers'} so far`;
        if (!e) {
            $('folio').textContent = 'The first edition is being set';
            $('edition').innerHTML = `<p class="press-empty">The first edition comes out ${esc(out)}. ${esc(sofar)}.</p>`;
        } else {
            const commented = data.papers.filter((p) => p.commentary.length).length;
            const count = `${data.papers.length} ${data.papers.length === 1 ? 'paper' : 'papers'}${e.in_progress ? ' so far' : ''}`;
            $('folio').innerHTML = `<span>No. ${e.number}</span><span>${esc(span(e.start, e.end))}</span>
                <span>${count}${commented ? `, ${commented} with commentary` : ''}</span>`;
            $('edition').innerHTML = (data.papers.length ? compose(data.papers) : '<p class="press-empty">No papers this week.</p>')
                + discussedHtml(data.discussed);
            fit();
            // The lead's figure sets the front row's height once it's in.
            $('edition').querySelector('.s-lead img')?.addEventListener('load', fit);
        }
        step($('prevEdition'), e?.prev);
        step($('nextEdition'), e?.next);
        const names = data.journals;
        const under = e?.in_progress ? `This week’s edition fills in as papers come out, until ${esc(out)}. `
            : e?.next && data.editions[data.editions.length - 1] === e.next && n.so_far ? `No. ${Number(e.next.slice(6))} is under way, ${esc(sofar)}. ` : '';
        $('foot').innerHTML = `<p>${under}Research papers in neuroscience from
            ${esc(names.slice(0, -1).join(', '))} and ${esc(names[names.length - 1])}, by the week they came out.</p>
            <p>Abstracts and links: <a href="https://europepmc.org/" target="_blank" rel="noopener">Europe PMC</a>. Figures: the publishers.</p>`;
        document.title = e ? `Situation · Papers No. ${e.number}` : 'Situation · Papers';
    }

    function step(button, id) {
        button.disabled = !id;
        button.dataset.edition = id || '';
        button.querySelector('.folio-step-label').textContent = id ? `No. ${Number(id.slice(6))}` : '';
        button.setAttribute('aria-label', id ? `${button.id === 'prevEdition' ? 'Previous' : 'Next'} edition, No. ${Number(id.slice(6))}` : 'No more editions');
    }

    function drawStatus() {
        $('updated').textContent = failure === 'signed-out' ? 'signed out' : failure ? 'offline' : ago(data?.fetched);
        const s = data?.sources;
        if (s) {
            $('journals').textContent = `${s.ok}/${s.total}`;
            $('journalsReadout').classList.toggle('warn', s.failing.length > 0);
            $('journalsReadout').title = s.failing.length ? `Couldn’t read: ${s.failing.join(', ')}` : 'Every journal answered';
        }
        let notice = '';
        if (failure === 'signed-out') notice = 'Signed out, so this isn’t updating.';
        else if (failure) notice = 'Can’t reach the server, so this isn’t updating.';
        $('notice').textContent = notice;
        $('notice').hidden = !notice;
    }

    async function load(edition, { push = false } = {}) {
        try {
            const res = await fetch(url(`/api/papers${edition ? `?edition=${encodeURIComponent(edition)}` : ''}`), { credentials: 'same-origin' });
            if (res.status === 401) failure = 'signed-out';
            else if (!res.ok) throw new Error(res.status);
            else {
                data = await res.json();
                failure = null;
                await measure(data.papers);
                drawEdition();
                if (push && data.edition) history.pushState({ edition: data.edition.id }, '', url(`/papers/${data.edition.id}`));
                if (push) window.scrollTo({ top: 0 });
            }
        } catch (_) {
            failure = 'offline';
        }
        if (!data && failure) {
            $('edition').innerHTML = `<p class="press-empty">${failure === 'signed-out' ? 'Signed out.' : 'Can’t reach the server.'}
                <button type="button" class="btn retry">Try again</button></p>`;
        }
        drawStatus();
    }

    // ---------- reading ----------

    /** A story opens in place to its whole abstract and full figure; again closes it (its row grows). */
    function toggle(el) {
        el.classList.toggle('open');
        el.setAttribute('aria-expanded', el.classList.contains('open'));
    }

    $('edition').addEventListener('click', (e) => {
        const to = e.target.closest('[data-edition]');
        if (to) { e.preventDefault(); return load(to.dataset.edition, { push: true }); }
        if (e.target.closest('.retry')) return load(fromPath());
        if (e.target.closest('a') || window.getSelection()?.toString()) return;
        const el = e.target.closest('.story');
        if (el) toggle(el);
    });
    $('edition').addEventListener('keydown', (e) => {
        if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('story')) {
            e.preventDefault();
            toggle(e.target);
        }
    });
    for (const id of ['prevEdition', 'nextEdition']) {
        $(id).addEventListener('click', () => $(id).dataset.edition && load($(id).dataset.edition, { push: true }));
    }
    // ← and → turn the page.
    document.addEventListener('keydown', (e) => {
        if (e.altKey || e.ctrlKey || e.metaKey || /input|textarea|select/i.test(e.target.tagName)) return;
        const button = { ArrowLeft: $('prevEdition'), ArrowRight: $('nextEdition') }[e.key];
        if (button && !button.disabled) button.click();
    });
    // The edition is in the address (/papers/2026-W44), so back and forward step through what you read.
    const fromPath = () => (location.pathname.match(/\/papers\/(\d{4}-W\d{2})\/?$/) || [])[1] || null;
    window.addEventListener('popstate', () => load(fromPath()));

    load(fromPath());
    let resizing = 0;
    window.addEventListener('resize', () => { clearTimeout(resizing); resizing = setTimeout(fit, 150); });
    document.fonts?.ready.then(fit);
    setInterval(drawStatus, 60000);
    setInterval(() => !document.hidden && load(data?.edition?.id), 30 * 60000);
})();
