// Smoke test for the migration wizard (PRD #173, Slice 5).
//
// The wizard is the one path a reader takes when they leave another manager
// behind, and every step of it is a promise: the cards come from the registered
// adapters, the preview is read-only, the options are what the commit actually
// carries, and the result can be taken back. This drives the whole chain
// through the DOM — source card, path, analyze, options, the SSE run, the
// reference pass and undo — because a wizard that only works step by step is
// not a wizard.
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { defaultRoutes, findByText, flush, installFetchMock, pluginNavRendered, typeInto } from './helpers.js';

const SOURCES = {
    sources: [
        { id: 'bibtex', label_key: 'migration.source.bibtex', kind: 'file', extensions: ['.bib', '.bibtex'], result_kind: 'items' },
        { id: 'ris', label_key: 'migration.source.ris', kind: 'file', extensions: ['.ris', '.txt'], result_kind: 'items' },
        { id: 'zotero_rdf', label_key: 'migration.source.zotero_rdf', kind: 'file', extensions: ['.rdf'], accepts_folder: true, result_kind: 'items' },
        { id: 'pdf_folder', label_key: 'migration.source.pdf_folder', kind: 'folder', extensions: ['.pdf'], result_kind: 'pdf_folder' },
    ],
};

const ANALYSIS = {
    kind: 'items',
    source: 'bibtex',
    path: '/exports/library.bib',
    total: 3,
    matched: 1,
    new: 2,
    attachments_found: 2,
    attachments_missing: 1,
    collections: [['Methods', 'Surveys']],
    tags: ['to-read'],
    items: [
        { key: 'smith2020', title: 'Attention is all you need', year: 2020, attachments: ['/pdfs/a.pdf'], attachments_missing: [], matched_paper_id: null, match_strategy: 'none', matched_has_pdf: false },
        { key: 'jones2019', title: 'On graph layouts', year: 2019, attachments: ['/pdfs/b.pdf'], attachments_missing: [], matched_paper_id: null, match_strategy: 'none', matched_has_pdf: false },
        { key: 'doe2018', title: 'A known item', year: 2018, attachments: [], attachments_missing: ['/pdfs/gone.pdf'], matched_paper_id: 7, match_strategy: 'doi', matched_has_pdf: false },
    ],
};

const COMMIT_FRAMES = [
    { type: 'progress', step: 'start', percent: 0, message: { code: 'migration.step.start', params: { total: 3 } } },
    { type: 'progress', step: 'item', percent: 30, message: { code: 'migration.step.item', params: { index: 1, total: 3, title: 'Attention is all you need' } } },
    {
        type: 'complete',
        run_id: 'run-abc',
        created: 2,
        matched: 1,
        attached: 2,
        failed: 0,
        llm_failures: [],
        results: [
            { key: 'smith2020', title: 'Attention is all you need', status: 'created', paper_id: 101, attached: true },
            { key: 'jones2019', title: 'On graph layouts', status: 'created', paper_id: 102, attached: true },
            { key: 'doe2018', title: 'A known item', status: 'matched', paper_id: 7, attached: false },
        ],
    },
];

const REF_FRAMES = [
    { type: 'progress', step: 'start', percent: 0, message: '2 items' },
    { type: 'complete', processed: 2, total_references: 41, total_in_library: 5, results: [] },
];

let calls;

function card(id) {
    return [...document.querySelectorAll('[data-testid="migrate-source-card"]')].find(
        (el) => el.getAttribute('data-card') === id,
    );
}

function testid(id) {
    return document.querySelector(`[data-testid="${id}"]`);
}

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    calls = installFetchMock([
        { method: 'GET', url: '/api/migration/sources', reply: SOURCES },
        { method: 'POST', url: '/api/migration/analyze', reply: ANALYSIS },
        { method: 'POST', url: '/api/migration/commit', stream: COMMIT_FRAMES },
        { method: 'POST', url: '/api/papers/bulk-extract-references', stream: REF_FRAMES },
        { method: 'POST', url: '/api/migration/undo', reply: { run_id: 'run-abc', deleted: 2, created: 2, detached: 0, attached: 0 } },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    const link = await vi.waitFor(() => {
        const el = [...document.querySelectorAll('a')].find((a) => a.getAttribute('href') === '#/migrate');
        if (!el || !pluginNavRendered()) throw new Error('sidebar not ready');
        return el;
    });
    await flush();
    link.click();
    await vi.waitFor(() => {
        if (!testid('migrate-step-source')) throw new Error('wizard not rendered');
    });
    await flush();
});

describe('migration wizard', () => {
    it('renders one card per known source and greys out the ones without an adapter', () => {
        const cards = [...document.querySelectorAll('[data-testid="migrate-source-card"]')];
        expect(cards.map((el) => el.getAttribute('data-card'))).toEqual([
            'zotero', 'citavi', 'mendeley', 'bibtex', 'ris', 'pdf_folder',
        ]);
        // Registered adapters wear the server's label key; the two cards
        // without an adapter of their own carry their own.
        expect(card('bibtex').textContent).toContain('BibTeX (.bib)');
        expect(card('mendeley').textContent).toContain('Mendeley');
        // citavi has no adapter registered yet (Slice 3).
        expect(card('citavi').disabled).toBe(true);
        expect(card('citavi').textContent).toContain('Coming soon');
        expect(card('zotero').disabled).toBe(false);
    });

    it('walks source → preview → options → run → result and carries the ticked options into the commit', async () => {
        card('bibtex').click();
        await flush();
        typeInto(testid('migrate-path'), '/exports/library.bib');
        await flush();

        testid('migrate-analyze').click();
        await vi.waitFor(() => {
            if (!testid('migrate-step-analyze')) throw new Error('preview not rendered');
        });
        const analyzeCall = calls.find((c) => c.url === '/api/migration/analyze');
        expect(analyzeCall.body).toEqual({ source: 'bibtex', path: '/exports/library.bib', recursive: false });
        expect(findByText('div', 'Entries found')).toBeTruthy();
        expect(testid('migrate-selected-count').textContent).toContain('3 of 3 selected');

        // "new only" drops the one entry the library already has.
        testid('migrate-select-new').click();
        await flush();
        expect(testid('migrate-selected-count').textContent).toContain('2 of 3 selected');
        testid('migrate-select-all').click();
        await flush();

        testid('migrate-continue').click();
        await vi.waitFor(() => {
            if (!testid('migrate-step-options')) throw new Error('options not rendered');
        });

        // Defaults out of the box, then two deliberate changes.
        expect(testid('migrate-attach-all').checked).toBe(true);
        expect(testid('migrate-duplicates-attach').checked).toBe(true);
        expect(testid('migrate-refs-later').checked).toBe(true);
        expect(testid('migrate-opt-oa_fallback').checked).toBe(true);
        // No LLM connection in the default routes: the box stays off.
        expect(testid('migrate-opt-llm_categorize').checked).toBe(false);
        expect(testid('migrate-refs-cost').textContent).toContain('2 of the selected entries bring a PDF');

        testid('migrate-opt-import_notes').click();       // notes off
        testid('migrate-refs-all').click();               // references right after the run
        await flush();

        testid('migrate-start').click();
        await vi.waitFor(() => {
            if (!testid('migrate-step-result')) throw new Error('result not rendered');
        });

        const commit = calls.find((c) => c.url === '/api/migration/commit');
        expect(commit.body.source).toBe('bibtex');
        expect(commit.body.path).toBe('/exports/library.bib');
        expect(commit.body.selected_keys).toEqual(['smith2020', 'jones2019', 'doe2018']);
        expect(commit.body.attachment_keys).toBe(null);
        expect(commit.body.options).toEqual({
            import_abstract: true,
            import_notes: false,
            import_collections: true,
            import_tags: true,
            import_date_added: true,
            keep_cite_keys: true,
            fill_missing: true,
            llm_categorize: false,
            attach_pdfs: 'all',
            oa_fallback: true,
            duplicates: 'attach',
            extract_references: 'all',
        });

        expect(testid('migrate-result-created').textContent).toContain('2');
    });

    it('points the reference extraction at exactly the items of this run', async () => {
        await vi.waitFor(() => {
            if (!testid('migrate-refs-done')) throw new Error('reference pass not finished');
        });
        const refCall = calls.find((c) => c.url === '/api/papers/bulk-extract-references');
        // 101 and 102 got a PDF in this run; 7 was matched and has none.
        expect(refCall.body).toEqual({ paper_ids: [101, 102] });
        expect(testid('migrate-refs-done').textContent).toContain('41 references found');
    });

    it('offers the PDF-folder repair when the export named files it could not find', () => {
        expect(testid('migrate-match-folder')).toBeTruthy();
    });

    it('takes the whole run back through the undo endpoint', async () => {
        window.confirm = vi.fn(() => true);
        testid('migrate-undo').click();
        await vi.waitFor(() => {
            if (!testid('migrate-undone')) throw new Error('undo not reported');
        });
        const undo = calls.find((c) => c.url === '/api/migration/undo');
        expect(undo.body).toEqual({ run_id: 'run-abc' });
        expect(testid('migrate-undone').textContent).toContain('2');
    });
});
