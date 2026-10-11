// Item detail: the hierarchical category picker (handoff task 13). The "+"
// opens a filter over the category tree (indented by depth, assigned ones
// checked and disabled); a click assigns; a name no category has offers
// "Create “X”" under a chosen parent and assigns the new category at once;
// Enter assigns the first match or creates; Esc closes.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { defaultRoutes, flush, goTo, installFetchMock, typeInto } from './helpers.js';

const CATEGORIES = [
    { id: 1, name: 'Methods', parent_id: null },
    { id: 2, name: 'Surveys', parent_id: 1 },
    { id: 3, name: 'Theory', parent_id: null },
    { id: 4, name: 'Experiments', parent_id: 1 },
];
const PAPER = {
    id: 42, title: 'An item', authors: 'X', year: 2024, doi: '', filename: '',
    categories: [{ id: 3, name: 'Theory' }], custom_fields: [],
};

const q = (sel) => document.querySelector(sel);
const rowIds = () => [...document.querySelectorAll('[data-testid^="cat-picker-row-"]')]
    .map((el) => Number(el.getAttribute('data-testid').replace('cat-picker-row-', '')));

async function openPicker() {
    const plus = await vi.waitFor(() => {
        const el = q('button[title="Add category"]') || [...document.querySelectorAll('.lb-aside-block button')]
            .find((b) => b.textContent.trim() === '+');
        if (!el) throw new Error('detail not rendered');
        return el;
    });
    plus.click();
    await flush();
    return vi.waitFor(() => {
        const el = q('[data-testid="cat-picker"]');
        if (!el) throw new Error('picker not open');
        return el;
    });
}

function key(el, k) {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
}

describe('item detail: category picker', () => {
    it('shows the tree, assigns by click and Enter, creates under a parent, closes on Esc', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/papers/42', reply: PAPER },
            { method: 'GET', url: '/api/categories', reply: CATEGORIES },
            { method: 'GET', url: '/api/papers/42/references', reply: { references: [] } },
            { method: 'GET', url: '/api/papers/42/bibtex', reply: { key: 'x2024' } },
            { method: 'POST', url: '/api/papers/42/categories', reply: { status: 'ok' } },
            { method: 'POST', url: '/api/categories', reply: { id: 9, status: 'ok' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        goTo('#/paper/42');

        // --- the tree, indented, the assigned one checked and disabled -------
        let picker = await openPicker();
        expect(rowIds()).toEqual([1, 4, 2, 3]);
        expect(q('[data-testid="cat-picker-row-2"]').style.paddingLeft).toBe('22px');
        expect(q('[data-testid="cat-picker-row-1"]').style.paddingLeft).toBe('8px');
        expect(q('[data-testid="cat-picker-row-3"]').disabled).toBe(true);
        expect(q('[data-testid="cat-picker-row-3"]').textContent).toContain('✓');
        expect(document.activeElement).toBe(q('[data-testid="cat-picker-filter"]'));

        // --- a click assigns ------------------------------------------------
        q('[data-testid="cat-picker-row-4"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'POST' && c.url === '/api/papers/42/categories')) throw new Error('not assigned');
        });
        expect(calls.filter((c) => c.url === '/api/papers/42/categories').pop().body).toEqual({ category_id: 4 });
        await vi.waitFor(() => { if (q('[data-testid="cat-picker"]')) throw new Error('still open'); });

        // --- filter + Enter assigns the first match (ancestors as context) ---
        picker = await openPicker();
        typeInto(q('[data-testid="cat-picker-filter"]'), 'surv');
        await flush();
        expect(rowIds()).toEqual([1, 2]);
        expect(q('[data-testid="cat-picker-create-box"]')).toBeTruthy();  // no exact match
        key(q('[data-testid="cat-picker-filter"]'), 'Enter');
        await vi.waitFor(() => {
            const last = calls.filter((c) => c.url === '/api/papers/42/categories').pop();
            if (last.body.category_id !== 2) throw new Error('Enter did not assign');
        });
        await vi.waitFor(() => { if (q('[data-testid="cat-picker"]')) throw new Error('still open'); });

        // --- an exact name offers no create ---------------------------------
        picker = await openPicker();
        typeInto(q('[data-testid="cat-picker-filter"]'), 'methods');
        await flush();
        expect(q('[data-testid="cat-picker-create-box"]')).toBeNull();

        // --- a new name: create under a parent, assign at once --------------
        typeInto(q('[data-testid="cat-picker-filter"]'), 'Meta-analyses');
        await flush();
        expect(rowIds()).toEqual([]);
        const parent = q('[data-testid="cat-picker-parent"]');
        expect([...parent.options].map((o) => o.textContent.trim())).toEqual(
            ['— Top level —', 'Methods', 'Experiments', 'Surveys', 'Theory']);
        const treeLoads = calls.filter((c) => c.url === '/api/categories/tree').length;
        parent.value = '1';
        parent.dispatchEvent(new Event('change'));
        await flush();
        expect(q('[data-testid="cat-picker-create"]').textContent).toContain('Create “Meta-analyses”');
        key(q('[data-testid="cat-picker-filter"]'), 'Enter');
        await vi.waitFor(() => {
            const last = calls.filter((c) => c.url === '/api/papers/42/categories').pop();
            if (last.body.category_id !== 9) throw new Error('new category not assigned');
        });
        await vi.waitFor(() => { if (q('[data-testid="cat-picker"]')) throw new Error('still open'); });
        const created = calls.find((c) => c.method === 'POST' && c.url === '/api/categories');
        expect(created.body).toEqual({ name: 'Meta-analyses', parent_id: 1 });
        // The sidebar tree is fetched again.
        await vi.waitFor(() => {
            if (calls.filter((c) => c.url === '/api/categories/tree').length <= treeLoads) throw new Error('sidebar not refreshed');
        });

        // --- Esc closes -------------------------------------------------------
        picker = await openPicker();
        key(q('[data-testid="cat-picker-filter"]'), 'Escape');
        await flush();
        expect(q('[data-testid="cat-picker"]')).toBeNull();
        expect(picker).toBeTruthy();
    });
});
