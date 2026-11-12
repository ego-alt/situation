/* The woodcut at the head of each issue: the mouth of the Pearl River engraved in light lines,
 * the land hatched and dotted with peaks, the sea lined along the shore, and laid faintly over
 * the sea the sky above Hong Kong at 9pm on the day the issue came out: its brightest stars where
 * they stood, the river of stars, and the moon at its phase. Worked out here from the date, so an
 * issue's woodcut is the same every time it's opened; the coast is the map's own (static/map). */
(() => {
    const ROOT = window.APP_ROOT || '';
    const LAT = 22.3, LON = 114.17;          // Hong Kong
    const EVENING = 13;                      // 9pm in Hong Kong, in UTC hours
    // Each shape: its size, how far in its frame's sides stand (off the picture on a phone: a banner
    // from edge to edge, ruled above and below), the part of the map it shows (the map's own units),
    // where the sky, the moon, the title and the compass go.
    const SHAPES = {
        wide: { w: 1216, h: 250, side: 10, view: [806.0, 204.4, 24.0, 4.95], sky: [0.72, 0.6, 0.2, 0.5], moon: [-150, 64], title: [28, 26, 1], compass: [-70, -62] },
        narrow: { w: 390, h: 180, side: -8, view: [810.0, 205.0, 14.4, 5.7], sky: [0.48, 0.82, 0.32, 0.5], moon: [-44, 58], title: [12, 18, 0.8], compass: [-44, -44] },
    };
    const PLACES = [['香港', 817.15, 207.35, 14, 30], ['澳門', 815.75, 207.55, -34, 30], ['廣州', 815.6, 205.6, 10, 4]];
    // The brightest stars of a November evening: RA and Dec (degrees), magnitude, and the two named.
    const STARS = [
        [279.23, 38.78, 0.0, '織女'], [297.70, 8.87, 0.8, '牛郎'], [310.36, 45.28, 1.3], [344.41, -29.62, 1.2], [79.17, 46.00, 0.1],
        [68.98, 16.51, 0.9], [56.75, 24.12, 1.6], [37.95, 89.26, 2.0], [88.79, 7.41, 0.5], [78.63, -8.20, 0.1], [31.79, 23.46, 2.0],
        [2.10, 29.09, 2.1], [17.43, 35.62, 2.1], [10.90, 56.54, 2.2], [14.18, 60.72, 2.5], [2.29, 59.15, 2.3], [346.19, 15.21, 2.5],
        [345.94, 28.08, 2.4], [326.05, 9.88, 2.4], [305.56, 40.26, 2.2], [311.55, 33.97, 2.5], [296.56, 10.61, 2.7], [284.74, 32.69, 3.2],
        [283.82, 33.36, 3.5], [101.29, -16.72, -1.5], [114.83, 5.22, 0.4], [116.33, 28.03, 1.1], [152.09, 11.97, 1.4],
    ];
    const LINES = [[1, 21], [0, 22], [22, 23], [13, 14], [13, 15], [12, 11], [11, 10]];   // Altair's and Vega's neighbours, Cassiopeia, Andromeda
    const PHASES = ['新月', '蛾眉月', '上弦月', '盈凸月', '滿月', '虧凸月', '下弦月', '殘月'];
    const MONTH = 29.530588;                 // days, new moon to new moon
    const NEW_MOON = Date.UTC(2000, 0, 6, 18, 14);

    const rad = (d) => d * Math.PI / 180, deg = (r) => r * 180 / Math.PI;
    const el = (tag, attrs = {}, kids = '') => `<${tag} ${Object.entries(attrs).map(([k, v]) => `${k}="${v}"`).join(' ')}>${kids}</${tag}>`;
    const random = (seed) => () => (seed = (seed * 16807) % 2147483647) / 2147483647;

    /** Where a star stands from Hong Kong at `t`: altitude and azimuth, in degrees. */
    function altaz(ra, dec, t) {
        const days = (t - Date.UTC(2000, 0, 1, 12)) / 864e5;
        const ha = rad((280.46061837 + 360.98564736629 * days + LON - ra) % 360);
        const [d, lat] = [rad(dec), rad(LAT)];
        const alt = Math.asin(Math.sin(d) * Math.sin(lat) + Math.cos(d) * Math.cos(lat) * Math.cos(ha));
        const az = Math.atan2(-Math.sin(ha) * Math.cos(d), Math.cos(lat) * Math.sin(d) - Math.sin(lat) * Math.cos(d) * Math.cos(ha));
        return [deg(alt), (deg(az) + 360) % 360];
    }

    /** A point on the river of stars, the galactic equator, at longitude `l`: RA and Dec. */
    function galactic(l) {
        const [ra0, dec0, l0] = [rad(192.85948), rad(27.12825), rad(122.93192)];
        const dec = Math.asin(Math.cos(dec0) * Math.cos(l0 - rad(l)));
        const ra = ra0 + Math.atan2(Math.sin(l0 - rad(l)), -Math.sin(dec0) * Math.cos(l0 - rad(l)));
        return [(deg(ra) + 360) % 360, deg(dec)];
    }

    /** The moon on day `t`: how many days into its month, how much of it is lit, and its name. */
    function moonAt(t) {
        const age = (((t - NEW_MOON) / 864e5) % MONTH + MONTH) % MONTH;
        return { age, lit: (1 - Math.cos(2 * Math.PI * age / MONTH)) / 2, name: PHASES[Math.round(age / MONTH * 8) % 8] };
    }

    /** A woodcut star: four sharp points. */
    function star(x, y, s) {
        const p = [[0, -1.9], [0.35, -0.35], [1.9, 0], [0.35, 0.35], [0, 1.9], [-0.35, 0.35], [-1.9, 0], [-0.35, -0.35]];
        return el('path', { class: 'ink', d: `M${p.map(([a, b]) => `${(x + a * s).toFixed(1)},${(y + b * s).toFixed(1)}`).join('L')}Z` });
    }

    /** The moon at its phase: the lit part plain, the dark side hatched; waxing, it's lit on the right. */
    function moon(r, m, id) {
        const k = 1 - 2 * m.lit;                              // the terminator: 1 new, 0 half, -1 full
        const s = m.age < MONTH / 2 ? 1 : 0;
        const lit = `M0,${-r} A${r},${r} 0 0 ${s} 0,${r} A${(Math.abs(k) * r).toFixed(2)},${r} 0 0 ${k < 0 ? s : 1 - s} 0,${-r}Z`;
        return el('circle', { r, fill: `url(#moon-${id})`, class: 'line', 'stroke-width': 0.8 }) + el('path', { d: lit, class: 'lit' });
    }

    /** Tonight's sky laid over the sea as a faint planisphere centred at (cx, cy): north up, east left. */
    function sky(t, cx, cy, rx, ry, rnd) {
        const put = (ra, dec) => {
            const [alt, az] = altaz(ra, dec, t);
            if (alt < 4) return null;
            const d = (90 - alt) / 90;
            return [cx - rx * d * Math.sin(rad(az)), cy - ry * d * Math.cos(rad(az))];
        };
        const gauss = () => (rnd() + rnd() + rnd() - 1.5) * 1.4;
        const out = [];
        for (let l = 0; l < 360; l += 2) {
            const q = put(...galactic(l));
            for (let i = 0; q && i < 5; i++) {
                out.push(el('circle', { cx: (q[0] + gauss() * 7).toFixed(1), cy: (q[1] + gauss() * 5).toFixed(1), r: (0.35 + rnd() * 0.45).toFixed(2), class: 'ink faint' }));
            }
        }
        const at = STARS.map(([ra, dec]) => put(ra, dec));
        for (const [a, b] of LINES) {
            if (at[a] && at[b]) out.push(el('line', { x1: at[a][0].toFixed(1), y1: at[a][1].toFixed(1), x2: at[b][0].toFixed(1), y2: at[b][1].toFixed(1), class: 'line', 'stroke-width': 0.5, 'stroke-dasharray': '1.5 2' }));
        }
        STARS.forEach(([, , mag, name], i) => {
            if (!at[i]) return;
            out.push(star(at[i][0], at[i][1], Math.max(0.9, 2.6 - mag * 0.7)));
            if (name) out.push(el('text', { x: (at[i][0] + 6).toFixed(1), y: (at[i][1] + 3.5).toFixed(1), class: 'zh ink' }, name));
        });
        return el('g', { class: 'sky' }, out.join(''));
    }

    /** The title box, in the corner. */
    function title(x, y, scale) {
        return el('g', { transform: `translate(${x},${y}) scale(${scale})` },
            el('rect', { width: 150, height: 62, class: 'paper' }) + el('rect', { width: 150, height: 62, fill: 'none', class: 'line', 'stroke-width': 1 })
            + el('rect', { x: 3, y: 3, width: 144, height: 56, fill: 'none', class: 'line', 'stroke-width': 0.5 })
            + el('text', { x: 75, y: 30, class: 'zh ink title', 'text-anchor': 'middle' }, '珠江口')
            + el('text', { x: 75, y: 48, class: 'mono ink', 'text-anchor': 'middle' }, 'Pearl River mouth'));
    }

    /** An eight-pointed compass rose, each point half inked. */
    function compass(cx, cy) {
        let points = '';
        for (let i = 0; i < 8; i++) {
            const a = rad(i * 45), l = i % 2 ? 14 : 26;
            const tip = `${(l * Math.sin(a)).toFixed(1)},${(-l * Math.cos(a)).toFixed(1)}`;
            const side = (o) => `${(l * Math.sin(a + o)).toFixed(1)},${(-l * Math.cos(a + o)).toFixed(1)}`;
            points += el('path', { d: `M0,0 L${side(-0.18)} L${tip}Z`, class: 'ink' }) + el('path', { d: `M0,0 L${side(0.18)} L${tip}Z`, class: 'paper line', 'stroke-width': 0.6 });
        }
        return el('g', { transform: `translate(${cx},${cy})` }, el('circle', { r: 30, class: 'paper line', 'stroke-width': 0.6 }) + points
            + el('text', { y: -34, class: 'zh ink', 'text-anchor': 'middle' }, '北'));
    }

    /** Hong Kong, Macau and Guangzhou: a hollow dot, and the name out on the water. */
    function places(k, tx, ty) {
        return PLACES.map(([name, x, y, dx, dy]) => {
            const [px, py] = [x * k + tx, y * k + ty];
            const leader = dy > 4 ? el('line', { x1: px.toFixed(1), y1: (py + 4).toFixed(1), x2: (px + dx * 0.4).toFixed(1), y2: (py + dy - 12).toFixed(1), class: 'line', 'stroke-width': 0.6 })
                + el('rect', { x: (px + dx - 3).toFixed(1), y: (py + dy - 12).toFixed(1), width: 31, height: 16, class: 'paper' }) : '';
            return el('circle', { cx: px.toFixed(1), cy: py.toFixed(1), r: 3.2, class: 'paper line', 'stroke-width': 1.2 }) + leader
                + el('text', { x: (px + dx).toFixed(1), y: (py + dy).toFixed(1), class: 'zh ink place' }, name);
        }).join('');
    }

    /** Little woodcut peaks, scattered over the land clear of the coast, asking the land's own paths. */
    function peaks(svg, shape, k, tx, ty, rnd) {
        const land = [...svg.querySelectorAll('.coast path')];
        const point = svg.createSVGPoint();
        const isLand = (x, y) => { point.x = (x - tx) / k; point.y = (y - ty) / k; return land.some((p) => p.isPointInFill(point)); };
        const { w, h } = shape;
        const found = [];
        for (let i = 0; i < 3000 && found.length < 60; i++) {
            const x = w * 0.14 + rnd() * w * 0.78, y = 26 + rnd() * (h - 60);
            if (![[0, 0], [14, 0], [-14, 0], [0, 12], [0, -14]].every(([dx, dy]) => isLand(x + dx, y + dy))) continue;
            if (found.some(([a, b]) => Math.hypot(a - x, b - y) < 30)) continue;
            found.push([x, y]);
        }
        return found.map(([x, y]) => {
            const s = 6 + rnd() * 6;
            const hill = `M${-s},0 Q${-s * 0.4},${-s * 0.5} 0,${-s * 1.15} Q${s * 0.4},${-s * 0.5} ${s},0`;
            const shade = [0.3, 0.5, 0.7].map((f) => el('line', { x1: (s * f * 0.9).toFixed(1), y1: (-s * (1 - f)).toFixed(1), x2: (s * f * 0.5).toFixed(1), y2: 0, class: 'line', 'stroke-width': 0.7 })).join('');
            return el('g', { transform: `translate(${x.toFixed(1)},${y.toFixed(1)})` }, el('path', { d: `${hill}Z`, class: 'paper' }) + el('path', { d: hill, fill: 'none', class: 'line', 'stroke-width': 1 }) + shade);
        }).join('');
    }

    let coast = null;
    const read = () => coast || (coast = Promise.all(['CN', 'HK', 'MO'].map((iso) => fetch(`${ROOT}/static/map/regions/${iso}.json`).then((r) => r.json())))
        .then((all) => all.map((r) => r.outline)).catch(() => { coast = null; return null; }));

    /** Draw the woodcut of the issue out on `out` (an ISO date) into `box`: wide, or a shorter banner where
     * the box is narrow (a phone). Drawn again only when the issue or the shape changes. */
    async function draw(box, out) {
        box.hidden = !out;
        if (!out) return;
        const narrow = box.clientWidth < 600;
        const drawn = `${out} ${narrow}`;
        if (box.dataset.drawn === drawn) return;
        const outlines = await read();
        if (!outlines) { box.hidden = true; return; }
        const shape = SHAPES[narrow ? 'narrow' : 'wide'];
        const { w, h, side } = shape, [x0, y0, vw, vh] = shape.view;
        const id = narrow ? 'n' : 'w';
        const [iw, ih] = [w - 2 * side, h - 20];
        const k = Math.max(iw / vw, ih / vh);                   // fill the frame, cropping the map's longer side
        const [tx, ty] = [side + (iw - vw * k) / 2 - x0 * k, 10 + (ih - vh * k) / 2 - y0 * k];
        const at = `matrix(${k.toFixed(4)},0,0,${k.toFixed(4)},${tx.toFixed(2)},${ty.toFixed(2)})`;
        const paths = (extra = '') => outlines.map((d) => `<path d="${d}" vector-effect="non-scaling-stroke"${extra}/>`).join('');
        // The sea lined along the shore: rings of the coast, each an inked line edged in the sea's colour.
        const rings = [50, 40, 31, 23, 16, 10, 5].map((width, i) => el('g', { transform: at, fill: 'none', 'stroke-linejoin': 'round' },
            el('g', { class: 'line', 'stroke-width': width, opacity: (0.18 + i * 0.12).toFixed(2) }, paths()) + el('g', { class: 'sea-line', 'stroke-width': width - 1.1 }, paths()))).join('');
        const t = new Date(out);
        const evening = Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate(), EVENING);
        const m = moonAt(evening);
        const rnd = random(t.getUTCFullYear() * 400 + t.getUTCMonth() * 31 + t.getUTCDate());   // the same woodcut for the same issue
        const [sx, sy, srx, sry] = shape.sky;
        box.dataset.drawn = drawn;
        box.innerHTML = el('svg', { viewBox: `0 0 ${w} ${h}`, role: 'img', 'aria-label': `The mouth of the Pearl River, engraved, with the sky over Hong Kong that evening: ${m.name}` },
            el('defs', {}, el('clipPath', { id: `frame-${id}` }, el('rect', { x: side, y: 10, width: iw, height: ih }))
                + el('pattern', { id: `hatch-${id}`, patternUnits: 'userSpaceOnUse', width: 3.2, height: 3.2, patternTransform: 'rotate(-38)' }, el('line', { x1: 0, y1: 0, x2: 0, y2: 3.2, class: 'line', 'stroke-width': 0.6 }))
                + el('pattern', { id: `moon-${id}`, patternUnits: 'userSpaceOnUse', width: 2.2, height: 2.2, patternTransform: 'rotate(30)' }, el('line', { x1: 0, y1: 0, x2: 0, y2: 2.2, class: 'line', 'stroke-width': 0.7 }))
                + el('mask', { id: `land-${id}`, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: w, height: h }, el('g', { transform: at, fill: '#fff', class: 'coast' }, paths()))
                + el('mask', { id: `sea-${id}`, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: w, height: h }, el('rect', { width: w, height: h, fill: '#fff' }) + el('g', { transform: at, fill: '#000' }, paths())))
            + el('rect', { width: w, height: h, class: 'paper' })
            + el('g', { 'clip-path': `url(#frame-${id})` },
                el('g', { mask: `url(#sea-${id})` }, rings)
                + el('rect', { width: w, height: h, fill: `url(#hatch-${id})`, mask: `url(#land-${id})`, class: 'hatch' })
                + el('g', { transform: at, fill: 'none', class: 'line' }, paths(' stroke-width="1.3"'))
                + el('g', { class: 'peaks' })
                + el('g', { mask: `url(#sea-${id})` }, sky(evening, w * sx, h * sy, w * srx, h * sry, rnd)))
            + el('g', { transform: `translate(${(shape.moon[0] + w) % w},${shape.moon[1]})` }, moon(11, m, id) + el('text', { y: 26, class: 'zh ink', 'text-anchor': 'middle' }, m.name))
            + el('rect', { x: side, y: 10, width: iw, height: ih, fill: 'none', class: 'line', 'stroke-width': 1.4 })
            + el('rect', { x: side - 6, y: 4, width: iw + 12, height: h - 8, fill: 'none', class: 'line', 'stroke-width': 0.6 })
            + title(...shape.title) + compass((shape.compass[0] + w) % w, (shape.compass[1] + h) % h) + places(k, tx, ty));
        const svg = box.querySelector('svg');
        svg.querySelector('.peaks').innerHTML = peaks(svg, shape, k, tx, ty, rnd);
    }

    window.Woodcut = { draw };
})();
