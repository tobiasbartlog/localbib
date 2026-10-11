// Export in the item list (#212): one menu shared with the collection page.
// Scope follows the view -- the whole library without a filter, the server's
// scope as a plain GET link when it alone defines the list, the visible items
// as a POST body when a client-side filter narrows them, exactly the selection
// when there is one -- and the head names scope and count.
// One boot per file (the SPA mounts itself once), so the cases run in sequence.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, filterPill, openFilterPill } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Doe, J.', year: 2021, doi: '10.1/a', filename: 'a.pdf', categories: [] },
    { id: 2, title: 'Second item', authors: 'Roe, R.', year: 2022, doi: '10.1/b', filename: 'b.pdf', categories: [] },
    { id: 3, title: 'Third item', authors: 'Poe, P.', year: 2023, doi: '', filename: '', categories: [] },
];

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.trim();
const type = async (el, value) => {
    el.value = value;
    el.dispatchEvent(new Event('input'));
    await flush();
};

describe('item list: export menu', () => {
    it('follows the view: library, search, filter, selection', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            {
                method: 'POST', url: /^\/api\/export\/(bibtex|ris|csv|zip)$/, reply: { ok: true },
                headers: { 'Content-Disposition': 'attachment; filename=literatur_export.bib' },
            },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await vi.waitFor(() => {
            if (!q('input[type="checkbox"][value="2"]')) throw new Error('list not rendered');
        });
        URL.createObjectURL = vi.fn(() => 'blob:x');
        URL.revokeObjectURL = vi.fn();
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')
            .mockImplementation(function () { click.last = this.download; });
        const openMenu = async () => {
            if (!q('[data-testid="list-export-menu"]')) q('[data-testid="list-action-export"]').click();
            await flush();
        };
        const closeMenu = async () => {
            if (q('[data-testid="list-export-menu"]')) q('[data-testid="list-action-export"]').click();
            await flush();
        };
        const posts = () => calls.filter((c) => c.method === 'POST' && c.url.startsWith('/api/export/'));

        // 1. No filter: the whole library, plain GET links, count in the head.
        await openMenu();
        expect(text('[data-testid="list-export-scope"]')).toBe('3 items');
        for (const fmt of ['bibtex', 'ris', 'csv', 'zip']) {
            expect(q(`[data-testid="list-export-${fmt}"]`).getAttribute('href')).toBe(`/api/export/${fmt}`);
        }
        expect(text('[data-testid="list-export-bibtex"]')).toBe('BibTeX (.bib)');
        await closeMenu();

        // 2. A search alone defines the list: handed over as the server's scope.
        await type(q('.lb-search-input'), 'neural');
        await new Promise((r) => setTimeout(r, 450));
        await flush();
        await openMenu();
        expect(q('[data-testid="list-export-ris"]').getAttribute('href')).toBe('/api/export/ris?search=neural');
        await closeMenu();
        q('.lb-search-clear').click();
        await flush();

        // 3. A client-side filter narrows it: the visible items go by id.
        const yearPop = await openFilterPill('year');
        const from = yearPop.querySelector('[data-testid="filter-year-from"]');
        await type(from, '2022');
        await openMenu();
        expect(text('[data-testid="list-export-scope"]')).toBe('2 items');
        const btn = q('[data-testid="list-export-bibtex"]');
        expect(btn.tagName).toBe('BUTTON');
        btn.click();
        await vi.waitFor(() => { if (posts().length !== 1) throw new Error('no POST'); });
        expect(posts()[0].url).toBe('/api/export/bibtex');
        expect(posts()[0].body).toEqual({ ids: [2, 3] });
        await vi.waitFor(() => { if (!click.last) throw new Error('no download'); });
        expect(click.last).toBe('literatur_export.bib');
        // The Year pill's × drops the filter again.
        filterPill('year').closest('.lb-fbar-pw').querySelector('.lb-fbar-clr').click();
        await flush();
        await closeMenu();

        // 4. A selection wins: exactly those items, named in the head.
        q('input[type="checkbox"][value="1"]').click();
        await flush();
        q('input[type="checkbox"][value="3"]').click();
        await flush();
        await openMenu();
        expect(text('[data-testid="list-export-scope"]')).toBe('Selection: 2');
        q('[data-testid="list-export-zip"]').click();
        await vi.waitFor(() => { if (posts().length !== 2) throw new Error('no POST'); });
        expect(posts()[1].url).toBe('/api/export/zip');
        expect(posts()[1].body).toEqual({ ids: [1, 3] });
        click.mockRestore();

        // 5. Export / Import is no Settings section any more.
        window.location.hash = '#/settings/export';
        await vi.waitFor(() => {
            if (!q('[data-settings-section="general"]')) throw new Error('no fallback to general');
        });
        expect(q('[data-settings-section="export"]')).toBeNull();
        expect(q('[data-testid="settings-nav-export"]')).toBeNull();
    });
});
