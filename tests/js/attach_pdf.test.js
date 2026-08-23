// Smoke test for the two-phase PDF attach flow (non-book case):
//  1. file picked -> options modal (OCR / categories / chunks / references)
//  2. attach-pdf lands the file + detects whether it is a whole book
//  3. not a book -> attach-finalize runs the chosen steps
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const PAPER = {
    id: 99, title: 'Some Chapter', authors: 'X', year: 2025,
    doi: '', filename: '', categories: [], custom_fields: [],
};

function selectFile() {
    const input = [...document.querySelectorAll('input[type=file]')]
        .find((el) => (el.getAttribute('accept') || '').includes('pdf'));
    const file = new File([new Uint8Array([1, 2, 3])], 'paper.pdf', { type: 'application/pdf' });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change'));
}

function uncheck(label) {
    const modal = findByText('h3', 'PDF verarbeiten').closest('div');
    const box = [...modal.querySelectorAll('label')]
        .find((l) => l.textContent.includes(label))
        .querySelector('input[type=checkbox]');
    box.checked = false;
    box.dispatchEvent(new Event('change'));
}

describe('attach pdf two-phase flow (non-book)', () => {
    it('options -> attach-pdf(detect) -> attach-finalize with chosen steps', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/papers/99', reply: PAPER },
            { method: 'GET', url: '/api/papers/99/references', reply: { references: [] } },
            { method: 'GET', url: '/api/papers/99/bibtex', reply: { key: 'x2025' } },
            { method: 'POST', url: /\/attach-pdf/, reply: { paper_id: 99, filename: 'x.pdf', book: null } },
            { method: 'POST', url: /\/attach-finalize/, reply: { paper_id: 99, chunks: 0, categories: [] } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        window.location.hash = '#/paper/99';
        window.dispatchEvent(new HashChangeEvent('hashchange'));
        await flush(); await flush(); await flush();

        selectFile();
        await flush();
        expect(findByText('h3', 'PDF verarbeiten')).toBeTruthy();
        uncheck('Chunks');            // assert this is forwarded as false
        uncheck('Text/OCR');          // avoid the OCR follow-up call
        uncheck('Referenzen');        // avoid the references follow-up call
        await flush();

        findByText('button', 'Starten').click();
        await flush(); await flush(); await flush();

        const attach = calls.find((c) => c.method === 'POST' && /attach-pdf/.test(c.url));
        expect(attach.url).toContain('detect_book=true');

        const fin = calls.find((c) => c.method === 'POST' && /attach-finalize/.test(c.url));
        expect(fin).toBeTruthy();
        expect(fin.body.trim).toBe(null);
        expect(fin.body.do_categories).toBe(true);
        expect(fin.body.do_chunks).toBe(false);
    });
});
