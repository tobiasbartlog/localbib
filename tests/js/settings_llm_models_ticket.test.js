// Settings -> AI models (SPA-11): editing a connection while its model list
// is loading drops that answer - the old endpoint's list never lands after
// the edit invalidated it. Same for "remove key" and Discard.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootSettings, flush } from './helpers.js';
import { CONFIG } from './startup_check_fixtures.js';

const q = (sel) => document.querySelector(sel);
const conn = (id, sel) => q(`[data-testid="llm-connection-${id}"] ${sel}`);

// Replaces the probe route: `holdNext()` holds the next probe (answering the
// stale list) until the returned release is called; any other probe answers
// at once with the fresh list.
function armProbes() {
    const mocked = globalThis.fetch;
    let pending = null;
    globalThis.fetch = vi.fn(async (url, options) => {
        if (url === '/api/llm/models') {
            if (pending) {
                const held = pending;
                pending = null;
                await held;
                return new Response(JSON.stringify({ models: ['stale-model'], error: null }), { status: 200 });
            }
            return new Response(JSON.stringify({ models: ['fresh-model'], error: null }), { status: 200 });
        }
        return mocked(url, options);
    });
    return function holdNext() {
        let release;
        pending = new Promise((resolve) => { release = resolve; });
        return async () => {
            release();
            await new Promise((r) => setTimeout(r, 30));
            await flush();
        };
    };
}

async function openEditor(id) {
    conn(id, '[data-testid="llm-conn-edit"]').click();
    await flush();
}

async function typeInto(el, value) {
    el.value = value;
    el.dispatchEvent(new Event('input'));
    await flush();
}

describe('settings: AI models - a stale model list is dropped', () => {
    it('ignores the answer of a request the edit invalidated', async () => {
        await bootSettings([
            { method: 'GET', url: '/api/llm/config', reply: CONFIG },
        ], 'ai-models');
        const holdNext = armProbes();

        // A key edit while the probe is in flight.
        let release = holdNext();
        conn('local', '[data-testid="llm-conn-load-models"]').click();
        await flush();
        await openEditor('local');
        await typeInto(conn('local', '[data-testid="llm-conn-api-key"]'), 'new-key');
        await release();
        expect(document.body.innerHTML).not.toContain('stale-model');
        // Loading again after the edit shows the fresh list.
        conn('local', '[data-testid="llm-conn-load-models"]').click();
        await vi.waitFor(() => {
            if (!document.body.innerHTML.includes('fresh-model')) throw new Error('fresh list not shown');
        });

        // "remove key" on a connection with a stored key.
        release = holdNext();
        conn('oai', '[data-testid="llm-conn-load-models"]').click();
        await flush();
        await openEditor('oai');
        const removeKey = [...document.querySelectorAll('[data-testid="llm-connection-oai"] button')]
            .find((b) => b.textContent.trim() === 'remove key');
        expect(removeKey).toBeTruthy();
        removeKey.click();
        await flush();
        await release();
        expect(document.body.innerHTML).not.toContain('stale-model');

        // Discard restores the saved document: the answer for the draft's key
        // ('new-key' from above, so the draft differs) must not land on the
        // restored connection.
        release = holdNext();
        conn('local', '[data-testid="llm-conn-load-models"]').click();
        await flush();
        q('[data-testid="llm-discard"]').click();
        await flush();
        await release();
        expect(document.body.innerHTML).not.toContain('stale-model');
    });
});
