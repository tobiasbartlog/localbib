// Drag items from the library list onto a sidebar collection (handoff task
// 12): active collections take the drop like the categories (#199), the toast
// says what happened, undo removes exactly the newly added items, and an
// archived collection never accepts a drop.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Doe, J.', year: 2021, filename: 'a.pdf', categories: [] },
    { id: 2, title: 'Second item', authors: 'Roe, R.', year: 2022, filename: 'b.pdf', categories: [] },
    { id: 3, title: 'Third item', authors: 'Poe, P.', year: 2023, filename: 'c.pdf', categories: [] },
];
const COLLECTIONS = [
    { id: 11, name: 'Reading list', archived: false, item_count: 1 },
    { id: 12, name: 'Old project', archived: true, item_count: 4 },
];

const q = (sel) => document.querySelector(sel);

function fakeDataTransfer() {
    const store = {};
    return {
        get types() { return Object.keys(store); },
        // A real item drag carries an empty FileList; the App's global PDF
        // drop handler reads it when a drop bubbles up (archived row).
        files: [],
        setData(type, value) { store[type] = value; },
        getData(type) { return store[type] || ''; },
        setDragImage() {},
        effectAllowed: 'none',
        dropEffect: 'none',
    };
}

function fire(el, type, dataTransfer) {
    const ev = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: dataTransfer });
    el.dispatchEvent(ev);
    return ev;
}

describe('item list: drag onto a sidebar collection', () => {
    it('adds the selection, reports items already in it and undoes only the new ones', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/collections?archived=all', reply: COLLECTIONS },
            { method: 'POST', url: '/api/collections/11/items', reply: { added_ids: [2], already_ids: [3] } },
            { method: 'DELETE', url: '/api/collections/11/items', reply: { removed_ids: [2] } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        await vi.waitFor(() => {
            if (!q('li[data-paper-id="3"]') || !q('[data-collection-id="11"]')) throw new Error('not rendered');
        });
        q('input[type="checkbox"][value="2"]').click();
        await flush();
        q('input[type="checkbox"][value="3"]').click();
        await flush();

        const dt = fakeDataTransfer();
        fire(q('li[data-paper-id="3"]'), 'dragstart', dt);
        const target = q('[data-collection-id="11"]');
        const over = fire(target, 'dragover', dt);
        expect(over.defaultPrevented).toBe(true);
        await flush();
        expect(target.classList.contains('is-drop-target')).toBe(true);
        fire(target, 'drop', dt);

        const toast = await vi.waitFor(() => {
            const el = q('[data-testid="drop-toast"]');
            if (!el) throw new Error('no toast');
            return el;
        });
        const add = calls.find((c) => c.method === 'POST' && c.url === '/api/collections/11/items');
        expect(add.body).toEqual({ paper_ids: [2, 3] });
        expect(toast.textContent).toContain('1 added, 1 already in → Reading list');
        expect(target.classList.contains('is-drop-target')).toBe(false);
        const loadsBefore = calls.filter((c) => c.url === '/api/collections?archived=all').length;

        q('[data-testid="drop-toast-undo"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'DELETE')) throw new Error('no undo');
        });
        const undo = calls.find((c) => c.method === 'DELETE' && c.url === '/api/collections/11/items');
        expect(undo.body).toEqual({ paper_ids: [2] });
        // The counts are fetched again.
        await vi.waitFor(() => {
            if (calls.filter((c) => c.url === '/api/collections?archived=all').length <= loadsBefore) {
                throw new Error('counts not refreshed');
            }
        });

        // --- an archived collection never accepts a drop ---------------------
        q('[data-testid="collections-archive-toggle"]').click();
        await flush();
        const archived = await vi.waitFor(() => {
            const el = q('[data-collection-id="12"]');
            if (!el) throw new Error('archive not shown');
            return el;
        });
        const dt2 = fakeDataTransfer();
        fire(q('li[data-paper-id="1"]'), 'dragstart', dt2);
        fire(archived, 'dragover', dt2);
        await flush();
        expect(archived.classList.contains('is-drop-target')).toBe(false);
        fire(archived, 'drop', dt2);
        await flush();
        await flush();
        expect(calls.some((c) => c.url === '/api/collections/12/items')).toBe(false);
    });
});
