// Library search (SPA-10): an older search that answers late never
// overwrites the result of a newer one - a request ticket drops it.
// Own file - app.js self-mounts once per module graph (see helpers.js);
// the second case re-boots it through `vi.resetModules()`.
import { describe, expect, it, vi } from 'vitest';
import { defaultRoutes, flush, installFetchMock, typeInto } from './helpers.js';

const OLD = [{ id: 1, title: 'Old answer', authors: 'A', year: 2020, filename: '', categories: [] }];
const NEW = [{ id: 2, title: 'New answer', authors: 'B', year: 2021, filename: '', categories: [] }];
const STALE_SEMANTIC = {
    mode: 'semantic',
    results: [{ paper_id: 3, citekey: 'stale2020', title: 'Stale semantic hit', year: 2020, abstract_excerpt: '', score: 0.9 }],
};

describe('library search: the newest request wins', () => {
    it('drops a late answer to an older query', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?search=new/, reply: NEW },
            { method: 'GET', url: /^\/api\/papers\?/, reply: [] },
            ...defaultRoutes(),
        ]);
        // Hold the answer to "old" until the newer search has landed.
        const mocked = globalThis.fetch;
        let releaseOld;
        const oldHeld = new Promise((resolve) => { releaseOld = resolve; });
        globalThis.fetch = vi.fn(async (url, options) => {
            if (String(url).startsWith('/api/papers?search=old')) {
                await oldHeld;
                return new Response(JSON.stringify(OLD), { status: 200, headers: { 'Content-Type': 'application/json' } });
            }
            return mocked(url, options);
        });
        await import('../../static/app.js');
        const input = await vi.waitFor(() => {
            const el = document.querySelector('input.lb-search-input');
            if (!el) throw new Error('library not rendered');
            return el;
        });

        typeInto(input, 'old');
        await new Promise((r) => setTimeout(r, 350));
        typeInto(input, 'new');
        await vi.waitFor(() => {
            if (!document.body.textContent.includes('New answer')) throw new Error('new result not shown');
        });
        releaseOld();
        await flush();
        await new Promise((r) => setTimeout(r, 50));
        await flush();
        expect(document.body.textContent).toContain('New answer');
        expect(document.body.textContent).not.toContain('Old answer');
    });

    // Both loaders share one ticket: clearing the search in semantic mode
    // takes a ticket without a request, so the semantic answer still in
    // flight lands nowhere instead of filling the list under an empty box.
    it('drops a semantic answer that arrives after the search was cleared', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        vi.resetModules();
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/llm/status', reply: { reasoning: true, fast: true, embedding: true, connections: 1 } },
            ...defaultRoutes(),
        ]);
        // Hold the semantic answer until the search has been cleared.
        const mocked = globalThis.fetch;
        let release;
        const held = new Promise((resolve) => { release = resolve; });
        globalThis.fetch = vi.fn(async (url, options) => {
            if (String(url).startsWith('/api/search/semantic?q=')) {
                await held;
                return new Response(JSON.stringify(STALE_SEMANTIC), { status: 200, headers: { 'Content-Type': 'application/json' } });
            }
            return mocked(url, options);
        });
        await import('../../static/app.js');
        const toggle = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="semantic-toggle"]');
            if (!el) throw new Error('semantic toggle not rendered');
            return el;
        });
        toggle.click();
        await flush();
        expect(toggle.classList.contains('is-on')).toBe(true);

        typeInto(document.querySelector('input.lb-search-input'), 'neural');
        await new Promise((r) => setTimeout(r, 350));
        await flush();
        expect(globalThis.fetch.mock.calls.some(([u]) => String(u).startsWith('/api/search/semantic?q=neural'))).toBe(true);

        const clear = await vi.waitFor(() => {
            const el = document.querySelector('.lb-search-clear');
            if (!el) throw new Error('clear button not rendered');
            return el;
        });
        clear.click();
        await flush();
        expect(document.querySelector('input.lb-search-input').value).toBe('');

        release();
        await flush();
        await new Promise((r) => setTimeout(r, 50));
        await flush();
        expect(document.body.textContent).not.toContain('Stale semantic hit');
        expect(document.querySelector('[data-testid="semantic-results"]')).toBeFalsy();
        // Nothing is loading any more either: the dropped answer must not
        // leave the spinner behind.
        expect(document.querySelector('.spinner')).toBeFalsy();
    });
});
