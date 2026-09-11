// Global browser environment for the buildless CDN SPA (static/app.js).
//
// index.html loads Vue/VueRouter/d3/CodeMirror from CDNs as globals; app.js
// consumes them via destructuring at module evaluation time. This setup file
// recreates exactly that contract inside jsdom before any test imports app.js.
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import * as Vue from 'vue';
import * as VueRouter from 'vue-router';

globalThis.Vue = Vue;
globalThis.VueRouter = VueRouter;

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

// jsdom's alert/confirm raise "not implemented" — default to silent OK;
// individual tests override with vi.fn() to assert on the dialog.
window.alert = () => {};
window.confirm = () => true;

// Benign stubs for CDN libraries the smoke-tested views never exercise
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
