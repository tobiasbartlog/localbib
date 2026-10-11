// Upload into a collection (#203): the upload dialog carries the target
// collection (preselected when coming from a collection page), and a
// duplicate the library already had is reported as assigned too.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('upload into a collection', () => {
    it('sends the target collection with an upload and reports an assigned duplicate', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/import/pending', reply: { files: [], count: 0 } },
            { method: 'GET', url: /^\/api\/collections(\?.*)?$/, reply: [{ id: 4, name: 'Paper X', item_count: 1 }] },
            {
                method: 'POST', url: /^\/api\/import\/upload-smart\?/,
                stream: [
                    { type: 'progress', step: 'hash', message: 'import.step.hash', percent: 5 },
                    { type: 'error', message: 'import.step.duplicateFile', duplicate_paper_id: 7,
                      duplicate_title: 'Known item', collection: { id: 4, name: 'Paper X', added: true } },
                ],
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        window.location.hash = '#/import?collection=4';
        window.dispatchEvent(new HashChangeEvent('hashchange'));
        await vi.waitFor(() => {
            if (!q('[data-testid="import-bib-section"]')) throw new Error('import page not rendered');
        });

        window.dispatchEvent(new CustomEvent('global-pdf-drop', {
            detail: { files: [new File(['%PDF-1.4'], 'known.pdf', { type: 'application/pdf' })] },
        }));
        const select = await vi.waitFor(() => {
            const el = q('[data-testid="upload-target-select"]');
            if (!el || el.value !== '4') throw new Error('target not preselected');
            return el;
        });
        expect([...select.options].map((o) => o.value)).toEqual(['', '4', 'new']);
        q('[data-testid="upload-start"]').click();

        const line = await vi.waitFor(() => {
            const el = q('[data-testid="upload-result-collection"]');
            if (!el) throw new Error('no result');
            return el;
        });
        expect(line.textContent).toBe('Added to collection “Paper X”');
        const upload = calls.find((c) => c.method === 'POST' && c.url.startsWith('/api/import/upload-smart'));
        expect(upload.url).toContain('collection_id=4');
    });
});
