// The PDF-folder branch of the wizard (#176 + Slice 5).
//
// It brings files, not metadata: analyze answers a match table instead of item
// rows, the options step is skipped because there is nothing to decide, and the
// commit sends exactly the pairs the reader left ticked. The PDFs without an
// entry get the one-click hand-over to the smart import.
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { defaultRoutes, flush, installFetchMock, pluginNavRendered, typeInto } from './helpers.js';

const SOURCES = {
    sources: [
        { id: 'bibtex', label_key: 'migration.source.bibtex', kind: 'file', extensions: ['.bib'], result_kind: 'items' },
        { id: 'pdf_folder', label_key: 'migration.source.pdf_folder', kind: 'folder', extensions: ['.pdf'], result_kind: 'pdf_folder' },
    ],
};

const ANALYSIS = {
    kind: 'pdf_folder',
    source: 'pdf_folder',
    path: 'C:/old/pdfs',
    recursive: false,
    scanned: 3,
    total: 3,
    matched: 2,
    unmatched_count: 1,
    already_owned_count: 0,
    matches: [
        { pdf_path: 'C:/old/pdfs/a.pdf', pdf_name: 'a.pdf', paper_id: 11, paper_title: 'First item', strategy: 'doi', confidence: 1.0 },
        { pdf_path: 'C:/old/pdfs/b.pdf', pdf_name: 'b.pdf', paper_id: 12, paper_title: 'Second item', strategy: 'title', confidence: 0.91 },
    ],
    unmatched: [{ pdf_path: 'C:/old/pdfs/c.pdf', pdf_name: 'c.pdf' }],
    already_owned: [],
};

const COMMIT_FRAMES = [
    { type: 'progress', step: 'start', percent: 0, message: { code: 'migration.step.pdfStart', params: { total: 1 } } },
    {
        type: 'complete', run_id: 'run-pdf', created: 0, matched: 1, attached: 1, failed: 0, llm_failures: [],
        results: [{ key: 'C:/old/pdfs/a.pdf', title: 'a.pdf', status: 'attached', paper_id: 11, attached: true }],
    },
];

let calls;

function testid(id) {
    return document.querySelector(`[data-testid="${id}"]`);
}

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    calls = installFetchMock([
        { method: 'GET', url: '/api/migration/sources', reply: SOURCES },
        { method: 'POST', url: '/api/migration/analyze', reply: ANALYSIS },
        { method: 'POST', url: '/api/migration/commit', stream: COMMIT_FRAMES },
        { method: 'POST', url: '/api/migration/pdf-folder/import-unmatched', reply: { copied: [{ pdf_path: 'C:/old/pdfs/c.pdf', filename: 'c.pdf' }], failed: [], count: 1 } },
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

describe('migration wizard — PDF folder', () => {
    it('shows the match table, hands the unmatched PDFs to the smart import and commits only the ticked pairs', async () => {
        const folderCard = [...document.querySelectorAll('[data-testid="migrate-source-card"]')].find(
            (el) => el.getAttribute('data-card') === 'pdf_folder',
        );
        folderCard.click();
        await flush();
        // A folder source offers the subfolder switch; a file source does not.
        expect(testid('migrate-recursive')).toBeTruthy();

        typeInto(testid('migrate-path'), 'C:/old/pdfs');
        await flush();
        testid('migrate-analyze').click();
        await vi.waitFor(() => {
            if (!testid('migrate-step-analyze')) throw new Error('match table not rendered');
        });
        expect(testid('migrate-selected-count').textContent).toContain('2 of 2 selected');

        testid('migrate-import-unmatched').click();
        await vi.waitFor(() => {
            if (!testid('migrate-unmatched-done')) throw new Error('unmatched import not reported');
        });
        const handover = calls.find((c) => c.url === '/api/migration/pdf-folder/import-unmatched');
        expect(handover.body).toEqual({ paths: ['C:/old/pdfs/c.pdf'] });

        // Untick the fuzzy match; only the confirmed pair may be attached.
        const boxes = [...document.querySelectorAll('[data-testid="migrate-step-analyze"] input[type="checkbox"]')];
        boxes[1].click();
        await flush();

        // No options step for a folder of files — the table said everything.
        testid('migrate-continue').click();
        await vi.waitFor(() => {
            if (!testid('migrate-step-result')) throw new Error('result not rendered');
        });
        const commit = calls.find((c) => c.url === '/api/migration/commit');
        expect(commit.body).toEqual({
            source: 'pdf_folder',
            path: 'C:/old/pdfs',
            recursive: false,
            matches: [{ pdf_path: 'C:/old/pdfs/a.pdf', paper_id: 11 }],
        });
        expect(testid('migrate-refs-panel')).toBeNull();
    });
});
