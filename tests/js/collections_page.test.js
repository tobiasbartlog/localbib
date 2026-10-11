// The collection page (#201): the head with description, count and PDF
// coverage, the reused item list scoped by collection_id, removing selected
// items, archiving and deleting with a confirmation. The head is one head:
// two keys, an Add menu and a "more" menu sharing one open slot.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText, openHeadMenu } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('collection page', () => {
    it('shows the head, lists only its items, removes, archives and deletes', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        vi.stubGlobal('confirm', () => true);
        let coll = { id: 4, name: 'Paper X', description: 'Does Y cause Z?', archived: false,
            item_count: 2, pdf_count: 1 };
        let members = [
            { id: 1, title: 'First item', authors: 'Doe, J.', year: 2021, filename: 'a.pdf', categories: [], collection_note: 'key method' },
            { id: 2, title: 'Second item', authors: 'Roe, R.', year: 2022, filename: '', categories: [], collection_note: '' },
        ];
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/collections?archived=all', reply: () => [coll] },
            { method: 'GET', url: /^\/api\/papers\?.*collection_id=4/, reply: () => members },
            { method: 'GET', url: '/api/collections/4', reply: () => coll },
            {
                method: 'DELETE', url: '/api/collections/4/items',
                reply: ({ body }) => {
                    members = members.filter((p) => !body.paper_ids.includes(p.id));
                    coll = { ...coll, item_count: members.length, pdf_count: 1 };
                    return { removed_ids: body.paper_ids };
                },
            },
            {
                method: 'PATCH', url: '/api/collections/4',
                reply: ({ body }) => { coll = { ...coll, archived: !!body.archived }; return coll; },
            },
            { method: 'DELETE', url: '/api/collections/4', reply: { status: 'ok', id: 4, item_count: 1 } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        const row = await vi.waitFor(() => {
            const el = q('[data-testid="sidebar-collections"] [data-collection-id="4"]');
            if (!el) throw new Error('sidebar not rendered');
            return el;
        });
        row.click();

        const head = await vi.waitFor(() => {
            const el = q('[data-testid="collection-head"]');
            if (!el || !q('li[data-paper-id="2"]')) throw new Error('page not rendered');
            return el;
        });
        expect(head.querySelector('.lb-page-title').textContent).toContain('Paper X');
        expect(head.querySelector('[data-testid="collection-description"]').textContent).toBe('Does Y cause Z?');
        expect(head.querySelector('[data-testid="collection-count"]').textContent).toBe('2 items');
        expect(head.querySelector('[data-testid="collection-pdf-coverage"]').textContent).toBe('1 of 2 with PDF (50 %)');
        // One head, one count, one Research chat: no second figure line and
        // no library key beside the Collection's own.
        expect(document.querySelectorAll('[data-testid="collection-count"]').length).toBe(1);
        expect(document.querySelectorAll('[data-testid="collection-action-chat"]').length).toBe(1);
        expect([...head.querySelectorAll('button')].filter((b) => /research chat/i.test(b.textContent)).length).toBe(1);
        expect(q('[data-testid="collection-item-note"]').textContent).toBe('key method');
        expect(calls.some((c) => /^\/api\/papers\?.*collection_id=4/.test(c.url))).toBe(true);

        // The two menus share one open slot; Esc closes and hands focus back.
        await openHeadMenu('coll-add');
        const more = await openHeadMenu('coll-more');
        expect(q('#lb-fbar-pop-coll-add')).toBeNull();
        expect(more.getAttribute('role')).toBe('menu');
        await flush();
        expect(document.activeElement.getAttribute('data-testid')).toBe('collection-action-stats');
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
        expect(document.activeElement.getAttribute('data-testid')).toBe('collection-action-validate');
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await flush();
        expect(q('#lb-fbar-pop-coll-more')).toBeNull();
        expect(document.activeElement.getAttribute('data-testid')).toBe('collection-action-more');

        // Remove the selected item from the collection only.
        q('input[type="checkbox"][value="2"]').click();
        await flush();
        findByText('button', 'Choose action').click();
        await flush();
        q('[data-testid="bulk-remove-from-collection"]').click();
        await vi.waitFor(() => {
            if (q('li[data-paper-id="2"]')) throw new Error('still listed');
        });
        const removal = calls.find((c) => c.method === 'DELETE' && c.url === '/api/collections/4/items');
        expect(removal.body).toEqual({ paper_ids: [2] });
        expect(calls.some((c) => c.method === 'DELETE' && /^\/api\/papers/.test(c.url))).toBe(false);
        await vi.waitFor(() => {
            if (q('[data-testid="collection-count"]').textContent !== '1 item') throw new Error('count stale');
        });

        // Archive, then delete - both from the "more" menu.
        (await openHeadMenu('coll-more')).querySelector('[data-testid="collection-archive"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="collection-archived"]')) throw new Error('not archived');
        });
        expect(calls.find((c) => c.method === 'PATCH').body).toEqual({ archived: true });
        expect(q('#lb-fbar-pop-coll-more')).toBeNull();   // a pick closes the menu
        expect((await openHeadMenu('coll-more')).querySelector('[data-testid="collection-archive"]').textContent.trim()).toBe('Restore');

        q('[data-testid="collection-delete"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'DELETE' && c.url === '/api/collections/4')) throw new Error('no delete');
        });
        await vi.waitFor(() => {
            if (window.location.hash !== '#/') throw new Error('still on the page');
        });
        vi.unstubAllGlobals();
    });
});
