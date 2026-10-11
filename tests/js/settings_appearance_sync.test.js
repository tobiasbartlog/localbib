// Review round 2 of #235 (10): once a layout edit has been answered, a later
// sync adopts the server's layout again (a change made in another tab).
// Own file - one boot per file.
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const layoutPuts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings' && c.body.DETAIL_LAYOUT !== undefined);
const summary = () => q('[data-testid="layout-summary"]').textContent.trim();

const STANDARD = {
    v: 1,
    main: ['abstract', 'annotations'],
    tabs: [
        { name: '', blocks: ['metadata', 'customFields', 'categories', 'collections'] },
        { name: '', blocks: ['references'] },
        { name: '', blocks: ['notes'] },
    ],
    hidden: [],
};
window.LB_PREFS = { detailLayout: STANDARD };

describe('settings: Appearance, review round 2 of #235', () => {
    it('adopts the server layout in a sync that arrives after an edit has finished', async () => {
        let releaseSync = null;
        const elsewhere = { ...STANDARD, main: ['notes', 'abstract', 'annotations'], tabs: STANDARD.tabs.slice(0, 2) };
        const calls = await bootSettings([
            {
                method: 'GET', url: '/api/settings',
                reply: () => {
                    if (new Error().stack.includes('syncUiPrefs') && !releaseSync) return new Promise((resolve) => { releaseSync = () => resolve({ DETAIL_LAYOUT: JSON.stringify(elsewhere) }); });
                    return {};
                },
            },
            {
                method: 'PUT', url: '/api/settings',
                reply: ({ body }) => (body.DETAIL_LAYOUT === undefined ? { status: 'ok' } : { status: 'ok', detail_layout: JSON.parse(body.DETAIL_LAYOUT) }),
            },
        ], 'appearance');
        q('[data-testid="layout-toggle"]').click();
        await flush();
        q('[data-testid="layout-menu-references"]').click();
        await flush();
        q('[data-block="references"] [data-action="main"]').click();
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== 1) throw new Error('edit not sent');
        });
        await flush();
        expect(summary()).toContain('Main column: Abstract, Annotations, References');
        releaseSync();
        await flush(); await flush();
        expect(summary()).toContain('Main column: Notes, Abstract, Annotations');
    });
});
