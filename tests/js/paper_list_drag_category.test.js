// Drag items from the library list onto a sidebar category (#199): the drop
// sends the dragged ids, the toast says what happened, and undo removes
// exactly the pairs the drop created.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Doe, J.', year: 2021, filename: 'a.pdf', categories: [] },
    { id: 2, title: 'Second item', authors: 'Roe, R.', year: 2022, filename: 'b.pdf', categories: [] },
    { id: 3, title: 'Third item', authors: 'Poe, P.', year: 2023, filename: 'c.pdf', categories: [] },
];
const TREE = [{ id: 7, name: 'Methods', paper_count: 1, children: [] }];

const q = (sel) => document.querySelector(sel);

// jsdom has no DataTransfer; a plain store with the same surface is enough.
function fakeDataTransfer() {
    const store = {};
    return {
        get types() { return Object.keys(store); },
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

describe('item list: drag onto a sidebar category', () => {
    it('assigns the whole selection, reports already-assigned items and undoes only new pairs', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/categories/tree', reply: TREE },
            { method: 'POST', url: '/api/papers/categories', reply: { added_ids: [2], already_ids: [3] } },
            { method: 'POST', url: '/api/papers/categories/remove', reply: { removed_ids: [2] } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        await vi.waitFor(() => {
            if (!q('li[data-paper-id="3"]') || !q('[data-category-id="7"]')) throw new Error('not rendered');
        });
        q('input[type="checkbox"][value="2"]').click();
        await flush();
        q('input[type="checkbox"][value="3"]').click();
        await flush();

        const dt = fakeDataTransfer();
        fire(q('li[data-paper-id="3"]'), 'dragstart', dt);
        expect(dt.types).toEqual(['application/x-localbib-papers']);
        expect(dt.types).not.toContain('Files');

        const target = q('[data-category-id="7"]');
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
        const add = calls.find((c) => c.method === 'POST' && c.url === '/api/papers/categories');
        expect(add.body).toEqual({ paper_ids: [2, 3], category_id: 7 });
        expect(toast.textContent).toContain('1 added, 1 already assigned → Methods');

        q('[data-testid="drop-toast-undo"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.url === '/api/papers/categories/remove')) throw new Error('no undo');
        });
        const undo = calls.find((c) => c.url === '/api/papers/categories/remove');
        expect(undo.body).toEqual({ paper_ids: [2], category_id: 7 });
    });

    it('an unselected row drags only itself, and a non-item drag is ignored by categories', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        vi.resetModules();
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/categories/tree', reply: TREE },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await vi.waitFor(() => {
            if (!q('li[data-paper-id="1"]') || !q('[data-category-id="7"]')) throw new Error('not rendered');
        });
        q('input[type="checkbox"][value="2"]').click();
        await flush();

        const dt = fakeDataTransfer();
        fire(q('li[data-paper-id="1"]'), 'dragstart', dt);
        expect(JSON.parse(dt.getData('application/x-localbib-papers'))).toEqual([1]);

        const files = fakeDataTransfer();
        files.setData('Files', '');
        const over = fire(q('[data-category-id="7"]'), 'dragover', files);
        await flush();
        expect(q('[data-category-id="7"]').classList.contains('is-drop-target')).toBe(false);
        // The global PDF drop at the root still sees a file drag.
        expect(over.defaultPrevented).toBe(true);
    });

    it('a folded rail unfolds while an item drag rests on it and folds again at the drag end', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        localStorage.setItem('lbSidebarCollapsed', '1');
        vi.resetModules();
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/categories/tree', reply: TREE },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await vi.waitFor(() => {
            if (!q('li[data-paper-id="1"]')) throw new Error('not rendered');
        });
        const rail = q('aside.lb-sidebar');
        expect(rail.classList.contains('is-collapsed')).toBe(true);

        const dt = fakeDataTransfer();
        const row = q('li[data-paper-id="1"]');
        fire(row, 'dragstart', dt);
        fire(rail, 'dragenter', dt);
        await vi.waitFor(() => {
            if (rail.classList.contains('is-collapsed')) throw new Error('still folded');
        }, { timeout: 1500 });

        fire(row, 'dragend', dt);
        await flush();
        expect(rail.classList.contains('is-collapsed')).toBe(true);
        expect(localStorage.getItem('lbSidebarCollapsed')).toBe('1');
        localStorage.removeItem('lbSidebarCollapsed');
    });
});
