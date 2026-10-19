/* Signed out while the page was open (home stack only).
 *
 * The dashboard's nginx answers a request from an expired session with a 401
 * and `X-Signed-Out: 1` (a page load would go to the sign-in page instead).
 * The request fails as usual, so whatever was being saved stays on the page;
 * this adds one bar offering to sign in again in a new tab, after which the
 * save can simply be retried. The bar goes once a request succeeds again.
 *
 * Shared byte-identical across library, calendar and tapes.
 */
(function () {
    let bar = null;

    function show() {
        if (bar) return;
        bar = document.createElement('div');
        bar.className = 'signed-out-bar';
        bar.setAttribute('role', 'alert');
        const next = encodeURIComponent(location.pathname + location.search);
        bar.innerHTML = '<span>You’ve been signed out. Nothing on this page is lost.</span>'
            + `<a href="/login?next=${next}" target="_blank" rel="noopener">Sign in again</a>`
            + '<span class="signed-out-hint">then try again here.</span>';
        const style = document.createElement('style');
        style.textContent = `
.signed-out-bar { position: fixed; z-index: 100000; left: 16px; right: 16px; margin: 0 auto;
  top: calc(12px + env(safe-area-inset-top, 0px)); width: fit-content; max-width: 640px;
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; padding: 10px 16px;
  border-radius: 10px; font: inherit; font-size: 14px; line-height: 1.4;
  background: var(--color-bg-elevated, #222); color: var(--color-text-primary, #eee);
  border: 1px solid var(--color-border, rgba(127,127,127,.35)); box-shadow: 0 6px 24px rgba(0,0,0,.25); }
.signed-out-bar a { color: var(--color-accent, inherit); font-weight: 600; text-decoration: underline; }
.signed-out-hint { color: var(--color-text-muted, inherit); }`;
        bar.appendChild(style);
        document.body.appendChild(bar);
    }

    function hide() {
        if (bar) { bar.remove(); bar = null; }
    }

    function check(status, signedOut) {
        if (status === 401 && signedOut) show();
        else if (status >= 200 && status < 300) hide();
    }

    const fetch0 = window.fetch;
    if (fetch0) {
        window.fetch = function () {
            return fetch0.apply(this, arguments).then((r) => {
                check(r.status, r.headers.get('X-Signed-Out'));
                return r;
            });
        };
    }

    const send0 = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function () {
        this.addEventListener('loadend', () => {
            check(this.status, this.status === 401 && this.getResponseHeader('X-Signed-Out'));
        });
        return send0.apply(this, arguments);
    };
})();
