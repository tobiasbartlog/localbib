// Settings -> Sources (#208): the email address on top saves on blur, the
// services are an accordion (all closed, one open at a time) and the optional
// OpenAlex key lives in the OpenAlex card with its own autosave.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const puts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings');
const open = () => ['crossref', 'openalex', 'datacite', 'openlibrary', 'arxiv'].filter((id) => q(`[data-testid="source-body-${id}"]`));

describe('settings: Sources', () => {
    it('saves the email, folds the services as an accordion and autosaves the OpenAlex key', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/settings', reply: { CROSSREF_MAILTO: 'me@example.org' } },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
        ], 'sources');

        // The email on top, saved on blur, with its status at the field.
        const mail = q('#env-CROSSREF_MAILTO');
        await vi.waitFor(() => {
            if (mail.value !== 'me@example.org') throw new Error('email not loaded');
        });
        expect(q('[data-settings-section="sources"]').textContent).toContain('only goes to these four services');
        typeInto(mail, 'new@example.org');
        mail.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (q('[data-field-status="CROSSREF_MAILTO"]').getAttribute('data-status') !== 'saved') throw new Error('not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ CROSSREF_MAILTO: 'new@example.org' });

        // Five cards, all closed, badges say which use the email - DataCite and
        // Open Library (the bibliographic lookup) among them.
        expect(['crossref', 'openalex', 'datacite', 'openlibrary', 'arxiv'].every((id) => q(`[data-testid="source-${id}"]`))).toBe(true);
        expect(open()).toEqual([]);
        expect(q('[data-testid="source-head-crossref"]').textContent).toContain('uses your email');
        expect(q('[data-testid="source-head-arxiv"]').textContent).toContain('freely accessible');
        expect(q('[data-testid="source-head-datacite"]').textContent).toContain('DataCite');
        expect(q('[data-testid="source-head-openlibrary"]').textContent).toContain('Open Library');
        expect(q('#env-OPENALEX_API_KEY')).toBeNull();

        // Only one open at a time.
        q('[data-testid="source-head-crossref"]').click();
        await flush();
        expect(open()).toEqual(['crossref']);
        expect(q('[data-testid="source-body-crossref"]').textContent).toContain('DOI matching on import');
        q('[data-testid="source-head-openalex"]').click();
        await flush();
        expect(open()).toEqual(['openalex']);

        // The key sits in the OpenAlex card, saves by itself, nothing mentions .env.
        const key = q('#env-OPENALEX_API_KEY');
        typeInto(key, 'sk-test');
        key.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (q('[data-field-status="OPENALEX_API_KEY"]').getAttribute('data-status') !== 'saved') throw new Error('key not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ OPENALEX_API_KEY: 'sk-test' });
        q('[data-testid="source-head-openalex"]').click();
        await flush();
        expect(open()).toEqual([]);
        expect(document.body.textContent).not.toContain('.env');
        expect(document.body.textContent).not.toContain('⟦');
    });
});
