// Collections in the sidebar (#201): active ones listed, the archive behind a
// toggle, "+" creates a new one and opens its page.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('sidebar: collections section', () => {
    it('lists active collections, reveals the archive and creates a new one', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        let all = [
            { id: 1, name: 'Paper X', description: '', archived: false, item_count: 3, pdf_count: 2 },
            { id: 2, name: 'Old review', description: '', archived: true, item_count: 5, pdf_count: 5 },
        ];
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/collections?archived=all', reply: () => all },
            {
                method: 'POST', url: '/api/collections',
                reply: ({ body }) => {
                    const created = { id: 3, name: body.name, description: body.description,
                        archived: false, item_count: 0, pdf_count: 0, added_ids: [], already_ids: [] };
                    all = all.concat([created]);
                    return created;
                },
            },
            { method: 'GET', url: '/api/collections/3', reply: () => all.find((c) => c.id === 3) },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        const section = await vi.waitFor(() => {
            const el = q('[data-testid="sidebar-collections"]');
            if (!el || !el.querySelector('[data-collection-id="1"]')) throw new Error('not rendered');
            return el;
        });
        expect(section.textContent).toContain('Paper X');
        expect(section.textContent).not.toContain('Old review');

        q('[data-testid="collections-archive-toggle"]').click();
        await flush();
        expect(section.querySelector('[data-collection-id="2"]').textContent).toContain('Old review');

        q('[data-testid="collection-new"]').click();
        await flush();
        const name = q('[data-testid="collection-new-name"]');
        name.value = 'Literature for paper Y';
        name.dispatchEvent(new Event('input'));
        await flush();
        q('[data-testid="collection-new-create"]').click();

        await vi.waitFor(() => {
            if (!q('[data-testid="collection-head"]')) throw new Error('collection page not open');
        });
        const create = calls.find((c) => c.method === 'POST' && c.url === '/api/collections');
        expect(create.body).toEqual({ name: 'Literature for paper Y', description: '' });
        expect(window.location.hash).toBe('#/collection/3');
        expect(q('.lb-page-title').textContent).toContain('Literature for paper Y');
        expect(q('[data-testid="sidebar-collections"] [data-collection-id="3"]')).not.toBeNull();
    });
});
