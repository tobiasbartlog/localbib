// Smoke: with an embedding model configured, the semantic-search toggle shows
// up and switching it on routes the search through /api/search/semantic
// instead of /api/papers, rendering the scored hits in their own list (no
// silent merge into the regular, unscored list -- #101 acceptance criteria).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'Regular Result', authors: 'A. Author', year: 2024, doi: '10.1/x', journal: '', filename: 'a.pdf', categories: [], custom_fields: [] },
];

const SEMANTIC_RESPONSE = {
    mode: 'semantic',
    results: [
        { paper_id: 1, citekey: 'author2024', title: 'Semantic Hit', year: 2024, abstract_excerpt: 'Ein passender Auszug...', score: 0.873 },
    ],
};

describe('semantic search toggle (shown with embedding model)', () => {
    it('toggles between the regular list and /api/search/semantic results', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: /^\/api\/search\/semantic/, reply: SEMANTIC_RESPONSE },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/settings', reply: { LLM_EMBED_MODEL: 'qwen3-embedding-8b' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        const toggle = document.querySelector('[data-testid="semantic-toggle"]');
        expect(toggle).toBeTruthy();
        expect(toggle.classList.contains('is-on')).toBe(false);

        // Off by default: regular papers list renders, no semantic call made yet.
        expect(findByText('h3', 'Regular Result')).toBeTruthy();
        expect(calls.some((c) => /\/api\/search\/semantic/.test(c.url))).toBe(false);

        toggle.click();
        await flush();
        expect(toggle.classList.contains('is-on')).toBe(true);

        const searchInput = document.querySelector('.lb-search-input');
        searchInput.value = 'neural networks';
        searchInput.dispatchEvent(new Event('input'));
        await new Promise((r) => setTimeout(r, 350)); // debounce
        await flush(); await flush();

        const semanticCall = calls.find((c) => /\/api\/search\/semantic/.test(c.url));
        expect(semanticCall).toBeTruthy();
        expect(semanticCall.url).toContain('q=neural');

        const results = document.querySelector('[data-testid="semantic-results"]');
        expect(results).toBeTruthy();
        expect(findByText('h3', 'Semantic Hit')).toBeTruthy();
        expect(findByText('span', 'Score 0.873')).toBeTruthy();

        // Toggling back off returns to the regular, unscored list.
        toggle.click();
        await flush(); await flush();
        expect(toggle.classList.contains('is-on')).toBe(false);
        expect(document.querySelector('[data-testid="semantic-results"]')).toBeFalsy();
    });
});
