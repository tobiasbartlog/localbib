// BibTeX handover (contract 2.6): `LocalBib.importBibtex(text, {collection,
// filename})` opens the import page, whose BibTeX dialog turns the text into
// the .bib file and previews it with the Collection preselected. Nothing is
// created before the user's click. The tests share one booted app and run in
// order.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, goTo } from './helpers.js';

const q = (sel) => document.querySelector(sel);

const BIB = '@article{Chen.2024,\n  title = {Life-cycle costs},\n  year = {2024}\n}\n';

// jsdom's File has no text(); a FileReader reads it.
function readFile(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsText(file);
    });
}

async function shows(sel) {
    return vi.waitFor(() => {
        const el = q(sel);
        if (!el) throw new Error('not shown: ' + sel);
        return el;
    });
}

describe('LocalBib.importBibtex', () => {
    let calls;
    const previews = () => calls.filter((c) => c.url === '/api/import/bibtex/preview');

    it('previews the handed-over text with the collection preselected and creates only on the click', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/import/pending', reply: { files: [], count: 0 } },
            { method: 'GET', url: /^\/api\/collections(\?.*)?$/, reply: [
                { id: 3, name: 'Other', item_count: 0 }, { id: 4, name: 'Kapitel 2', item_count: 1 }] },
            {
                method: 'POST', url: '/api/import/bibtex/preview',
                reply: { total: 1, cited_count: null, matched_count: 0, entries: [
                    { key: 'Chen.2024', title: 'Life-cycle costs', authors: '', year: 2024, doi: '', raw: {},
                      cited: null, match: null, entry_type: 'article' }] },
            },
            {
                method: 'POST', url: '/api/import/bibtex/commit',
                reply: ({ body }) => ({ total: 1, created: 1, matched: 0, errors: 0, enriched: 0,
                    results: [{ key: 'Chen.2024', status: 'created', paper_id: 9, cite_key: 'Chen.2024' }],
                    // Without a target the import keeps no Collection.
                    collection: body.collection_id
                        ? { id: 4, name: 'Kapitel 2', created: false, added_ids: [9], already_ids: [], not_cited: [] }
                        : null }),
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        goTo('#/');
        await flush();

        expect(typeof window.LocalBib.importBibtex).toBe('function');
        expect(window.LocalBib.importBibtex('   ', { collection: 4 })).toBe(false);
        expect(window.LocalBib.importBibtex(BIB, { collection: 4, filename: 'alt.bib' })).toBe(true);

        const summary = await shows('[data-testid="bib-import-summary"]');
        expect(window.location.hash).toBe('#/import');
        expect(summary.textContent).toContain('1 entries: 0 already in the library, 1 new');
        const [preview] = previews();
        const file = preview.body.get('bib_file');
        expect(file.name).toBe('alt.bib');
        expect(await readFile(file)).toBe(BIB);
        expect(preview.body.get('tex_file')).toBeNull();
        expect(q('[data-testid="bib-import"]').textContent).toContain('Kapitel 2');

        // A preview only: nothing is created until the click.
        await flush();
        expect(calls.some((c) => c.url === '/api/import/bibtex/commit')).toBe(false);
        q('[data-testid="bib-import-commit"]').click();
        await shows('[data-testid="bib-import-result"]');
        const commit = calls.find((c) => c.url === '/api/import/bibtex/commit');
        expect(commit.body.collection_id).toBe(4);
        expect(commit.body.source_name).toBe('alt.bib');
        expect(commit.body.entries.map((e) => e.key)).toEqual(['Chen.2024']);
    });

    it('without a collection the target is "no collection" and the text waits for the click', async () => {
        const before = previews().length;
        expect(window.LocalBib.importBibtex(BIB, { collection: null })).toBe(true);
        // The open import page takes it at once: a fresh dialog in its first step.
        const dialog = await vi.waitFor(() => {
            const el = q('[data-testid="bib-import"]');
            if (!el || !q('[data-testid="bib-import-preview"]')) throw new Error('dialog not ready');
            return el;
        });
        await flush();
        expect(previews().length).toBe(before);
        // A whole library's .bib goes into no Collection: that target is preselected.
        expect(dialog.querySelector('[data-testid="bib-import-target"]').value).toBe('none');
        // The handed-over text is the file: the preview button reads it.
        q('[data-testid="bib-import-preview"]').click();
        await shows('[data-testid="bib-import-summary"]');
        const file = previews().at(-1).body.get('bib_file');
        expect(file.name).toBe('import.bib');
        expect(await readFile(file)).toBe(BIB);
        expect(q('[data-testid="bib-import"]').textContent).toContain('no collection');

        q('[data-testid="bib-import-commit"]').click();
        const result = await shows('[data-testid="bib-import-result"]');
        const commit = calls.filter((c) => c.url === '/api/import/bibtex/commit').at(-1);
        expect('collection_id' in commit.body).toBe(false);
        expect('new_collection_name' in commit.body).toBe(false);
        expect(result.textContent).toContain('1 created, 0 already present');
        expect(result.textContent).not.toContain('⟦');
    });
});
