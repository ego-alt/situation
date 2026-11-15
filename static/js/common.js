/* What every page's script uses (it runs first): the app's own addresses, under the stack's
 * prefix (its tag's data-root), an element by its id, text made safe to put in a page, a link
 * only if it's to the web, and whether the page is laid out for a phone (the stylesheets' 860px). */
window.App = (() => {
    const root = document.currentScript.dataset.root;
    return {
        url: (p) => `${root}${p}`,
        $: (id) => document.getElementById(id),
        esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
        webLink: (u) => (/^https?:\/\//i.test(String(u || '')) ? u : null),
        narrow: window.matchMedia('(max-width: 860px)'),
    };
})();
