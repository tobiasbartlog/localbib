// One DOI rule for the server and the SPA (#227): every vector of
// tests/fixtures/doi_vectors.json typed into the import page's DOI list shows
// as the DOI the intake's `normalize_doi` makes of it (tests/test_doi_vectors.py
// checks the server's half against the same file) — as a DOI or as "not a DOI".
import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { installFetchMock, defaultRoutes, flush, goTo, typeInto } from './helpers.js';

const { vectors } = JSON.parse(readFileSync(resolve(__dirname, '../fixtures/doi_vectors.json'), 'utf-8'));
const q = (sel) => document.querySelector(sel);
const rows = () => [...document.querySelectorAll('[data-testid="import-doi-row"]')].map((r) => [
    r.querySelector('[data-testid="import-doi-value"]').textContent.trim(), r.getAttribute('data-state')]);

describe('the DOI list reads DOIs like the server', () => {
    it('normalizes and judges every shared vector', async () => {
        expect(vectors.length).toBeGreaterThanOrEqual(10);
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/import/pending', reply: { files: [], count: 0 } },
            {
                method: 'POST', url: '/api/papers/by-doi/exists',
                reply: ({ body }) => ({ papers: Object.fromEntries(body.dois.map((d) => [d, null])) }),
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        goTo('#/import');
        const input = await vi.waitFor(() => {
            const el = q('[data-testid="import-doi-input"]');
            if (!el) throw new Error('import page not rendered');
            return el;
        });

        const seen = [];
        for (const v of vectors) {
            typeInto(input, v.input);
            await flush();
            q('[data-testid="import-doi-check"]').click();
            if (!v.normalized) {
                await flush();
                seen.push([v.input, rows()]);
                continue;
            }
            await vi.waitFor(() => {
                if (!q('[data-testid="import-doi-summary"]')) throw new Error('no preview for ' + v.input);
            });
            seen.push([v.input, rows()]);
        }
        expect(seen).toEqual(vectors.map((v) => [v.input,
            v.normalized ? [[v.normalized, v.is_doi ? 'new' : 'invalid']] : []]));
    });
});
