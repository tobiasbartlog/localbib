// Regression: a paper whose extracted references include DOI-less entries must
// still render. The reference template falls back to refScholarUrl(ref) when a
// ref has a title but no DOI; that method used to collide with a same-named
// computed (paper-level scholarUrl), so Vue dropped the method and calling
// scholarUrl(ref) threw mid-render -> blank screen for exactly those papers.
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const PAPER = {
    id: 52, title: 'Choosing the Right Data Format', authors: 'Melekova, A.',
    year: 2026, doi: '', journal: '', filename: 'x.pdf', categories: [], custom_fields: [],
};
const REFS = {
    paper_id: 52, count: 2, references: [
        { id: 1, ref_index: 1, title: 'With a DOI', authors: 'A', year: 2021, doi: '10.1/x', matched_paper_id: null },
        { id: 2, ref_index: 2, title: 'No DOI here', authors: 'B', year: 2024, doi: '', matched_paper_id: null },
    ],
};

describe('paper detail render', () => {
    it('renders a paper whose references include DOI-less entries', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/papers/52', reply: PAPER },
            { method: 'GET', url: '/api/papers/52/references', reply: REFS },
            { method: 'GET', url: '/api/papers/52/bibtex', reply: { key: 'm2026' } },
            ...defaultRoutes(),
        ]);

        await import('../../static/app.js');
        await flush();
        window.location.hash = '#/paper/52';
        window.dispatchEvent(new HashChangeEvent('hashchange'));
        await flush(); await flush(); await flush();

        // The title renders (render did not abort) and the DOI-less ref offers a
        // Scholar fallback link built by refScholarUrl().
        expect(findByText('h1', 'Choosing the Right Data Format')).toBeTruthy();
        const scholar = [...document.querySelectorAll('a')]
            .find((a) => (a.getAttribute('href') || '').includes('scholar.google.com'));
        expect(scholar).toBeTruthy();
    });
});
