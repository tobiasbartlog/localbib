// Global browser environment for the buildless CDN SPA (static/app.js).
//
// index.html loads Vue/VueRouter/d3/CodeMirror from CDNs as globals; app.js
// consumes them via destructuring at module evaluation time. This setup file
// recreates exactly that contract inside jsdom before any test imports app.js.
import DOMPurify from 'dompurify';
import * as marked from 'marked';
import * as Vue from 'vue';
import * as VueRouter from 'vue-router';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { TextDecoder as NodeTextDecoder, TextEncoder as NodeTextEncoder } from 'node:util';

globalThis.Vue = Vue;
globalThis.VueRouter = VueRouter;

// i18n (ADR-0018): index.html laedt die Kataloge als <script>-Globals vor
// app.js. Ohne sie zeigte jeder Text nur seinen Schluesselpfad, und jeder
// Test liefe gegen eine leere Oberflaeche. `window.LB_LANG` bleibt ungesetzt:
// die Tests laufen in der Quellsprache Englisch - dem, was Kaeufer sehen.
import '../../static/locales/core.en.js';
import '../../static/locales/core.de.js';

// Markdown rendering: the real libraries, pinned in package.json to the exact
// versions index.html loads from the CDN, so a test asserting that stored text
// is sanitised asserts the sanitiser that actually ships. `marked` is the
// module namespace, as the CDN's UMD build exposes it (`marked.Marked` too).
globalThis.marked = marked;
globalThis.DOMPurify = DOMPurify;

// SSE: a plugin page may open an events endpoint on mount; jsdom has no
// EventSource. The fake records instances so tests could push events.
class FakeEventSource {
    constructor(url) {
        this.url = url;
        this.onmessage = null;
        this.onerror = null;
        FakeEventSource.instances.push(this);
    }

    close() {}
}
FakeEventSource.instances = [];
globalThis.EventSource = FakeEventSource;

// The streaming endpoints decode their chunks and the wizard cancels its run
// through an AbortController. Node brings both; jsdom's window does not always
// carry them over, and app.js reads them off the global.
if (typeof globalThis.TextDecoder === 'undefined') {
    globalThis.TextDecoder = NodeTextDecoder;
}
if (typeof globalThis.TextEncoder === 'undefined') {
    globalThis.TextEncoder = NodeTextEncoder;
}
if (typeof globalThis.AbortController === 'undefined') {
    globalThis.AbortController = class {
        constructor() { this.signal = { aborted: false }; }
        abort() { this.signal.aborted = true; }
    };
}

// Add-on scripts (#187): the SPA injects a <script src> for each active
// Add-on, but jsdom runs no external scripts. app.js routes every script load
// through this one seam; here it fetches the text through the test's fetch
// mock and evaluates it in global scope, so the real fixture script runs.
window.LB_SCRIPT_LOADER = async (url) => {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`could not load ${url} (HTTP ${resp.status})`);
    const source = await resp.text();
    new Function(source)();
};

// PDF preview (#229): jsdom cannot run PDF.js (no canvas, no worker, no
// import() of a URL). The SPA reaches PDF.js through one adapter only, so this
// fake stands in for it - `open(url, { page })`, `goTo(page, rect)`, `zoom()`
// and the page-change message - and records what the SPA asked for in
// `window.LB_PDF_VIEWER.log`. Pretends every PDF has twelve pages.
const pdfViewerLog = { opened: [], jumps: [], zooms: [] };
window.LB_PDF_VIEWER = (host, handlers = {}) => {
    const say = (page) => handlers.onPageChange && handlers.onPageChange({ page, pages: 12 });
    return {
        async open(url, opts = {}) {
            pdfViewerLog.opened.push({ url, page: opts.page || 1 });
            say(opts.page || 1);
            return { pages: 12 };
        },
        goTo(page, rect) {
            pdfViewerLog.jumps.push({ page, rect });
            say(page);
        },
        zoom(step) { pdfViewerLog.zooms.push(step); },
        destroy() {},
    };
};
window.LB_PDF_VIEWER.log = pdfViewerLog;

// Markdown editor (ADR-0026): the SPA reaches Milkdown through one seam,
// `window.LB_MD_EDITOR` (else a lazy import of static/markdown/editor.mjs).
// The smokes get a textarea that keeps the real editor's contract: onChange
// only after typing, setValue is not an edit, getValue hands out the text.
// `window.LB_MD_EDITOR.log` records what the SPA created. The real editor
// is tested on its own (markdown_roundtrip.test.js).
const mdEditorLog = { created: [] };
window.LB_MD_EDITOR = {
    async createMarkdownEditor(root, opts = {}) {
        const ta = document.createElement('textarea');
        ta.className = 'lb-md-fake';
        ta.value = opts.value || '';
        ta.readOnly = !!opts.readonly;
        ta.placeholder = opts.placeholder || '';
        ta.addEventListener('input', () => opts.onChange && opts.onChange(ta.value));
        root.append(ta);
        mdEditorLog.created.push({ value: ta.value, readonly: !!opts.readonly, resolvers: opts.resolvers || {} });
        return {
            getValue: () => ta.value,
            setValue: (v) => { ta.value = v || ''; },
            setReadonly: (on) => { ta.readOnly = !!on; },
            focus: () => ta.focus(),
            destroy: () => ta.remove(),
        };
    },
};
window.LB_MD_EDITOR.log = mdEditorLog;

// jsdom's alert/confirm raise "not implemented" — default to silent OK;
// individual tests override with vi.fn() to assert on the dialog.
window.alert = () => {};
window.confirm = () => true;

// d3 v7: the real library, pinned in package.json to the exact version the CDN
// URL in index.html serves (d3.v7.min.js = 7.9.0), so the knowledge network
// is drawn in the smokes and its nodes can be asserted (#241). It is the
// package's own UMD bundle - the file that CDN URL delivers - evaluated the
// way the page's <script> runs it, so it installs `globalThis.d3`; one file
// instead of thirty ES modules keeps every smoke's setup quick.
new Function(readFileSync(resolve(__dirname, '../../node_modules/d3/dist/d3.min.js'), 'utf8'))();

// CodeMirror 5: callable (an Add-on's manuscript editor mounts into a host
// element) and with .fromTextArea. The stub keeps a textarea in the DOM.
function fakeEditor(textarea) {
    return {
        on: () => {},
        focus: () => textarea && textarea.focus(),
        refresh: () => {},
        getValue: () => (textarea ? textarea.value : ''),
        setValue: (v) => { if (textarea) textarea.value = v; },
        getSelection: () => '',
        replaceSelection: () => {},
        toTextArea: () => {},
    };
}
globalThis.CodeMirror = (host, options) => fakeEditor(null);
globalThis.CodeMirror.fromTextArea = (textarea) => fakeEditor(textarea);
