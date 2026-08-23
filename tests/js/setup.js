// Global browser environment for the buildless CDN SPA (static/app.js).
//
// index.html loads Vue/VueRouter/d3/CodeMirror from CDNs as globals; app.js
// consumes them via destructuring at module evaluation time. This setup file
// recreates exactly that contract inside jsdom before any test imports app.js.
import * as Vue from 'vue';
import * as VueRouter from 'vue-router';

globalThis.Vue = Vue;
globalThis.VueRouter = VueRouter;

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
// (citation network / LaTeX editor are out of smoke scope).
globalThis.d3 = {};
globalThis.CodeMirror = {
    fromTextArea: () => ({
        on: () => {},
        getValue: () => '',
        setValue: () => {},
        toTextArea: () => {},
    }),
};
