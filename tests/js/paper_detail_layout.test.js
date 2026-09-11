// Three-column paper detail (#161): PDF panel, content column and metadata
// column are three regions of one assembly. jsdom has no layout, so this is a
// structural smoke test — the three regions exist, the PDF actions sit in the
// panel's own header rather than the modal's top bar, and a paper without a
// file gets the drop zone instead of a viewer.
//
// The SPA self-mounts on import and vitest isolates module graphs per file, so
// both papers are visited through one boot by navigating the router.
import { describe, it, expect, beforeAll } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const WITH_PDF = {
    id: 71, title: 'Docked Panels', authors: 'Ritter, S.', year: 2026,
    doi: '10.1/dp', journal: '', filename: 'ritter-2026.pdf', notes: 'kurz',
    categories: [], custom_fields: [],
};
const NO_PDF = {
    id: 72, title: 'Nothing Attached', authors: 'Frey, L.', year: 2026,
    doi: '10.1/na', journal: '', filename: '', notes: 'kurz',
    categories: [], custom_fields: [],
};

const q = (testid) => document.querySelector(`[data-testid="${testid}"]`);

async function openPaper(paper) {
    window.location.hash = `#/paper/${paper.id}`;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await flush(); await flush(); await flush();
}

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    installFetchMock([
        { method: 'GET', url: '/api/papers/71', reply: WITH_PDF },
        { method: 'GET', url: '/api/papers/72', reply: NO_PDF },
        { method: 'GET', url: /^\/api\/papers\/7\d\/references$/, reply: { references: [] } },
        { method: 'GET', url: /^\/api\/papers\/7\d\/bibtex$/, reply: { key: 'x2026' } },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    await flush();
});

describe('paper detail layout', () => {
    it('renders the three regions and docks the PDF actions in the panel header', async () => {
        await openPaper(WITH_PDF);

        expect(findByText('h1', 'Docked Panels')).toBeTruthy();

        const panel = q('pdf-panel');
        const content = q('content-column');
        const meta = q('metadata-column');
        expect(panel).toBeTruthy();
        expect(content).toBeTruthy();
        expect(meta).toBeTruthy();

        // The panel is a sibling of the modal inside the assembly, not a
        // section inside the content column.
        const assembly = q('detail-assembly');
        expect(panel.parentElement).toBe(assembly);
        expect(content.contains(panel)).toBe(false);

        // Viewer, and the filename plus "PDF oeffnen" live in the panel header.
        const frame = q('pdf-frame');
        expect(frame).toBeTruthy();
        expect(frame.getAttribute('src')).toContain('/api/papers/71/pdf');
        expect(panel.textContent).toContain('ritter-2026.pdf');

        const openBtn = findByText('button', 'PDF oeffnen');
        expect(openBtn).toBeTruthy();
        expect(panel.contains(openBtn)).toBe(true);
        expect(document.querySelector('.lb-modal-head').contains(openBtn)).toBe(false);

        // The metadata edit form stays in the content column: opening it must
        // not move it into the narrow sidebar.
        document.querySelector('button[aria-label="Mehr"]').click();
        await flush();
        findByText('button', 'Bearbeiten').click();
        await flush();
        const form = findByText('h4', 'Metadaten bearbeiten');
        expect(form).toBeTruthy();
        expect(content.contains(form)).toBe(true);
    });

    it('offers attach, open access and Scholar in the drop zone when no PDF is attached', async () => {
        await openPaper(NO_PDF);

        expect(findByText('h1', 'Nothing Attached')).toBeTruthy();

        const panel = q('pdf-panel');
        expect(panel.className).toContain('is-empty');
        expect(q('pdf-frame')).toBeNull();

        // All three offers live in the drop zone itself — the panel header does
        // not carry a second copy of them.
        const drop = q('pdf-drop-zone');
        expect(drop).toBeTruthy();
        const dropButtons = [...drop.querySelectorAll('button')].map((b) => b.textContent.trim());
        expect(dropButtons).toContain('PDF anhaengen');
        expect(dropButtons).toContain('PDF aus Open Access');
        const scholar = [...drop.querySelectorAll('a')]
            .find((a) => (a.getAttribute('href') || '').includes('scholar.google.com'));
        expect(scholar).toBeTruthy();
        expect(panel.querySelector('.lb-pdf-head').textContent).not.toContain('PDF anhaengen');
    });

    it('swallows a click in the gap and still closes on the backdrop beside it', async () => {
        await openPaper(WITH_PDF);

        // The gap belongs to the assembly, so a click near the panels is not a
        // click on the backdrop.
        q('detail-assembly').dispatchEvent(new MouseEvent('click', { bubbles: true }));
        await flush();
        expect(window.location.hash).toBe('#/paper/71');
        expect(q('pdf-panel')).toBeTruthy();

        document.querySelector('.lb-modal-overlay')
            .dispatchEvent(new MouseEvent('click', { bubbles: true }));
        await flush(); await flush();
        expect(window.location.hash).not.toBe('#/paper/71');
    });
});
