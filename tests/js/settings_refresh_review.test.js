// Pflege → Aktualisieren → Refresh mit Prüfung: die Vorschau schreibt nichts, der Dialog
// zeigt vorher/nachher, nichts ist vorab angekreuzt, Entfernungen sind rot
// und markieren ihren Eintrag, und gesendet wird nur, was angekreuzt ist.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, flush } from './helpers.js';

const ROWS = [
    { id: '1:category_add:3', paper_id: 1, paper_title: 'Paper One', kind: 'category_add',
      category_id: 3, before: null, after: 'New Cat', confidence: 0.8 },
    { id: '1:category_remove:2', paper_id: 1, paper_title: 'Paper One', kind: 'category_remove',
      category_id: 2, before: 'Old Cat', after: null, assigned_by: 'manual' },
    { id: '2:field:title', paper_id: 2, paper_title: 'Paper Two', kind: 'field', field: 'title',
      before: 'Paper Two', after: 'Paper Two, Corrected', source: 'crossref', clears: false },
];

const q = (sel) => document.querySelector(sel);

describe('settings: Refresh mit Prüfung', () => {
    it('zeigt die Prüfliste und übernimmt nur angekreuzte Zeilen', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: {} },
            {
                method: 'POST',
                url: '/api/maintenance/refresh-review/propose',
                stream: [
                    { type: 'progress', current: 1, total: 2, percent: 50, title: 'Paper One' },
                    { type: 'done', rows: ROWS, stats: { items: 2, errors: 0, no_text: 0 }, warnings: [] },
                ],
            },
            {
                method: 'POST',
                url: '/api/maintenance/refresh-review/apply',
                reply: ({ body }) => ({ applied: body.rows.length, skipped: [] }),
            },
        ], 'refresh');

        q('[data-testid="refresh-review-start"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="refresh-review-dialog"]')) throw new Error('Dialog fehlt');
        });
        await flush();

        // Nur der Kategorien-Bereich ist vorgewählt.
        const propose = calls.find((c) => c.url === '/api/maintenance/refresh-review/propose');
        expect(propose.body).toEqual({ scopes: ['categories'], only_gaps: true });

        // Nichts vorab angekreuzt.
        const apply = q('[data-testid="refresh-review-apply"]');
        expect(apply.disabled).toBe(true);

        // Der Eintrag mit Entfernung ist markiert, der andere nicht.
        expect(q('[data-paper="1"] [data-testid="refresh-review-removes"]')).toBeTruthy();
        expect(q('[data-paper="2"] [data-testid="refresh-review-removes"]')).toBeNull();
        const removal = q('[data-row="1:category_remove:2"]');
        expect(removal.className).toContain('bg-red-50');
        expect(removal.textContent).toContain('Old Cat');
        expect(removal.textContent).toContain('will be removed');
        expect(removal.textContent).toContain('assigned manually');

        // Vorher / nachher einer Metadaten-Zeile.
        const title = q('[data-row="2:field:title"]');
        expect(title.textContent).toContain('Paper Two');
        expect(title.textContent).toContain('Paper Two, Corrected');

        // Alle auswählen → alle drei, inkl. Warnhinweis zur Entfernung.
        const all = q('[data-testid="refresh-review-select-all"]');
        all.click();
        await flush();
        expect(apply.textContent).toContain('Apply 3 changes');
        expect(document.body.textContent).toContain('1 selected changes remove data.');

        // Die Entfernung wieder abwählen.
        removal.querySelector('input').click();
        await flush();
        expect(apply.textContent).toContain('Apply 2 changes');

        apply.click();
        await vi.waitFor(() => {
            if (!q('[data-testid="refresh-review-result"]')) throw new Error('Ergebnis fehlt');
        });
        const sent = calls.find((c) => c.url === '/api/maintenance/refresh-review/apply');
        expect(sent.body.rows.map((r) => r.id).sort()).toEqual(['1:category_add:3', '2:field:title']);
        expect(q('[data-testid="refresh-review-result"]').textContent).toContain('2 changes applied.');
    });
});
