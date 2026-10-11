// Collections in the item detail (#201): the item's collections with its note
// there, editing that note, and a delete warning that names how many
// collections the item would leave.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('item detail: collections', () => {
    it('lists collections with notes, edits a note and warns on delete', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        let note = 'key method';
        const item = () => ({
            id: 5, title: 'An item', authors: 'Doe, J.', year: 2024, doi: '', journal: '',
            filename: 'x.pdf', abstract: '', notes: '', categories: [], custom_fields: [],
            collections: [
                { id: 1, name: 'Paper X', archived: false, note, added_at: '2026-10-08' },
                { id: 2, name: 'Old review', archived: true, note: '', added_at: '2026-10-01' },
            ],
        });
        const calls = installFetchMock([
            { method: 'GET', url: '/api/papers/5', reply: () => item() },
            { method: 'GET', url: /^\/api\/papers\/5\/references$/, reply: { count: 0, references: [] } },
            { method: 'GET', url: /^\/api\/papers\/5\/bibtex$/, reply: { key: 'Doe2024' } },
            {
                method: 'PATCH', url: '/api/collections/1/items/5',
                reply: ({ body }) => { note = body.note; return { collection_id: 1, paper_id: 5, note }; },
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        window.location.hash = '#/paper/5';
        window.dispatchEvent(new HashChangeEvent('hashchange'));

        const block = await vi.waitFor(() => {
            const el = q('[data-testid="detail-collections"]');
            if (!el || !el.querySelector('[data-collection-id="2"]')) throw new Error('not rendered');
            return el;
        });
        expect(block.querySelector('[data-collection-id="1"]').textContent).toContain('key method');
        expect(block.querySelector('[data-collection-id="2"]').textContent).toContain('Archived');

        block.querySelector('[data-collection-id="1"] [data-testid="detail-collection-note-edit"]').click();
        await flush();
        const input = q('[data-testid="detail-collection-note-input"]');
        input.value = 'the baseline';
        input.dispatchEvent(new Event('input'));
        await flush();
        q('[data-testid="detail-collection-note-save"]').click();
        await vi.waitFor(() => {
            if (!q('[data-collection-id="1"]').textContent.includes('the baseline')) throw new Error('note stale');
        });
        expect(calls.find((c) => c.method === 'PATCH').body).toEqual({ note: 'the baseline' });

        const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
        q('.lb-modal-actions button[aria-label="More"]').click();
        await flush();
        const del = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Delete');
        del.click();
        expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('It is in 2 collections'));
        expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
        confirmSpy.mockRestore();
    });
});
