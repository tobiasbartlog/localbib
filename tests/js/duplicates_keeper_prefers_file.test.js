// Duplicates (LIB-B4 residual): the preselected keeper is an item whose PDF
// is really on disk, even when a file-less twin carries more metadata.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootApp, findByText, flush } from './helpers.js';

const GROUP = {
    reason: 'same DOI',
    papers: [
        { id: 1, title: 'Rich metadata', authors: 'A', doi: '10.1/x', filename: 'a.pdf', has_file: false },
        { id: 2, title: 'Has the PDF', authors: '', doi: '', filename: 'b.pdf', has_file: true },
    ],
};

describe('duplicates: keeper preselection', () => {
    it('prefers the item that has its file', async () => {
        await bootApp([
            { method: 'GET', url: '/api/duplicates', reply: { groups: [GROUP] } },
        ]);
        const btn = await vi.waitFor(() => {
            const el = findByText('button', 'Duplicates');
            if (!el) throw new Error('library not rendered');
            return el;
        });
        btn.click();
        await flush();
        const keptRow = await vi.waitFor(() => {
            const row = document.querySelector('.border-l-4.border-accent');
            if (!row) throw new Error('no keeper preselected');
            return row;
        });
        expect(keptRow.textContent).toContain('Has the PDF');
        expect(keptRow.textContent).not.toContain('Rich metadata');
    });
});
