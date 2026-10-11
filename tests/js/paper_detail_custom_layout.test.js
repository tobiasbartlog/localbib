// A stored detail layout (#230) arrives with the first paint (window.LB_PREFS,
// like light/dark) and decides what the detail view shows where: the order in
// the main column, a hidden block, a tab with an own name that is shown exactly
// as stored, and the number on a tab holding References and Annotations.
// Own file - the first paint is read when app.js loads (helpers.js).
import { describe, it, expect, beforeAll } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const PAPER = {
    id: 93, title: 'Custom Layout', authors: 'Kern, D.', year: 2026, doi: '', journal: '',
    filename: 'cl.pdf', abstract: 'An abstract.', notes: 'hallo', categories: [], custom_fields: [],
};
const ANN = (id) => ({
    id, kind: 'highlight', subtype: 'Highlight', page: 1, rect: { x: 0.1, y: 0.1, w: 0.4, h: 0.02 },
    color: '#FFEE58', quote: 'quoted ' + id, comment: null, replies: [], context: null, caption: null,
    author: 'x', created: '2026-10-09T20:39:18+02:00', modified: null, merged: 1,
    has_image: false, section: null,
});

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    window.LB_PREFS = {
        darkMode: '', thesisMode: '',
        detailLayout: {
            v: 1,
            main: ['notes', 'abstract'],
            tabs: [{ name: ' Lesen & Notieren ', blocks: ['annotations', 'references'] }],
            hidden: ['metadata', 'customFields', 'categories', 'collections'],
        },
    };
    installFetchMock([
        { method: 'GET', url: /^\/api\/papers\/\d+$/, reply: { ...PAPER } },
        { method: 'GET', url: /^\/api\/papers\/\d+\/references$/, reply: { count: 2, references: [1, 2].map((n) => ({ id: n, ref_index: n, title: 'R' + n })) } },
        { method: 'GET', url: /^\/api\/papers\/\d+\/bibtex$/, reply: { key: 'k2026' } },
        {
            method: 'GET', url: /^\/api\/papers\/\d+\/annotations$/,
            reply: { file_modified: '2026-10-09T18:40:00+00:00', pages: 3, multiple_authors: false, has_sections: false, annotations: [ANN('a'), ANN('b'), ANN('c')] },
        },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    await flush();
});

describe('detail view with a stored layout', () => {
    it('follows the stored order, hides what is hidden and names the tab as stored', async () => {
        window.location.hash = '#/paper/93';
        window.dispatchEvent(new HashChangeEvent('hashchange'));
        await flush(); await flush(); await flush();

        // Main column: Notes above the abstract.
        const main = document.querySelector('[data-zone="main"]');
        const notes = main.querySelector('[data-testid="paper-notes"]');
        const abstract = [...main.querySelectorAll('.lb-detail-section')].find((s) => s.textContent.includes('An abstract.'));
        expect(notes && abstract).toBeTruthy();
        expect(notes.compareDocumentPosition(abstract) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

        // One tab; its own name exactly as stored (no trimming, no translation),
        // a number adding References (2) and Annotations (3).
        const tabs = [...document.querySelectorAll('[role="tab"]')];
        expect(tabs).toHaveLength(1);
        expect(tabs[0].querySelector('.lb-tab-label').textContent).toBe(' Lesen & Notieren ');
        expect(tabs[0].querySelector('.lb-tab-count').textContent).toBe('5');
        expect(tabs[0].querySelector('.lb-tab-dot')).toBeNull();   // Notes are not in this tab
        const side = document.querySelector('[data-zone="tab0"]');
        expect(side.querySelector('[data-testid="annotations"]')).toBeTruthy();
        expect(side.textContent).toContain('R1');

        // Hidden blocks are not rendered anywhere.
        expect(document.querySelector('[data-testid="block-metadata"]')).toBeNull();
        expect(document.querySelector('[data-testid="block-categories"]')).toBeNull();
        expect(document.querySelector('[data-testid="detail-collections"]')).toBeNull();
        // Title and authors stay fixed on top of the main column.
        expect(main.querySelector('h1').textContent).toBe('Custom Layout');
    });
});
