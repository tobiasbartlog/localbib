// Marketplace install: the download's byte progress (SSE `progress` events,
// #191) actually reaches the progress bar and its text, not just the final
// `complete` event other marketplace tests exercise. The install endpoint is
// mocked with a stream the test drives frame by frame (instead of the fixed
// array `helpers.js`'s `stream` route offers), so the DOM can be inspected
// between chunks.
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { bootMarketplace, flush } from './helpers.js';

const PERMISSIONS = [{ key: 'library.read', enforced: true }];

function card(over) {
    return {
        id: 'demo1', name: 'DemoOne', tagline: 'An official demo', description: '',
        author: '', license: '', homepage: '', trust: 'official',
        languages: ['en'], tags: [], icon: '', screenshots: [],
        requires_source: false, size: 4096, in_index: true, source: '',
        installed: false, installed_version: '', installed_not_in_index: false,
        offered_version: '1.0.0', update_available: false, state: 'not_installed',
        addon_state: '', installed_permissions: [], permissions: PERMISSIONS, versions: [],
        ...over,
    };
}

function progressEl() {
    return document.querySelector('[data-testid="marketplace-progress"]');
}

function barWidth() {
    const bar = progressEl().querySelector('.h-full');
    return bar.style.width;
}

// A controllable SSE body: `push(frame)` delivers exactly one
// `data: {...}\n\n` chunk to the next `reader.read()`, `finish()` ends the
// stream. Mirrors the framing `helpers.js`'s fixed-array `streamResponse`
// uses, but lets the test await the app's reaction between frames.
function controlledStream() {
    const encoder = new TextEncoder();
    const queue = [];
    let wake = null;
    let ended = false;
    const reader = {
        read: async () => {
            while (queue.length === 0 && !ended) {
                await new Promise((resolve) => { wake = resolve; });
            }
            if (queue.length) return { done: false, value: queue.shift() };
            return { done: true, value: undefined };
        },
        cancel: async () => {},
    };
    return {
        response: { ok: true, status: 200, body: { getReader: () => reader }, json: async () => ({}) },
        push(frame) {
            queue.push(encoder.encode(`data: ${JSON.stringify(frame)}\n\n`));
            if (wake) { const w = wake; wake = null; w(); }
        },
        finish() {
            ended = true;
            if (wake) { const w = wake; wake = null; w(); }
        },
    };
}

let calls;
let stream;

describe('marketplace install byte progress', () => {
    beforeAll(async () => {
        calls = await bootMarketplace([
            { method: 'GET', url: '/api/marketplace', reply: () => ({ addons: [card({})], offline: false, has_index: true, index_date: null, core: {} }) },
        ]);
        stream = controlledStream();
        const original = globalThis.fetch;
        globalThis.fetch = vi.fn(async (url, options = {}) => {
            if (url === '/api/marketplace/install/demo1' && (options.method || 'GET').toUpperCase() === 'POST') {
                calls.push({ method: 'POST', url, body: JSON.parse(options.body) });
                return stream.response;
            }
            return original(url, options);
        });
    });

    it('shows the real received/total bytes from the SSE progress events as they arrive', async () => {
        document.querySelector('[data-testid="marketplace-button-demo1"]').click();
        await vi.waitFor(() => {
            if (!progressEl()) throw new Error('progress bar not shown');
        });
        expect(progressEl().textContent).toContain('0 B');
        expect(progressEl().textContent).toContain('4.0 KB');
        expect(barWidth()).toBe('0%');

        stream.push({ type: 'progress', step: 'download', received: 1024, total: 4096 });
        await flush();
        expect(progressEl().textContent).toContain('1.0 KB');
        expect(progressEl().textContent).toContain('4.0 KB');
        expect(barWidth()).toBe('25%');

        stream.push({ type: 'progress', step: 'download', received: 2048, total: 4096 });
        await flush();
        expect(progressEl().textContent).toContain('2.0 KB');
        expect(barWidth()).toBe('50%');

        stream.push({ type: 'progress', step: 'download', received: 4096, total: 4096 });
        await flush();
        expect(progressEl().textContent).toContain('4.0 KB');
        expect(barWidth()).toBe('100%');

        stream.push({ type: 'progress', step: 'verify' });
        await flush();
        expect(progressEl().textContent).not.toContain('KB');
        expect(barWidth()).toBe('100%');

        stream.push({
            type: 'complete',
            addon: { id: 'demo1', state: 'consent_pending' },
            consent: { id: 'demo1', name: 'DemoOne', version: '1.0.0', trust: 'official',
                       origin: 'index', sha256: '', permissions: PERMISSIONS, missing: ['library.read'] },
        });
        stream.finish();
        await vi.waitFor(() => {
            if (progressEl()) throw new Error('progress bar still shown');
            if (!document.querySelector('[data-testid="consent-dialog"]')) throw new Error('consent dialog not shown');
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/marketplace/install/demo1')).toBe(true);
        document.querySelector('[data-testid="consent-decline"]').click();
        await flush();
    });
});
