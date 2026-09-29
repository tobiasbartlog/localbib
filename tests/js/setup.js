// Global browser environment for the buildless CDN SPA (static/app.js).
//
// index.html loads Vue/VueRouter/d3/CodeMirror from CDNs as globals; app.js
// consumes them via destructuring at module evaluation time. This setup file
// recreates exactly that contract inside jsdom before any test imports app.js.
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import * as Vue from 'vue';
import * as VueRouter from 'vue-router';
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
// is sanitised asserts the sanitiser that actually ships.
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

// jsdom's alert/confirm raise "not implemented" — default to silent OK;
// individual tests override with vi.fn() to assert on the dialog.
window.alert = () => {};
window.confirm = () => true;

// Benign stub for CDN libraries the smoke-tested views never exercise
// (citation network is out of smoke scope).
globalThis.d3 = {};

// CodeMirror 5: callable (the manuscript editor mounts into a host element)
// and with .fromTextArea (the notes editor upgrades a textarea). The stub
// keeps the textarea in the DOM, which is what the notes smoke test looks at.
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
