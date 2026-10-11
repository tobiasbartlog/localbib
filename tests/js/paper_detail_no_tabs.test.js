// A stored layout without tabs (#230): no tab bar, no side panel, the main
// column takes the full width and carries every block that is not hidden.
// Own file - the first paint is read when app.js loads (helpers.js).
import { describe, it, expect, beforeAll } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const PAPER = {
    id: 94, title: 'No Tabs', authors: 'Kern, D.', year: 2026, doi: '', journal: '',
    filename: '', abstract: 'An abstract.', notes: 'hallo', categories: [], custom_fields: [],
};

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    window.LB_PREFS = {
        darkMode: '', thesisMode: '',
        detailLayout: { v: 1, main: ['abstract', 'notes', 'metadata'], tabs: [], hidden: ['references', 'annotations', 'customFields', 'categories', 'collections'] },
    };
    installFetchMock([
        { method: 'GET', url: /^\/api\/papers\/\d+$/, reply: { ...PAPER } },
        { method: 'GET', url: /^\/api\/papers\/\d+\/references$/, reply: { count: 0, references: [] } },
        { method: 'GET', url: /^\/api\/papers\/\d+\/bibtex$/, reply: { key: 'k2026' } },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    await flush();
});

describe('detail view without tabs', () => {
    it('has no tab bar and no side panel, and the main column holds the blocks', async () => {
        window.location.hash = '#/paper/94';
        window.dispatchEvent(new HashChangeEvent('hashchange'));
        await flush(); await flush(); await flush();

        expect(document.querySelector('[role="tablist"]')).toBeNull();
        expect(document.querySelector('[role="tabpanel"]')).toBeNull();
        expect(document.querySelector('.lb-modal-aside')).toBeNull();
        expect(document.querySelector('.lb-modal').classList.contains('has-tabs')).toBe(false);
        // Close stays in the head.
        expect(document.querySelector('.lb-head-side button[aria-label="Close"]')).toBeTruthy();

        const main = document.querySelector('[data-zone="main"]');
        expect(main.textContent).toContain('An abstract.');
        expect(main.querySelector('[data-testid="paper-notes"]')).toBeTruthy();
        expect(main.querySelector('[data-testid="block-metadata"]')).toBeTruthy();
        expect(document.querySelectorAll('[data-testid="paper-notes"]')).toHaveLength(1);
        expect(main.querySelector('[data-testid="annotations"]')).toBeNull();
    });
});
