// Smoke (#178): the first run ends on the migration offer.
//
// A new user meets an empty library right after onboarding, so the last thing
// the dialog does is ask where they are coming from. Picking a manager closes
// the dialog and drops them into the wizard with that card already chosen —
// the offer is a shortcut into /migrate, never a second configuration step.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const SOURCES = {
    sources: [
        { id: 'bibtex', label_key: 'migration.source.bibtex', kind: 'file', extensions: ['.bib'], result_kind: 'items' },
        { id: 'ris', label_key: 'migration.source.ris', kind: 'file', extensions: ['.ris'], result_kind: 'items' },
        { id: 'zotero_rdf', label_key: 'migration.source.zotero_rdf', kind: 'file', extensions: ['.rdf'], accepts_folder: true, result_kind: 'items' },
        { id: 'pdf_folder', label_key: 'migration.source.pdf_folder', kind: 'folder', extensions: ['.pdf'], result_kind: 'pdf_folder' },
    ],
};

describe('migration offer after onboarding', () => {
    it('follows Save with the offer and routes Zotero into the preselected wizard', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: {} },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            { method: 'GET', url: '/api/migration/sources', reply: SOURCES },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        // Page one is the configuration page and still ends on Save.
        expect(document.querySelector('[data-testid="onboarding-provider"]')).toBeTruthy();
        expect(document.querySelector('[data-testid="onboarding-migrate-zotero_rdf"]')).toBeFalsy();

        document.querySelector('[data-testid="onboarding-save"]').click();
        await flush(); await flush(); await flush();

        // ONBOARDING_COMPLETED is written by page one, not by the offer — the
        // offer can therefore never block or re-ask.
        const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/settings');
        expect(put.body.ONBOARDING_COMPLETED).toBe('true');

        // Page two: the dialog is still up, now asking about the old manager.
        const dialog = document.querySelector('[data-testid="onboarding-dialog"]');
        expect(dialog).toBeTruthy();
        expect(dialog.querySelector('[data-testid="onboarding-provider"]')).toBeFalsy();
        for (const id of ['zotero_rdf', 'citavi', 'mendeley', 'bibtex']) {
            expect(dialog.querySelector(`[data-testid="onboarding-migrate-${id}"]`)).toBeTruthy();
        }
        expect(dialog.querySelector('[data-testid="onboarding-migrate-later"]')).toBeTruthy();

        document.querySelector('[data-testid="onboarding-migrate-zotero_rdf"]').click();
        await flush(); await flush();

        // Dialog gone, wizard open, Zotero card already selected.
        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
        expect(window.location.hash).toContain('/migrate');
        expect(window.location.hash).toContain('source=zotero_rdf');
        const card = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="migrate-source-card"][data-card="zotero"]');
            if (!el || !el.classList.contains('is-selected')) throw new Error('Zotero card not preselected');
            return el;
        });
        expect(card.classList.contains('is-selected')).toBe(true);
    });
});
