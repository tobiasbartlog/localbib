// Smoke: without a configured embedding model the semantic-search toggle stays
// hidden and the Papers view behaves exactly as before (#101, regression case).
// A separate module instance from semantic_search_toggle_shown.test.js is
// needed on purpose -- app.js is a self-mounting ES module, so re-importing it
// within one test file would reuse the already-mounted instance instead of
// re-evaluating with a different /api/settings response.
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'Regular Result', authors: 'A. Author', year: 2024, doi: '10.1/x', journal: '', filename: 'a.pdf', categories: [], custom_fields: [] },
];

describe('semantic search toggle (hidden without embedding model)', () => {
    it('stays hidden and the regular list still renders (regression smoke)', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/settings', reply: {} }, // no LLM_EMBED_MODEL
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        expect(document.querySelector('[data-testid="semantic-toggle"]')).toBeFalsy();
        expect(findByText('h3', 'Regular Result')).toBeTruthy();
    });
});
