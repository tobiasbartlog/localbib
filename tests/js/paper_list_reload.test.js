// The item list stays in place while it reloads: the current rows stay on
// screen, dimmed and aria-busy, instead of a spinner that collapses the page
// (and with it the scroll position). And a filter set on one list does not
// follow you to the next one - nor does resetting it load the list twice.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import {
    defaultRoutes, filterPillActive, flush, goTo, installFetchMock, openFilterPill,
} from './helpers.js';

const PAPERS = [
    { id: 1, title: 'In the collection', authors: 'A', year: 2015, doi: '10.1/a', filename: 'a.pdf', categories: [{ id: 7, name: 'Methods' }] },
    { id: 2, title: 'Not in it', authors: 'B', year: 2020, doi: '', filename: '', categories: [{ id: 7, name: 'Methods' }] },
];
const COLLECTIONS = [{ id: 9, name: 'Paper X', description: '', archived: false, item_count: 1, pdf_count: 1 }];

const q = (sel) => document.querySelector(sel);
const rows = () => [...document.querySelectorAll('li[data-paper-id]')].map((li) => Number(li.getAttribute('data-paper-id'))).sort();
const papersCalls = (calls) => calls.filter((c) => String(c[0]).startsWith('/api/papers?'));

describe('item list: reload and navigation', () => {
    it('keeps the list while reloading and resets filters on a new list', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        history.replaceState(null, '', '#/');
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?.*collection_id=9/, reply: [PAPERS[0]] },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/collections', reply: COLLECTIONS },
            { method: 'GET', url: '/api/categories', reply: [{ id: 7, name: 'Methods' }] },
            ...defaultRoutes(),
        ]);
        // Hold the Collection's answer until the test lets it through.
        const mocked = globalThis.fetch;
        let release = null;
        globalThis.fetch = vi.fn(async (url, options) => {
            if (String(url).includes('collection_id=9') && !release) {
                await new Promise((resolve) => { release = resolve; });
            }
            return mocked(url, options);
        });
        await import('../../static/app.js');
        await vi.waitFor(() => {
            if (rows().length !== 2) throw new Error('list not rendered');
        });
        const region = q('[data-testid="list-region"]');
        expect(region.getAttribute('aria-busy')).toBe('false');

        // --- A server-side reload keeps the rows, dimmed, and shows no spinner
        (await openFilterPill('collection')).querySelector('[role="radio"][data-value="9"]').click();
        await vi.waitFor(() => {
            if (!release) throw new Error('collection request not sent yet');
        });
        await flush();
        expect(rows()).toEqual([1, 2]);
        expect(q('[data-testid="list-region"]')).toBe(region);
        expect(region.getAttribute('aria-busy')).toBe('true');
        expect(region.classList.contains('is-busy')).toBe(true);
        expect(q('.spinner')).toBeNull();
        release();
        await vi.waitFor(() => {
            if (rows().length !== 1) throw new Error('collection answer not shown');
        });
        expect(rows()).toEqual([1]);
        expect(region.getAttribute('aria-busy')).toBe('false');

        // --- A new list starts unfiltered, with one load ---------------------
        (await openFilterPill('pdf')).querySelector('[role="radio"][data-value="yes"]').click();
        await flush();
        expect(filterPillActive('pdf')).toBe(true);
        expect(filterPillActive('collection')).toBe(true);
        const before = papersCalls(globalThis.fetch.mock.calls).length;
        goTo('#/category/7');
        await vi.waitFor(() => {
            if (!papersCalls(globalThis.fetch.mock.calls).some((c) => String(c[0]).includes('category_id=7'))) {
                throw new Error('category not loaded');
            }
        });
        await flush(); await flush();
        const after = papersCalls(globalThis.fetch.mock.calls).slice(before);
        expect(after.map((c) => String(c[0]))).toEqual(['/api/papers?category_id=7&']);
        await vi.waitFor(() => {
            if (rows().length !== 2) throw new Error('category list not shown');
        });
        expect(filterPillActive('pdf')).toBe(false);
        expect(filterPillActive('collection')).toBe(false);
        expect(q('[data-testid="filter-reset"]')).toBeNull();
        expect(q('.lb-page-meta').textContent).toMatch(/^\s*2 items/);
    });
});
