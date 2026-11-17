/* Dispatch: one day's issue, laid out as a front page. Its papers come first, on the days there
 * are some: one leads, the others fill the rail beside it and a row under it; then the day's
 * print, poem (or passage) and question, and the dates coming up. Everything is read here: a
 * story opens in place, and only an opened one links out to where it's from. */
(() => {
    const { url, $, esc, webLink, narrow } = App;
    // Titles and abstracts come from the server already escaped, with only <i>, <sub> and <sup> kept.
    const marked = (s) => String(s ?? '');
    const section = (name) => data?.sections?.[name];   // what one of the issue's sections took (dispatch.SECTIONS)
    const plain = (html) => { const el = document.createElement('div'); el.innerHTML = html; return el.textContent; };

    const ROW_MAX = 600;     // the most a row runs to before its text is cut
    const MIN_LINES = 4;     // a story shows at least this much of its abstract
    const NIGHT = [19, 7];   // from 7pm to 7am on your clock, the figures, mostly drawn on white, are dimmed

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

    /** First and last author: who did it and whose lab it came from. */
    function byline(names) {
        if (!names.length) return '';
        const list = names.length > 1 ? `${names[0]} … ${names[names.length - 1]}` : names[0];
        const text = names.length === 2 ? `${names[0]} & ${names[1]}` : list;
        const all = names.length > 2 ? ` title="${esc(`${names.length} authors: ${names.slice(0, 40).join(', ')}${names.length > 40 ? ` and ${names.length - 40} more` : ''}`)}"` : '';
        return `<p class="byline"${all}>${esc(text)}</p>`;
    }

    function figure(p, large, caption = '') {
        const src = webLink(large ? p.figure_large || p.figure : p.figure);
        if (!src) return '';
        const big = !large && webLink(p.figure_large);
        const r = large && ratios.get(p.id);
        return `<figure class="fig"${r ? ` style="--r: ${r.toFixed(3)}"` : ''}><img src="${esc(src)}"${big ? ` data-large="${esc(big)}"` : ''} alt="Figure from “${esc(plain(p.title))}”"
            loading="${large ? 'eager' : 'lazy'}" decoding="async" referrerpolicy="no-referrer" onerror="this.closest('figure').remove()">${caption ? `<figcaption>${caption}</figcaption>` : ''}</figure>`;
    }

    /** The issue's cover, at the head of the rail beside the story it shows. */
    function cover(p) {
        const src = webLink(p.cover_image);
        return src ? `<figure class="rail-cover"><img src="${esc(src)}" alt="This week’s cover of ${esc(p.source)}" decoding="async" referrerpolicy="no-referrer"
            onerror="this.closest('figure').remove()"><figcaption><span class="rail-cover-k">On the cover</span>
            <span class="rail-cover-note">This week’s ${esc(p.source)} shows the lead story.</span></figcaption></figure>` : '';
    }

    const summary = (c) => (c.summary ? `<p class="standfirst-sum">${marked(c.summary)}</p>` : '');

    /** A story's title (already safe to put in the page): the button that opens it, unless it's shown whole. */
    const heading = (title, opens = true) => `<h2 class="story-title">${opens ? `<button type="button" class="story-open" aria-expanded="false">${title}</button>` : title}</h2>`;

    /** The commentary's own headline and summary, under the paper's: what the field made of it. */
    function standfirst(p) {
        return p.commentary.map((c) => `<div class="standfirst"><p class="standfirst-head"><span class="standfirst-k">${esc(c.label)}</span> ${marked(c.title)}</p>${summary(c)}</div>`).join('');
    }

    /** A link out, or nothing when there's nowhere safe to go. */
    function away(href, label, cls = '') {
        const link = webLink(href);
        return link ? `<a${cls ? ` class="${cls}"` : ''} href="${esc(link)}" target="_blank" rel="noopener">${esc(label)} <span aria-hidden="true">↗</span></a>` : '';
    }

    /** The way out, shown only in an opened story: everything else is read here. */
    function links(p) {
        const all = [away(p.url, p.source), p.free && away(p.free.url, `Free · ${p.free.label}`, 'free'),
            ...p.commentary.map((c) => away(c.url, c.label))].filter(Boolean);
        return all.length ? `<p class="links">${all.join('')}</p>` : '';
    }

    function kicker(p) {
        // An earlier paper, in this issue for the commentary that has since come out on it.
        const year = p.published.slice(0, 4) !== data.edition.id.slice(0, 4) && { year: 'numeric' };
        const earlier = p.earlier ? `<span>From ${esc(day(p.published, { day: 'numeric', month: 'short', ...year }))}</span>` : '';
        return `<p class="kicker"><span class="kicker-k">${esc(p.source)}</span> ${p.cover ? '<span class="kicker-flash">Cover story</span>' : ''}${earlier}</p>`;
    }

    /** One story. `size` is lead, rail or mod: a module `w` of 12 columns wide, which floats its
     * figure in the corner for the text to wrap (wide) or puts its headline over it (the narrower
     * of two). In the rail a story leads with its figure, or on a desktop goes without under a
     * commentary's standfirst. On a phone every story but the lead shows its figure as a
     * thumbnail. The title is the button that opens the story in place. */
    function story(p, size, w = 0) {
        const lead = size === 'lead';
        const mod = size === 'mod';
        const art = figure(p, lead);
        const bare = size === 'rail' && p.commentary.length;
        const headFirst = mod && w === 5;
        const head = `<div class="story-head">
                ${kicker(p)}
                ${heading(marked(p.title))}
                ${byline(p.authors)}
                ${standfirst(p)}
            </div>`;
        const kind = mod ? `s-mod w${w}${w >= 7 ? ' side' : ''}` : `s-${size}`;
        const marks = `${art && !bare ? ' has-fig' : ''}${art && bare ? ' bare' : ''}${headFirst ? ' head-first' : ''}${p.commentary.length ? ' has-note' : ''}`;
        return `<article class="story ${kind}${marks}">
            <div class="flow">${lead || headFirst ? head + art : art + head}${p.abstract ? `<p class="abstract">${marked(p.abstract)}</p>` : ''}</div>
            ${links(p)}
        </article>`;
    }

    // Each figure's width over height, so the widest goes in the widest slot.
    const ratios = new Map();
    const ratio = (p) => ratios.get(p.id) ?? (p.figure ? 1.3 : 0);

    /** Each figure's proportions, for the desktop's slots. A phone only needs the lead's, to keep
     * its place while it loads, so it fetches just that (the large one it shows). */
    function measure(stories) {
        const list = narrow.matches ? stories.slice(0, 1) : stories;
        const todo = list.filter((p) => webLink(p.figure) && !ratios.has(p.id)).map((p) => new Promise((done) => {
            const img = new Image();
            img.referrerPolicy = 'no-referrer';
            img.onload = () => { ratios.set(p.id, img.naturalWidth / img.naturalHeight); done(); };
            img.onerror = done;
            img.src = narrow.matches ? webLink(p.figure_large) || p.figure : p.figure;
        }));
        return Promise.race([Promise.all(todo), new Promise((done) => setTimeout(done, 1500))]);
    }

    /** The lead with a rail beside it, then a row of what the rail had no room for. The stories
     * come in order (papers.front), the lead first, and are three at most (papers.MOST). */
    function compose(stories) {
        const [lead, ...queue] = stories;
        const art = cover(lead);
        // The rail runs as long as the lead: two stories, one less with the cover at its head or,
        // on a desktop, beside a lead without a figure (its text is short).
        const rail = queue.splice(0, 2 - !!art - (!narrow.matches && !ratio(lead)));
        const front = rail.length || art ? `<div class="row row-front">${story(lead, 'lead')}<div class="rail">${art}${rail.map((p) => story(p, 'rail')).join('')}</div></div>`
            : `<div class="row">${story(lead, 'lead')}</div>`;
        // What's left, the wider figure in the wider slot.
        const widths = queue.length > 1 ? [7, 5] : [12];
        const rest = queue.sort((a, b) => ratio(b) - ratio(a)).map((p, i) => story(p, 'mod', widths[i])).join('');
        return front + (rest && `<div class="row">${rest}</div>`);
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
     * column ends with a gap. The row is as tall as its widest story (or the lead), to ROW_MAX,
     * but never so short that a story beside it shows less than a few lines. */
    function fit() {
        const rows = [...document.querySelectorAll('#edition .row')];
        for (const flow of document.querySelectorAll('#edition .flow')) { flow.style.maxHeight = ''; flow.classList.remove('cut'); }
        if (narrow.matches) return;
        for (const row of rows) {
            const lead = row.querySelector('.s-lead');
            if (lead) {
                const rail = [...row.querySelectorAll('.rail .story')];
                const gaps = rail.length > 1 ? (rail[1].getBoundingClientRect().top - rail[0].getBoundingClientRect().bottom) * (rail.length - 1) : 0;
                const above = rail.length ? rail[0].getBoundingClientRect().top - lead.getBoundingClientRect().top : 0;   // the cover, if any
                const each = Math.max((Math.min(natural(lead), ROW_MAX) - above - gaps) / (rail.length || 1), ...rail.map(need));
                rail.forEach((el) => clamp(el, each));
                clamp(lead, Math.max(ROW_MAX, above + gaps + each * rail.length));
                continue;
            }
            const stories = [...row.children].filter((el) => !el.classList.contains('open'));
            const widest = stories.reduce((a, b) => (b.offsetWidth > a.offsetWidth ? b : a), stories[0]);
            if (!widest) continue;
            const height = Math.min(Math.max(natural(widest), ...stories.map(need)), ROW_MAX);
            stories.forEach((el) => clamp(el, height));
        }
    }

    /** An issue as it came out: numbered by the day of the year, with its day. */
    function drawEdition() {
        const e = data.edition;
        if (!e) {
            $('folio').textContent = 'The first issue is being set';
            $('edition').innerHTML = '<p class="press-empty">It comes out in the morning, at 8 in Hong Kong.</p>';
        } else {
            $('folio').innerHTML = `<span>No. ${e.number}</span><span>${esc(day(e.id, { weekday: 'short', day: 'numeric', month: 'short' }))}<span class="folio-year"> ${e.id.slice(0, 4)}</span></span>`;
            $('edition').innerHTML = section('papers')?.length ? compose(section('papers')) : '';
            fit();
            // The lead's figure sets the front row's height once it's in.
            $('edition').querySelector('.s-lead img')?.addEventListener('load', fit);
        }
        step('prev', e?.prev);
        step('next', e?.next);
        document.title = e ? `Situation · Dispatch No. ${e.number}` : 'Situation · Dispatch';
    }

    /** The steps to the issue before or after, in the folio, each by its day. */
    function step(dir, id) {
        for (const button of document.querySelectorAll(`.folio-step[data-dir="${dir}"]`)) {
            button.disabled = !id;
            button.dataset.edition = id || '';
            button.querySelector('.folio-step-label').textContent = id ? day(id) : '';
            button.setAttribute('aria-label', id ? `${dir === 'prev' ? 'Previous' : 'Next'} issue, ${day(id)}` : 'No more issues');
        }
    }

    function drawStatus() {
        const fresh = data?.fetched ? ago(data.fetched) : null;
        $('updated').textContent = failure === 'signed-out' ? 'Signed out' : failure ? 'Offline'
            : fresh ? `Updated ${fresh}` : 'Not updated yet';
        const failing = data?.sources?.failing || [];
        $('sourceStatus').classList.toggle('warn', !!failure || failing.length > 0);
        $('sourceStatus').title = failing.length ? `Couldn’t read: ${failing.join(', ')}`
            : data?.sources ? `All ${data.sources.total} sources answered` : '';
        // Said once: with nothing to show, the page itself says it (and the sign-in bar, when there is one).
        let notice = '';
        if (data && failure === 'signed-out') notice = document.querySelector('.signed-out-bar') ? '' : 'Signed out, so this isn’t updating.';
        else if (data && failure) notice = 'Can’t reach the server, so this isn’t updating.';
        else if (failing.length) notice = `Couldn’t read ${failing.join(' or ')} this time.`;
        $('notice').textContent = notice;
        $('notice').hidden = !notice;
    }

    // The issue the page came with (app.py): the first one asked for, when it's that one.
    let given = JSON.parse($('issue').textContent);

    /** Fetch and draw an issue: `push` puts it in the history; `y` puts back where you were. */
    async function load(edition, { push = false, y = null } = {}) {
        try {
            const first = given && (!edition || edition === given.edition?.id) ? given : null;
            given = null;
            const res = first ? null : await fetch(url(`/api/dispatch${edition ? `?edition=${encodeURIComponent(edition)}` : ''}`), { credentials: 'same-origin' });
            if (res?.status === 401) failure = 'signed-out';
            else if (res && !res.ok) throw new Error(res.status);
            else {
                data = first || await res.json();
                failure = null;
                // The woodcut needs only the day: it doesn't wait for the figures to be measured.
                Woodcut.draw($('woodcut'), data.edition?.out);
                await measure(section('papers') || []);
                drawEdition();
                drawPrint();
                drawPoem();
                drawBird();
                drawForecast();
                drawDates();
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
    // The day's question about the world (forecast.py), set in the paper's own modules, shown
    // open since they don't open or shut: the question as a story, with what the newsrooms wrote
    // as its standfirst, your answer beside it, then the results just in. The
    // crowd's odds come from the server only once you've answered, or once it can't be answered.

    const pct = (n) => `${n}%`;
    const on = (iso) => new Date(iso).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    const OUTCOME = { yes: 'Yes', no: 'No', void: 'Void: refunded, not scored' };

    /** What the newsrooms Situation reads wrote on it the week before, like a commentary's standfirst. */
    function reading(news) {
        return news.map((a) => `<div class="standfirst"><p class="standfirst-head"><span class="standfirst-k">${esc(a.outlet)}</span>
            <a href="${esc(webLink(a.url) || '#')}" target="_blank" rel="noopener">${esc(a.title)} <span aria-hidden="true">↗</span></a></p></div>`).join('');
    }

    /** The question as a story: the rule as its abstract, the market as its way out. */
    function asked(q, kicker) {
        const [rule] = (q.rule || '').split(/\n+/);
        return `<article class="story open w8">
            <p class="kicker"><span class="kicker-k">${esc(kicker)}</span><span>${esc(q.subject)}</span><span>Closes ${esc(on(q.closes))}</span></p>
            <h2 class="story-title">${esc(q.title)}</h2>
            ${reading(q.news)}
            ${rule ? `<p class="abstract">${esc(rule)}</p>` : ''}
            <p class="links">${away(q.link, 'Polymarket')}${away(q.source, 'Resolution source')}</p>
        </article>`;
    }

    /** What happened, what you said and what the crowd said, in a sentence. */
    function outcome(q) {
        const scored = q.score ? ` Brier score: you ${q.score.yours.toFixed(2)}, the crowd ${q.score.crowd.toFixed(2)}.` : '';
        const said = q.yours == null ? 'You didn’t answer' : `You said ${pct(q.yours)}`;
        return `${esc(OUTCOME[q.state])}. ${said}; the crowd ${pct(q.crowd)}.${scored}`;
    }

    /** Your answer beside the question: the slider, saved when you let go of it, and greyed out
     * once the question closes. What came of it is news for a later issue, not this one. */
    function answer(q) {
        const set = q.yours != null;
        const live = q.state === 'open';
        // Much the same length whatever it says, a line on a desktop, so the module keeps its size from day to day.
        const by = new Date(q.closes).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
        const note = !live ? `Closed; the crowd ${pct(q.crowd)}. Result to follow.`
            : set ? `Saved; the crowd ${pct(q.crowd)}. Change by ${by}.` : `Answer by ${by} to see the crowd’s odds.`;
        return `<div class="story open w4 answer" data-edition="${esc(q.edition)}">
            <p class="kicker"><label class="kicker-k" for="fcChance">Your chance of Yes</label></p>
            <output class="story-title" for="fcChance">${set ? pct(q.yours) : '—'}</output>
            <input class="range${set ? '' : ' unset'}" id="fcChance" type="range" min="0" max="100" step="5" value="${set ? q.yours : 50}"
                style="--v: ${set ? q.yours : 50}%" aria-valuetext="${set ? pct(q.yours) : 'not set'}"${live ? '' : ' disabled'}>
            <div class="range-scale" aria-hidden="true"><span>No</span><span>50</span><span>Yes</span></div>
            <p class="byline" role="status">${note}</p></div>`;
    }

    /** The days from the issue's to a date's first. */
    const off = (d) => Math.round((Date.parse(d.day) - Date.parse(data.edition.id)) / 864e5);

    /** How far off a date is: the beat it's said on (dates.py), in words. */
    function beat(d) {
        const days = off(d);
        if (days > 1) return { 7: 'In a week', 14: 'In two weeks' }[days] || `In ${days} days`;
        return days === 1 ? 'Tomorrow' : days === 0 ? 'Today' : d.until === data.edition.id ? 'Last day' : 'On now';
    }

    /** Something of a date to watch, at its hour on your clock: its stream, when it has one. */
    function part(p) {
        const state = Date.now() < new Date(p.starts) || !p.ends ? '' : Date.now() < new Date(p.ends) ? 'Live · ' : 'Replay · ';
        const label = `${state}${new Date(p.starts).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' })} ${p.name}`;
        return away(p.link, label) || `<span>${esc(label)}</span>`;
    }

    /** The dates coming (dates.py): a holiday, a climbing World Cup, a UFC card, each said two weeks
     * ahead, a week ahead, the day before and while it's on. From the day before, what there is to
     * watch of it. A section only when there are some. */
    function drawDates() {
        const dates = section('dates') || [];
        const el = $('dates');
        el.hidden = !dates.length;
        const span = (d) => [...new Set([d.day, d.until])].map((iso) => day(iso, { weekday: 'short', day: 'numeric', month: 'short' })).join(' – ');
        el.innerHTML = el.hidden ? '' : `<h2 class="section-head"><span>Dates</span><span class="n">the two weeks ahead</span></h2>
            <div class="rest-list">${dates.map((d) => `<article class="story open s-line"><p class="kicker"><span class="kicker-k">${beat(d)}</span>
                <span>${esc(span(d))}</span><span>${esc(d.kind)}</span>${d.place ? `<span>${esc(d.place)}</span>` : ''}</p>
                <h2 class="story-title">${away(d.link, d.title) || esc(d.title)}</h2>
                ${d.note ? `<p class="byline">${esc(d.note)}</p>` : ''}
                ${d.parts.length && off(d) <= 1 ? `<p class="links">${d.parts.map(part).join('')}</p>` : ''}</article>`).join('')}</div>`;
    }

    /** A picture with its text, set as a story, the whole of it beside the text: under its
     * section's head, a kicker (its parts already safe to put in the page), the title, the text and
     * its links, and under the picture who made it, when that's to be said. `shut`, its text is
     * shut as a paper's is, its title the button that opens it to the whole text and the links;
     * the picture stays whole (dispatch.css). */
    function drawPicture(name, head, p, kicker, title, links, { caption = '', shut = false } = {}) {
        const el = $(name);
        el.hidden = !p;
        if (!p) { el.innerHTML = ''; return; }
        ratios.set(p.id, p.ratio);
        const [first, ...more] = kicker.filter(Boolean);
        el.innerHTML = `<h2 class="section-head"><span>${head}</span><span class="n">${esc(p.source)}</span></h2>
            <div class="row"><article class="story${shut ? '' : ' open'} w12 side has-fig">
                <div class="flow">${figure(p, true, caption)}<div class="story-head">
                    <p class="kicker"><span class="kicker-k">${first}</span>${more.map((m) => `<span>${m}</span>`).join('')}</p>
                    ${heading(title, shut)}
                </div><p class="abstract">${marked(p.text)}</p></div>
                ${links ? `<p class="links">${links}</p>` : ''}
            </article></div>`;
    }

    /** The day's print, whole, with what its museum says of it shut until it's opened, as a paper's text is. */
    function drawPrint() {
        const p = section('print');
        drawPicture('print', 'The day’s print', p, p ? [p.maker, p.date, p.technique].map(esc) : [], p && marked(p.title),
            p && away(p.url, p.source), { shut: true });
    }

    /** The day's bird as the print is, whole, with what Wikipedia says of it shut until it's opened:
     * its Chinese name beside its English, and under its photograph who took it and the licence. */
    function drawBird() {
        const b = section('bird');
        drawPicture('bird', 'The day’s bird', b, b ? [esc(b.status), `<i class="latin">${esc(b.latin)}</i>`] : [],
            b && `${marked(b.title)} <span class="bird-zh" lang="zh-HK">${esc(b.zh)}</span>`, '',
            { caption: b && webLink(b.photo) && `<a href="${esc(b.photo)}" target="_blank" rel="noopener">${b.credit} · ${esc(b.licence)}</a>`, shut: true });
    }

    /** A line of the poem; of a passage, with the words that have a note marked, each a button to it. */
    function line(text, i, notes) {
        const chars = [...text];
        let at = 0;
        const words = notes.map((n, k) => {
            if (n.line !== i) return '';
            const before = chars.slice(at, n.start).join('');
            at = n.end;
            return `${esc(before)}<span class="gloss" role="button" tabindex="0" aria-expanded="false" data-note="${k}">${esc(chars.slice(n.start, n.end).join(''))}</span>`;
        });
        return words.join('') + esc(chars.slice(at).join(''));
    }

    /** The day's poem, to learn by heart: set as a story, line by line, in Hong Kong's characters.
     * Some days it's a passage of 古文觀止 instead, to read with the dictionary's notes on its words. */
    function drawPoem() {
        const p = section('poem');
        const el = $('poem');
        el.hidden = !p;
        // A poem is shown whole, to learn by heart; a passage, long, starts shut as a paper does.
        const passage = !!p?.notes;
        el.innerHTML = p ? `<h2 class="section-head"><span>The day’s ${passage ? 'passage' : 'poem'}</span><span class="n">${esc(p.book)}</span></h2>
            <div class="row"><article class="story${passage ? '' : ' open'} w12" lang="zh-HK">
                <p class="kicker"><span class="kicker-k">${esc(p.author)}</span><span>${esc(p.form)}</span></p>
                ${heading(esc(p.title), passage)}
                <p class="abstract">${p.lines.map((text, i) => line(text, i, p.notes || [])).join('<br>')}</p>
                ${passage ? `<p class="links">${away('https://dict.revised.moe.edu.tw/', '註釋：教育部《重編國語辭典修訂本》')}</p>` : ''}
            </article></div>` : '';
    }

    /** A marked word, pressed, shows its note under its line: what the dictionary says the word
     * means there. The same word again, or a press anywhere else, puts the note away. */
    function gloss(word) {
        const shown = $('poem').querySelector('.gloss[aria-expanded=true]');
        $('poem').querySelector('.gloss-note')?.remove();
        shown?.setAttribute('aria-expanded', false);
        if (!word || word === shown) return;
        word.setAttribute('aria-expanded', true);
        word.insertAdjacentHTML('afterend', `<span class="gloss-note" role="note">${section('poem').notes[word.dataset.note].senses.map((s) => `<span><b>${esc(s.word)}</b>${s.kind ? `<small>${esc(s.kind)}</small>` : ''}${esc(s.def)}</span>`).join('')}</span>`);
    }
    document.addEventListener('click', (e) => { if (!e.target.closest('.gloss-note')) gloss(e.target.closest('.gloss')); });
    $('poem').addEventListener('keydown', (e) => {
        if (e.key === 'Escape') gloss(null);
        if (e.key !== 'Enter' && e.key !== ' ' || !e.target.matches('.gloss')) return;
        e.preventDefault();
        gloss(e.target);
    });

    function drawForecast() {
        const f = data?.forecast;
        const el = $('forecast');
        el.hidden = !f;
        if (!f) { el.innerHTML = ''; return; }
        const q = f.question;
        // No question: being chosen as the issue comes out, none was eligible, or (an issue from
        // before there were questions) none was ever asked, and then there's nothing to say.
        const none = f.pending ? 'Today’s question is being chosen.'
            : f.asked ? 'No question today: nothing big enough closes in the week ahead.' : '';
        const results = f.results.map((r) => `<div class="row"><article class="story open w12">
            <p class="kicker"><span class="kicker-k">Asked ${esc(day(r.edition))}</span><span>${esc(r.subject)}</span></p>
            <h2 class="story-title">${esc(r.title)}</h2><p class="byline">${outcome(r)}</p></article></div>`).join('');
        if (!q && !none && !results) { el.hidden = true; el.innerHTML = ''; return; }
        el.innerHTML = `<h2 class="section-head"><span>The day’s question</span><span class="n">From Polymarket</span></h2>
            ${q ? `<div class="row asking">${asked(q, 'Forecast')}${answer(q)}</div>` : none ? `<p class="press-empty">${none}</p>` : ''}
            ${results}`;
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
        $('viewerTitle').textContent = el.querySelector('.story-title').textContent;
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

    /** A press on a story (a paper, the print, the bird, a passage) opens or shuts it; on an open one's figure, shows the figure. */
    function read(e) {
        if (e.target.closest('.retry')) return load(fromPath());
        if (e.target.closest('a, .gloss, .gloss-note') || window.getSelection()?.toString()) return;
        const el = e.target.closest('.story');
        if (!el) return;
        if (e.target.closest('.fig') && (el.classList.contains('open') || el.classList.contains('s-lead'))) return view(el);
        if (el.querySelector('.story-open')) toggle(el);   // a poem shown whole doesn't shut
    }
    for (const id of ['edition', 'print', 'poem', 'bird']) $(id).addEventListener('click', read);
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

    // The issue is in the address (/dispatch/2026-11-12), so back and forward step through what you
    // read, each where you left it. Leaving for News and coming back soon after does too.
    const fromPath = () => (location.pathname.match(/\/dispatch\/(\d{4}-\d{2}-\d{2})\/?$/) || [])[1] || null;
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
    window.addEventListener('resize', () => {
        clearTimeout(resizing);
        resizing = setTimeout(() => { fit(); Woodcut.draw($('woodcut'), data?.edition?.out); }, 150);
    });
    // Turned wide (a tablet, a window), the desktop's slots need every figure's proportions.
    narrow.addEventListener('change', () => data && !narrow.matches && measure(section('papers') || []).then(() => drawEdition()));
    document.fonts?.ready.then(fit);
    /** Night or day on your clock: at night the figures, drawn on white, are dimmed. */
    const dusk = () => {
        const h = new Date().getHours();
        document.body.classList.toggle('night', h >= NIGHT[0] || h < NIGHT[1]);
    };
    dusk();
    setInterval(() => { drawStatus(); dusk(); }, 60000);
    setInterval(() => !document.hidden && !viewing && load(data?.edition?.id), 30 * 60000);
})();
