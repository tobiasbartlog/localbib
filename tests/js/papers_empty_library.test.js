// Smoke (#178): the empty-library panel.
//
// Zero rows means one of two things, and the list must not confuse them: a
// search that matched nothing is a dead end to back out of, while a library
// with nothing in it yet is a beginning and gets the two ways in — import
// PDFs, or migrate from another manager. Own file (app.js self-mounts once per
// module graph, see helpers.js); the papers route answers statefully so all
// three states are reachable in one boot.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, typeInto } from './helpers.js';

const PAPER = {
    id: 1, title: 'A first item', authors: 'Doe, J.', year: 2021,
    journal: 'Journal', doi: '10.1/x', filename: 'a.pdf', categories: [],
};

describe('empty library panel', () => {
    it('offers import and migration only while the library itself is empty', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        let papers = [];
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?/, reply: () => papers },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        const panel = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="library-empty-panel"]');
            if (!el) throw new Error('empty-library panel not rendered');
            return el;
        });
        expect(panel.querySelector('[data-testid="library-empty-import"]').getAttribute('href')).toBe('#/import');
        expect(panel.querySelector('[data-testid="library-empty-migrate"]').getAttribute('href')).toBe('#/migrate');

        // A search that finds nothing is not an empty library.
        typeInto(document.querySelector('.lb-search-input'), 'nothing matches this');
        await vi.waitFor(() => {
            if (document.querySelector('[data-testid="library-empty-panel"]')) {
                throw new Error('panel still shown while searching');
            }
        }, { timeout: 2000 });

        // ...and neither is a library that has something in it.
        papers = [PAPER];
        document.querySelector('.lb-search-clear').click();
        await flush(); await flush();
        expect(document.querySelector('[data-testid="library-empty-panel"]')).toBeFalsy();
        expect(document.body.textContent).toContain('A first item');
    });
});
