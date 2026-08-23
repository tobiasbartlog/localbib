// Smoke test for the two-phase PDF attach flow (book case): when attach-pdf
// reports a detected book, a trim preview modal appears and confirming it
// finalizes with the suggested chapter range (cover kept).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const PAPER = {
    id: 99, title: 'Some Chapter', authors: 'X', year: 2025,
    doi: '', filename: '', categories: [], custom_fields: [],
};

function selectFile() {
    const input = [...document.querySelectorAll('input[type=file]')]
        .find((el) => (el.getAttribute('accept') || '').includes('pdf'));
    const file = new File([new Uint8Array([1, 2, 3])], 'book.pdf', { type: 'application/pdf' });
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

describe('attach pdf two-phase flow (book)', () => {
    it('shows trim preview and finalizes with the chapter range', async () => {
        const book = {
            detected: true, page_count: 442, printed_start: '153', printed_end: '165',
            range: { start_page: 165, end_page: 177, keep_cover: true },
            chapter_pages: 13, method: 'toc+labels', confidence: 'high',
        };
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/papers/99', reply: PAPER },
            { method: 'GET', url: '/api/papers/99/references', reply: { references: [] } },
            { method: 'GET', url: '/api/papers/99/bibtex', reply: { key: 'x2025' } },
            { method: 'POST', url: /\/attach-pdf/, reply: { paper_id: 99, filename: 'x.pdf', book } },
            { method: 'POST', url: /\/attach-finalize/, reply: { paper_id: 99, trimmed_pages: 14, chunks: 0, categories: [] } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        window.location.hash = '#/paper/99';
        window.dispatchEvent(new HashChangeEvent('hashchange'));
        await flush(); await flush(); await flush();

        selectFile();
        await flush();
        uncheck('Text/OCR');
        uncheck('Referenzen');
        await flush();
        findByText('button', 'Starten').click();
        await flush(); await flush(); await flush();

        // Trim modal appears, nothing finalized yet.
        expect(findByText('h3', 'Buch erkannt')).toBeTruthy();
        expect(calls.some((c) => /attach-finalize/.test(c.url))).toBe(false);

        // Confirm trimming -> finalize with the suggested range.
        findByText('button', 'Zuschneiden').click();
        await flush(); await flush(); await flush();

        const fin = calls.find((c) => c.method === 'POST' && /attach-finalize/.test(c.url));
        expect(fin).toBeTruthy();
        expect(fin.body.trim).toEqual({ start_page: 165, end_page: 177, keep_cover: true });
    });
});
