// Analyses and export on a collection (#202): the actions in the collection
// head (chat and network as keys, the rest in its "more" menu), the export
// formats scoped by collection_id, statistics and refresh-review showing their
// scope, the research chat started with the resolved item ids, and the
// citation network page labelled with the scope.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, openHeadMenu } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('collection page: analyses and export', () => {
    it('runs every analysis on exactly the collection and shows the scope', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const coll = { id: 4, name: 'Paper X', description: '', archived: false, item_count: 2, pdf_count: 1 };
        const members = [
            { id: 1, title: 'First item', authors: 'Doe, J.', year: 2021, filename: 'a.pdf', categories: [] },
            { id: 2, title: 'Second item', authors: 'Roe, R.', year: 2022, filename: '', categories: [] },
        ];
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/collections?archived=all', reply: [coll] },
            { method: 'GET', url: /^\/api\/papers\?.*collection_id=4/, reply: members },
            { method: 'GET', url: '/api/papers', reply: members },
            { method: 'GET', url: '/api/collections/4', reply: coll },
            {
                method: 'GET', url: '/api/stats?collection_id=4',
                reply: {
                    scope: { type: 'collection', id: 4, name: 'Paper X' }, paper_count: 2, category_count: 0,
                    year_min: 2021, year_max: 2022, with_doi: 0, with_pdf: 1,
                    by_year: [{ year: 2021, count: 1 }, { year: 2022, count: 1 }],
                    top_venues: [], top_categories: [],
                },
            },
            {
                method: 'GET', url: /^\/api\/research-chat\/chunk-status/,
                reply: { papers: [{ paper_id: 1, chunked: true, chunk_count: 3 }, { paper_id: 2, chunked: true, chunk_count: 2 }] },
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        const row = await vi.waitFor(() => {
            const el = q('[data-testid="sidebar-collections"] [data-collection-id="4"]');
            if (!el) throw new Error('sidebar not rendered');
            return el;
        });
        row.click();
        const bar = await vi.waitFor(() => {
            const el = q('[data-testid="collection-actions"]');
            if (!el) throw new Error('action bar not rendered');
            return el;
        });

        // Export, inside the "more" menu: every format scoped by collection_id.
        const more = await openHeadMenu('coll-more');
        expect(bar.contains(more)).toBe(true);
        expect(more.querySelector('[data-testid="collection-export-scope"]').textContent).toBe('2 items');
        for (const fmt of ['bibtex', 'ris', 'csv', 'zip']) {
            const link = more.querySelector(`[data-testid="collection-export-${fmt}"]`);
            expect(link.getAttribute('href')).toBe(`/api/export/${fmt}?collection_id=4`);
            expect(link.getAttribute('role')).toBe('menuitem');
        }

        // Statistics: scoped request, scope label.
        more.querySelector('[data-testid="collection-action-stats"]').click();
        const stats = await vi.waitFor(() => {
            const el = q('[data-testid="collection-stats"]');
            if (!el) throw new Error('stats not open');
            return el;
        });
        expect(stats.querySelector('[data-testid="collection-stats-scope"]').textContent).toBe('Scope: collection “Paper X”');
        expect(stats.querySelector('[data-testid="collection-stats-pdf"]').textContent).toBe('1 with PDF');
        expect(stats.querySelectorAll('[data-testid="collection-stats-byYear"] li').length).toBe(2);
        stats.parentElement.click();  // backdrop closes
        await flush();

        // Refresh with review: the resolved ids, labelled with the scope.
        (await openHeadMenu('coll-more')).querySelector('[data-testid="collection-action-refresh"]').click();
        const scope = await vi.waitFor(() => {
            const el = q('[data-testid="refresh-review-scope"]');
            if (!el) throw new Error('refresh review not open');
            return el;
        });
        expect(scope.textContent).toBe('Scope: collection “Paper X”');
        q('[data-testid="bulk-refresh-review-panel"]').parentElement.click();
        await flush();

        // Research chat: started with the collection's ids and its name.
        bar.querySelector('[data-testid="collection-action-chat"]').click();
        const chatScope = await vi.waitFor(() => {
            const el = q('[data-testid="chat-scope"]');
            if (!el) throw new Error('chat not opened');
            return el;
        });
        expect(chatScope.textContent).toBe('Scope: collection “Paper X”');
        expect(window.location.hash).toContain('paper_ids=1,2');
        expect(calls.some((c) => c.url === '/api/research-chat/chunk-status?paper_ids=1,2')).toBe(true);

        // Citation network: the analysis page carries the scope.
        window.location.hash = '#/collection/4';
        const networkBtn = await vi.waitFor(() => {
            const el = q('[data-testid="collection-action-network"]');
            if (!el) throw new Error('back on the page');
            return el;
        });
        networkBtn.click();
        const label = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-scope"]');
            if (!el || !el.textContent.includes('Paper X')) throw new Error('analysis scope missing');
            return el;
        });
        expect(label.textContent).toBe('Scope: collection “Paper X”');
        expect(q('[data-testid="analysis-scope-clear"]')).not.toBeNull();
    });
});
