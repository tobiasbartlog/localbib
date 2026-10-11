// Import into a collection (#203): the BibTeX/.tex dialog on the collection
// page commits into that collection and shows the re-import's "no longer
// cited" suggestion, which removes only after an explicit click.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, openHeadMenu } from './helpers.js';

const q = (sel) => document.querySelector(sel);

function pickFile(input, file) {
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change'));
}

describe('import into a collection', () => {
    it('commits a .bib into the collection and suggests what is no longer cited', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const coll = { id: 4, name: 'Paper X', description: '', archived: false, item_count: 2, pdf_count: 0 };
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/collections(\?.*)?$/, reply: () => [coll] },
            { method: 'GET', url: '/api/collections/4', reply: () => coll },
            {
                method: 'POST', url: '/api/import/bibtex/preview',
                reply: {
                    total: 3, cited_count: 2, matched_count: 1,
                    entries: [
                        { key: 'kept', title: 'Still cited', authors: 'Doe, J.', year: 2020, doi: '', raw: {},
                          cited: true, match: { paper_id: 1, cite_key: 'kept' } },
                        { key: 'fresh', title: 'Fresh work', authors: 'Neu, N.', year: 2021, doi: '10.5/f', raw: {},
                          cited: true, match: null },
                        { key: 'other', title: 'Not cited', authors: '', year: 2019, doi: '', raw: {},
                          cited: false, match: null },
                    ],
                },
            },
            {
                method: 'POST', url: '/api/import/bibtex/commit',
                reply: {
                    total: 2, created: 1, matched: 1, errors: 0, enriched: 0,
                    results: [
                        { key: 'kept', status: 'matched', paper_id: 1, cite_key: 'kept' },
                        { key: 'fresh', status: 'created', paper_id: 9, cite_key: 'fresh' },
                    ],
                    collection: {
                        id: 4, name: 'Paper X', created: false, added_ids: [9], already_ids: [1],
                        not_cited: [{ paper_id: 2, title: 'Dropped citation', cite_key: 'dropped', year: 2018 }],
                    },
                },
            },
            { method: 'DELETE', url: '/api/collections/4/items', reply: ({ body }) => ({ removed_ids: body.paper_ids }) },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        window.location.hash = '#/collection/4';
        window.dispatchEvent(new HashChangeEvent('hashchange'));

        // .bib/.tex import sits in the head's Add menu.
        const add = await openHeadMenu('coll-add');
        add.querySelector('[data-testid="collection-action-bib-import"]').click();
        await flush();
        expect(q('#lb-fbar-pop-coll-add')).toBeNull();
        const dialog = await vi.waitFor(() => {
            const el = q('[data-testid="bib-import"]');
            if (!el || el.querySelector('[data-testid="bib-import-target"]').value !== '4') throw new Error('dialog not ready');
            return el;
        });
        pickFile(dialog.querySelector('[data-testid="bib-import-bib"]'), new File(['@article{x}'], 'paper.bib'));
        pickFile(dialog.querySelector('[data-testid="bib-import-tex"]'), new File(['\\cite{x}'], 'paper.tex'));
        const oa = dialog.querySelector('[data-testid="bib-import-oa"]');
        oa.click();
        await flush();
        q('[data-testid="bib-import-preview"]').click();

        const summary = await vi.waitFor(() => {
            const el = q('[data-testid="bib-import-summary"]');
            if (!el) throw new Error('no preview');
            return el;
        });
        expect(summary.textContent).toContain('2 entries: 1 already in the library, 1 new');
        q('[data-testid="bib-import-commit"]').click();

        const suggestion = await vi.waitFor(() => {
            const el = q('[data-testid="bib-import-not-cited"]');
            if (!el) throw new Error('no result');
            return el;
        });
        const commit = calls.find((c) => c.url === '/api/import/bibtex/commit');
        expect(commit.body.collection_id).toBe(4);
        expect(commit.body.entries.map((e) => e.key)).toEqual(['kept', 'fresh']);
        expect(commit.body.entries[0].matched_paper_id).toBe(1);
        expect(calls.some((c) => c.url === '/api/import/bibtex/oa-check')).toBe(false);
        expect(suggestion.textContent).toContain('1 item is no longer cited');
        expect(suggestion.querySelector('[data-paper-id="2"]').textContent).toContain('Dropped citation');
        // A suggestion: nothing has been removed by the commit.
        expect(calls.some((c) => c.method === 'DELETE')).toBe(false);

        q('[data-testid="bib-import-remove"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="bib-import-removed"]')) throw new Error('not removed');
        });
        const removal = calls.find((c) => c.method === 'DELETE' && c.url === '/api/collections/4/items');
        expect(removal.body).toEqual({ paper_ids: [2] });
    });
});
