// The core without any Add-on (#192): the item list, the item detail and the
// Research Chat render with no slot content and never probe an Add-on's API
// to find out whether it is there — the load list is the only signal.
import { describe, expect, it, vi } from 'vitest';

import { bootApp, flush } from './helpers.js';

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Smith, A.', year: 2020, cite_key: 'smith2020', categories: [], custom_fields: [], filename: '' },
];

function go(hash) {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
}

describe('Core without Add-ons', () => {
    it('shows no Add-on filter, aside or chat toggle and asks no Add-on API', async () => {
        history.replaceState(null, '', '#/');
        const calls = await bootApp([
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/papers', reply: PAPERS },
            { method: 'GET', url: '/api/papers/1', reply: PAPERS[0] },
            { method: 'GET', url: /^\/api\/papers\/\d+\/references/, reply: { references: [], count: 0 } },
            { method: 'GET', url: /^\/api\/research-chat\/chunk-status/, reply: { papers: [{ paper_id: 1, chunked: true, chunk_count: 2 }] } },
        ]);

        const filterButton = await vi.waitFor(() => {
            const b = document.querySelector('.lb-toolbar-l button');
            if (!b) throw new Error('item list not rendered');
            return b;
        });
        filterButton.click();
        await flush();
        const bar = document.querySelector('.lb-filter-grid');
        expect(bar).not.toBeNull();
        expect(bar.style.gridTemplateColumns).toBe('140px 140px 1fr 1fr 1fr 1fr');
        expect(bar.querySelectorAll('select').length).toBe(4);

        go('#/paper/1');
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="metadata-column"]')) throw new Error('detail not rendered');
        });
        await flush();
        expect(document.querySelector('[data-addon-slot]')).toBeNull();

        go('#/research-chat?paper_ids=1');
        await vi.waitFor(() => {
            if (!document.querySelector(`input[placeholder="${window.LB_I18N.en['chat.inputPlaceholder']}"]`)) {
                throw new Error('chat not rendered');
            }
        });
        await flush();
        expect(document.querySelector('[data-addon-slot]')).toBeNull();

        const probes = calls.filter((c) => /^\/api\/plugins\/[^/?]+\//.test(c.url));
        expect(probes).toEqual([]);
    });
});
