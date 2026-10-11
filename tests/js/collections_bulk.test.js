// "Add to collection" from the library list (#201): into an existing
// collection (idempotent, already-contained items reported), into a new one
// created in the same step, and the "Collection" list filter.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText, filterPill, filterPillActive, openFilterPill } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Doe, J.', year: 2021, filename: 'a.pdf', categories: [] },
    { id: 2, title: 'Second item', authors: 'Roe, R.', year: 2022, filename: 'b.pdf', categories: [] },
];
const COLLECTIONS = [{ id: 9, name: 'Paper X', description: '', archived: false, item_count: 1, pdf_count: 1 }];

const q = (sel) => document.querySelector(sel);

async function openPicker() {
    findByText('button', 'Choose action').click();
    await flush();
    q('[data-testid="bulk-add-to-collection"]').click();
    return vi.waitFor(() => {
        const el = q('[data-testid="collection-picker"]');
        if (!el || !el.querySelector('[data-collection-id="9"]')) throw new Error('picker not ready');
        return el;
    });
}

describe('item list: add to collection', () => {
    it('adds the selection to an existing or a new collection, and filters by collection', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?.*collection_id=9/, reply: [PAPERS[0]] },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/collections', reply: COLLECTIONS },
            { method: 'GET', url: '/api/collections?archived=all', reply: COLLECTIONS },
            { method: 'POST', url: '/api/collections/9/items', reply: { added_ids: [2], already_ids: [1] } },
            {
                method: 'POST', url: '/api/collections',
                reply: ({ body }) => ({ id: 10, name: body.name, description: '', archived: false,
                    item_count: 2, pdf_count: 2, added_ids: body.paper_ids, already_ids: [] }),
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await vi.waitFor(() => {
            if (!q('li[data-paper-id="2"]')) throw new Error('list not rendered');
        });

        for (const id of [1, 2]) {
            q(`input[type="checkbox"][value="${id}"]`).click();
            await flush();
        }

        // Existing collection: one added, one already there.
        let picker = await openPicker();
        picker.querySelector('[data-collection-id="9"] input').click();
        await flush();
        picker.querySelector('[data-testid="collection-picker-confirm"]').click();
        const toast = await vi.waitFor(() => {
            const el = q('[data-testid="collection-toast"]');
            if (!el) throw new Error('no toast');
            return el;
        });
        const add = calls.find((c) => c.method === 'POST' && c.url === '/api/collections/9/items');
        expect(add.body).toEqual({ paper_ids: [1, 2] });
        expect(toast.textContent).toContain('1 added, 1 already in → Paper X');
        expect(q('[data-testid="collection-picker"]')).toBeNull();

        // New collection, created with the selection in one request.
        // The selection stays, so it can go into a second collection.
        picker = await openPicker();
        picker.querySelector('[data-testid="collection-picker-new"]').click();
        await flush();
        const name = picker.querySelector('[data-testid="collection-picker-new-name"]');
        name.value = 'Paper Y';
        name.dispatchEvent(new Event('input'));
        await flush();
        picker.querySelector('[data-testid="collection-picker-confirm"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'POST' && c.url === '/api/collections')) throw new Error('no create');
        });
        const create = calls.find((c) => c.method === 'POST' && c.url === '/api/collections');
        expect(create.body).toEqual({ name: 'Paper Y', paper_ids: [1, 2] });

        // The list filter "Collection" narrows the list server-side.
        const pop = await openFilterPill('collection');
        pop.querySelector('[role="radio"][data-value="9"]').click();
        await vi.waitFor(() => {
            if (q('li[data-paper-id="2"]')) throw new Error('filter not applied');
        });
        expect(q('li[data-paper-id="1"]')).not.toBeNull();
        expect(calls.some((c) => c.method === 'GET' && /^\/api\/papers\?.*collection_id=9/.test(c.url))).toBe(true);
        // A pick from the list closes the popover; the pill names the collection.
        expect(document.getElementById('lb-fbar-pop-collection')).toBeNull();
        expect(filterPillActive('collection')).toBe(true);
        expect(filterPill('collection').textContent).toContain('Paper X');
    });
});
