/* Engraved plate: the map printed in two inks, as a banknote is.
 *
 * The SVG map stays on top for borders, pins and labels; under it a WebGL
 * canvas engraves the plate from three masks drawn from the same paths:
 *   land     where to cut the land's engraved lines
 *   near     land blurred a little: ripple lines that follow the coasts
 *   far      story countries blurred a lot: where the second ink bleeds
 * Navy is the plate; terracotta only where something is happening.
 * Also: guilloche and rosettes for the page (generated SVG), and a band of
 * microtext made from the day's headlines. */
(() => {
    const svg = document.getElementById('map');
    const theatre = document.querySelector('.theatre');
    if (!svg || !theatre) return;

    // Night printing: cream ink on navy paper (?ink=night, or the system's dark mode).
    const ink = new URLSearchParams(location.search).get('ink');
    const night = ink ? ink === 'night' : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.ink = night ? 'night' : 'day';

    // ---- the plate ------------------------------------------------------------------
    const canvas = document.createElement('canvas');
    canvas.className = 'plate';
    theatre.prepend(canvas);
    const gl = canvas.getContext('webgl2', { premultipliedAlpha: false, antialias: false });
    if (!gl) { document.documentElement.dataset.plate = 'none'; return; }

    const mask = document.createElement('canvas');
    const near = document.createElement('canvas');
    const far = document.createElement('canvas');
    const globe = document.createElement('canvas');
    const gctx = globe.getContext('2d');
    const mctx = mask.getContext('2d', { willReadFrequently: false });
    const nctx = near.getContext('2d');
    const fctx = far.getContext('2d');

    const VERT = `#version 300 es
in vec2 a; void main() { gl_Position = vec4(a, 0.0, 1.0); }`;

    const FRAG = `#version 300 es
precision highp float;
uniform sampler2D uMask, uNear, uFar, uGlobe;
uniform vec2 uRes, uOrigin, uCenter;
uniform float uScale, uDpr, uNight;
out vec4 o;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
}
float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + 7.1; a *= 0.5; } return v; }

// A family of engraved lines along t (one period per unit): ink where the line is,
// w its width as a share of the period. Anti-aliased to the pixel.
float cut(float t, float w) {
    float d = abs(fract(t) - 0.5);
    float aa = fwidth(t) * 0.75;
    return 1.0 - smoothstep(w * 0.5 - aa, w * 0.5 + aa, d);
}
// A hairline: about px css pixels wide whatever the spacing.
float hair(float t, float px) { return cut(t, min(px * uDpr * fwidth(t), 0.9)); }

void main() {
    vec2 p = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y);     // canvas px, top-left origin
    vec2 uv = gl_FragCoord.xy / uRes;
    vec4 m = texture(uMask, uv);
    vec4 n = texture(uNear, uv);
    vec4 f = texture(uFar, uv);
    float globe = texture(uGlobe, uv).r;           // inside the projection's outline
    float land = m.r;
    float flash = m.g, priority = m.b;
    vec2 s = (p - uOrigin) / uDpr;                 // css px, anchored to the map: lines travel with it
    vec2 q = (p - uOrigin) / uScale;               // map units: relief stays put at any zoom

    vec3 paper = mix(vec3(0.945, 0.918, 0.859), vec3(0.055, 0.094, 0.165), uNight);
    vec3 navy  = mix(vec3(0.133, 0.231, 0.408), vec3(0.851, 0.812, 0.725), uNight);
    vec3 terra = mix(vec3(0.745, 0.333, 0.180), vec3(0.910, 0.502, 0.314), uNight);

    // Paper: grain and the faint unevenness of a printed sheet.
    float grain = noise(p * 0.9) * 0.6 + noise(p * 0.23) * 0.4;
    paper *= 1.0 + (grain - 0.5) * mix(0.06, 0.10, uNight);

    // ---- sea: a guilloche net, swept by a slow warp, and ripples along the coasts ----
    vec2 w = s + vec2(26.0 * sin(s.y / 190.0 + 1.3), 18.0 * sin(s.x / 230.0));
    float k = 1.0 / 29.0;
    float g1 = (w.y + 5.5 * sin(w.x * k)) / 6.5;
    float g2 = (w.y - 5.5 * sin(w.x * k + 0.6)) / 6.5;
    float net = max(hair(g1, 0.55), hair(g2, 0.55));
    vec2 d = s - (uCenter - uOrigin) / uDpr;       // and rings from the plate's centre, very faint
    float ring = hair(length(d) / 9.0 * (1.0 + 0.03 * sin(16.0 * atan(d.y, d.x))), 0.5);
    float coast = n.r;                              // 1 on land, falling off over the sea
    float ripple = hair(coast * 9.0, 0.8) * smoothstep(0.015, 0.08, coast) * (1.0 - smoothstep(0.42, 0.55, coast));
    float sea = mix(ring * 0.22, max(net * 0.42 + ring * 0.1, ripple * 0.9), globe);

    // ---- land: engraved lines over invented relief, crosshatched where darkest ----
    float h = fbm(q * 0.12 + 3.0);
    float inner = smoothstep(0.55, 1.0, coast);   // away from the shore
    float dark = 0.22 + 0.36 * smoothstep(0.38, 0.82, h) + 0.22 * (1.0 - inner);
    float hot = max(flash, priority * 0.85);
    dark = mix(dark, dark * 0.85 + 0.12, hot);
    float t1 = (s.y + 2.2 * sin(s.x / 41.0 + h * 5.0)) / 3.4;
    float t2 = (s.x * 0.5 - s.y * 0.866 + 1.5 * sin(s.y / 33.0)) / 3.4;
    float lines = max(cut(t1, clamp(dark, 0.08, 0.92)), cut(t2, clamp((dark - 0.58) * 1.8, 0.0, 0.7)));

    // The second ink: story countries in terracotta, bleeding into the sea's ripples.
    float bleed = smoothstep(0.02, 0.35, max(f.g, f.b * 0.8)) * (1.0 - land);
    vec3 seaInk = mix(navy, terra, bleed);
    vec3 landInk = mix(navy, terra, hot);

    vec3 col = paper;
    col = mix(col, seaInk, sea * (1.0 - land) * (0.55 + 0.45 * bleed));
    col = mix(col, landInk, lines * land * mix(0.92, 0.85, uNight));
    // Ink isn't perfectly even: a little of the grain shows through.
    col = mix(col, paper, (grain - 0.5) * 0.12);
    o = vec4(col, 1.0);
}`;

    function shader(type, src) {
        const sh = gl.createShader(type);
        gl.shaderSource(sh, src);
        gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
        return sh;
    }
    const prog = gl.createProgram();
    gl.attachShader(prog, shader(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'a');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const U = (name) => gl.getUniformLocation(prog, name);

    const textures = ['uMask', 'uNear', 'uFar', 'uGlobe'].map((name, i) => {
        const t = gl.createTexture();
        gl.activeTexture(gl.TEXTURE0 + i);
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.uniform1i(U(name), i);
        return t;
    });

    const paths = new WeakMap();      // path element → its Path2D
    const path2d = (el) => {
        let p = paths.get(el);
        if (!p) { p = new Path2D(el.getAttribute('d') || ''); paths.set(el, p); }
        return p;
    };

    function masks(W, H, ctm, dpr, r) {
        for (const c of [mask, near, far, globe]) { if (c.width !== W || c.height !== H) { c.width = W; c.height = H; } }
        mctx.setTransform(1, 0, 0, 1, 0, 0);
        mctx.clearRect(0, 0, W, H);
        mctx.fillStyle = '#000';
        mctx.fillRect(0, 0, W, H);
        mctx.setTransform(ctm.a * dpr, ctm.b * dpr, ctm.c * dpr, ctm.d * dpr, (ctm.e - r.left) * dpr, (ctm.f - r.top) * dpr);
        const shapes = svg.querySelectorAll('#land .land:not(.covered), #detail .detail-land, #detail .region.hot-flash, #detail .region.hot-priority');
        for (const el of shapes) {
            const cls = el.classList;
            mctx.fillStyle = cls.contains('hot-flash') ? '#ff0' : cls.contains('hot-priority') ? '#f0f'
                : cls.contains('region') ? 'rgba(0,0,0,0)' : '#f00';
            mctx.fill(path2d(el));
        }
        gctx.setTransform(1, 0, 0, 1, 0, 0);
        gctx.fillStyle = '#000';
        gctx.fillRect(0, 0, W, H);
        gctx.setTransform(mctx.getTransform());
        gctx.fillStyle = '#f00';
        const sphere = svg.querySelector('.sphere');
        if (sphere) gctx.fill(path2d(sphere));
        const blur = (ctx, px) => {
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.filter = 'none';
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, W, H);
            ctx.filter = `blur(${px * dpr}px)`;
            ctx.drawImage(mask, 0, 0);
            ctx.filter = 'none';
        };
        blur(nctx, 14);
        blur(fctx, 34);
    }

    let pending = false;
    function draw() {
        pending = false;
        const r = theatre.getBoundingClientRect();
        const ctm = svg.getScreenCTM();
        if (!ctm || !r.width) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const W = Math.round(r.width * dpr), H = Math.round(r.height * dpr);
        if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
        masks(W, H, ctm, dpr, r);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        [mask, near, far, globe].forEach((c, i) => {
            gl.activeTexture(gl.TEXTURE0 + i);
            gl.bindTexture(gl.TEXTURE_2D, textures[i]);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, c);
        });
        const sphere = svg.querySelector('.sphere');
        const b = sphere ? sphere.getBBox() : { x: 0, y: 0, width: 0, height: 0 };
        const ox = (ctm.e - r.left) * dpr, oy = (ctm.f - r.top) * dpr, sc = ctm.a * dpr;
        gl.viewport(0, 0, W, H);
        gl.uniform2f(U('uRes'), W, H);
        gl.uniform2f(U('uOrigin'), ox, oy);
        gl.uniform2f(U('uCenter'), ox + sc * (b.x + b.width / 2), oy + sc * (b.y + b.height / 2));
        gl.uniform1f(U('uScale'), sc);
        gl.uniform1f(U('uDpr'), dpr);
        gl.uniform1f(U('uNight'), night ? 1 : 0);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(draw); } };
    new MutationObserver(schedule).observe(svg, { attributes: true, attributeFilter: ['viewBox', 'class'], childList: true, subtree: true });
    new ResizeObserver(schedule).observe(theatre);
    schedule();

    // ---- guilloche and rosettes for the page ----------------------------------------
    const css = getComputedStyle(document.documentElement);
    const inkVar = (v) => css.getPropertyValue(v).trim();
    const svgUrl = (body, w, h) => `url("data:image/svg+xml,${encodeURIComponent(
        `<svg xmlns='http://www.w3.org/2000/svg' width='${w}' height='${h}' viewBox='0 0 ${w} ${h}'>${body}</svg>`)}")`;

    /** A band of crossing sine lines, the net printed behind a banknote's lettering. */
    function band(w, h, color, lines = 9, waves = 6, opacity = 0.55) {
        let d = '';
        for (let fam = 0; fam < 2; fam++) {
            for (let i = 0; i < lines; i++) {
                const y0 = (h * (i + 0.5)) / lines, amp = h * 0.42, ph = (i / lines) * Math.PI * 2 * 0.35 + fam * Math.PI;
                let pts = [];
                for (let x = 0; x <= w; x += 2) {
                    const y = y0 + amp * Math.sin((x / w) * Math.PI * 2 * waves + ph) * Math.sin((i + 1) / lines * Math.PI);
                    pts.push(`${x.toFixed(1)},${Math.min(h, Math.max(0, y)).toFixed(1)}`);
                }
                d += `M${pts.join('L')}`;
            }
        }
        return svgUrl(`<path d='${d}' fill='none' stroke='${color}' stroke-width='0.45' opacity='${opacity}'/>`, w, h);
    }

    /** A spirograph rosette (hypotrochoid), the seal at a banknote's corner. */
    function rosette(size, color, R = 9, r = 4, dd = 6.5, turns = 4) {
        const c = size / 2, scale = (size * 0.47) / (R - r + dd);
        let pts = [];
        for (let t = 0; t <= Math.PI * 2 * turns + 0.01; t += 0.03) {
            const x = (R - r) * Math.cos(t) + dd * Math.cos(((R - r) / r) * t);
            const y = (R - r) * Math.sin(t) - dd * Math.sin(((R - r) / r) * t);
            pts.push(`${(c + x * scale).toFixed(2)},${(c + y * scale).toFixed(2)}`);
        }
        return svgUrl(`<circle cx='${c}' cy='${c}' r='${size * 0.49}' fill='none' stroke='${color}' stroke-width='0.6'/>`
            + `<path d='M${pts.join('L')}' fill='none' stroke='${color}' stroke-width='0.5'/>`, size, size);
    }

    const root = document.documentElement.style;
    root.setProperty('--band', band(240, 22, inkVar('--ink'), 8, 5));
    root.setProperty('--band-terra', band(240, 22, inkVar('--terra'), 8, 5, 0.75));
    root.setProperty('--net', band(180, 60, inkVar('--ink'), 14, 3, 0.11));
    root.setProperty('--rosette', rosette(64, inkVar('--terra')));
    root.setProperty('--rosette-ink', rosette(64, inkVar('--ink'), 11, 3, 5.2, 3));

    // ---- microtext: the day's headlines, too small to read without leaning in --------
    const micro = document.createElement('div');
    micro.className = 'microtext';
    micro.setAttribute('aria-hidden', 'true');
    theatre.append(micro);
    const fill = () => {
        const heads = [...document.querySelectorAll('#feed .headline')].map((h) => h.textContent.trim().toUpperCase());
        if (heads.length) micro.textContent = (heads.join('  ✦  ') + '  ✦  ').repeat(3);
    };
    new MutationObserver(fill).observe(document.getElementById('feed'), { childList: true, subtree: true });
    fill();
})();
