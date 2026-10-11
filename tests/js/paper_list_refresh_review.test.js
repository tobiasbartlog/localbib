// Refresh mit Prüfung für ausgewählte Einträge: die Aktion im Auswahl-Menü
// der Liste öffnet denselben Prüfablauf, aber nur für die markierten IDs.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Doe, J.', year: 2021, doi: '10.1/a', filename: 'a.pdf', categories: [] },
    { id: 2, title: 'Second item', authors: 'Roe, R.', year: 2022, doi: '10.1/b', filename: 'b.pdf', categories: [] },
];

const q = (sel) => document.querySelector(sel);

describe('item list: Refresh mit Prüfung für die Auswahl', () => {
    it('schickt nur die ausgewählten IDs und lädt die Liste nach dem Übernehmen neu', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            {
                method: 'POST',
                url: '/api/maintenance/refresh-review/propose',
                stream: [{ type: 'done', warnings: [], stats: { items: 1, errors: 0, no_text: 0 }, rows: [
                    { id: '2:category_add:5', paper_id: 2, paper_title: 'Second item', kind: 'category_add',
                      category_id: 5, before: null, after: 'Fresh', confidence: 0.9 },
                ] }],
            },
            { method: 'POST', url: '/api/maintenance/refresh-review/apply', reply: { applied: 1, skipped: [] } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        const box = await vi.waitFor(() => {
            const el = document.querySelector('input[type="checkbox"][value="2"]');
            if (!el) throw new Error('list not rendered');
            return el;
        });
        box.click();
        await flush();

        findByText('button', 'Choose action').click();
        await flush();
        q('[data-testid="bulk-refresh-review"]').click();
        await flush();

        const panel = q('[data-testid="bulk-refresh-review-panel"]');
        expect(panel.textContent).toContain('For the 1 selected items.');
        panel.querySelector('[data-testid="refresh-review-start"]').click();

        await vi.waitFor(() => {
            if (!q('[data-testid="refresh-review-dialog"]')) throw new Error('dialog missing');
        });
        const propose = calls.find((c) => c.url === '/api/maintenance/refresh-review/propose');
        expect(propose.body).toEqual({ scopes: ['categories'], only_gaps: true, paper_ids: [2] });

        q('[data-testid="refresh-review-select-all"]').click();
        await flush();
        const before = calls.filter((c) => /^\/api\/papers\?/.test(c.url)).length;
        q('[data-testid="refresh-review-apply"]').click();
        await vi.waitFor(() => {
            const after = calls.filter((c) => /^\/api\/papers\?/.test(c.url)).length;
            if (after <= before) throw new Error('list not reloaded');
        });
    });
});
