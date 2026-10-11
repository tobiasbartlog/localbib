// Settings -> Appearance (#231): light or dark moved here from General, and the
// detail layout is edited in a collapsed row. The ··· menu and dragging share
// one move rule; every change is one PUT of DETAIL_LAYOUT with the field status
// "Saved". Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const qa = (sel) => [...document.querySelectorAll(sel)];
const text = (sel) => (q(sel) ? q(sel).textContent.trim() : null);
const puts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings');
const layoutPuts = (calls) => puts(calls).filter((c) => c.body.DETAIL_LAYOUT !== undefined);
const lastLayout = (calls) => JSON.parse(layoutPuts(calls).at(-1).body.DETAIL_LAYOUT);
const status = () => {
    const el = q('[data-field-status="detailLayout"]');
    return el ? el.getAttribute('data-status') : null;
};

// Opens a block's ··· menu and clicks one of its entries.
async function menu(calls, block, action) {
    const before = layoutPuts(calls).length;
    q(`[data-testid="layout-menu-${block}"]`).click();
    await flush();
    const item = q(`[data-block="${block}"] [data-action="${action}"]`);
    if (!item) throw new Error(`no ${action} in the menu of ${block}`);
    item.click();
    await vi.waitFor(() => {
        if (layoutPuts(calls).length !== before + 1 || status() !== 'saved') throw new Error('layout not saved');
    });
    await flush();
}
const actions = (block) => qa(`[data-block="${block}"] [data-action]`).map((el) => el.getAttribute('data-action'));

describe('settings: Appearance', () => {
    it('edits the detail layout through the menu and by dragging, saving each change', async () => {
        let held = null;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/settings', reply: () => (held ? { DETAIL_LAYOUT: JSON.stringify(held) } : {}) },
            {
                method: 'PUT', url: '/api/settings',
                reply: ({ body }) => {
                    if (body.DETAIL_LAYOUT !== undefined) held = JSON.parse(body.DETAIL_LAYOUT);
                    return { status: 'ok' };
                },
            },
        ], 'appearance');

        // --- the section: after General, theme lives here and not in General ---
        expect(text('[data-testid="settings-head-title"]')).toBe('Appearance');
        expect(text('[data-testid="settings-head-group"]')).toBe('Settings');
        const nav = qa('[data-testid="settings-nav"] [data-testid^="settings-nav-"]').map((a) => a.getAttribute('data-testid'));
        expect(nav.slice(0, 2)).toEqual(['settings-nav-general', 'settings-nav-appearance']);
        expect(text('[data-testid="settings-theme-light"]')).toBe('Light');
        expect(q('[data-testid="settings-theme-light"]').getAttribute('aria-checked')).toBe('true');
        q('[data-testid="settings-theme-dark"]').click();
        await vi.waitFor(() => {
            if (!puts(calls).some((c) => c.body.DARK_MODE === 'true')) throw new Error('theme not saved');
        });
        expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

        // --- collapsed with a summary; Customize opens, Done closes ------------
        expect(q('[data-testid="layout-editor"]')).toBeNull();
        expect(text('[data-testid="layout-toggle-label"]')).toBe('Customize');
        expect(text('[data-testid="layout-summary"]'))
            .toBe('Main column: Abstract, Annotations · Tabs: Metadata, References, Notes');
        q('[data-testid="layout-toggle"]').click();
        await flush();
        expect(q('[data-testid="layout-editor"]')).toBeTruthy();
        expect(text('[data-testid="layout-toggle-label"]')).toBe('Done');
        expect(q('[data-testid="layout-toggle"]').getAttribute('aria-expanded')).toBe('true');
        expect(qa('[data-tab]').length).toBe(3);
        expect(qa('.lb-lay-hints li').length).toBe(4);

        // --- move through the menu: Notes to the main column; its tab is gone --
        q('[data-testid="layout-menu-notes"]').click();
        await flush();
        expect(actions('notes')).toEqual(['main', 'tab-0', 'tab-1', 'hide']);
        q('[data-testid="layout-menu-notes"]').click();
        await menu(calls, 'notes', 'main');
        expect(lastLayout(calls)).toEqual({
            v: 1,
            main: ['abstract', 'annotations', 'notes'],
            tabs: [
                { name: '', blocks: ['metadata', 'customFields', 'categories', 'collections'] },
                { name: '', blocks: ['references'] },
            ],
            hidden: [],
        });
        expect(qa('[data-tab]').length).toBe(2);
        expect(text('[data-testid="layout-summary"]'))
            .toBe('Main column: Abstract, Annotations, Notes · Tabs: Metadata, References');

        // --- rename: the name travels, the summary uses it ---------------------
        const name = q('[data-testid="layout-tabname-0"]');
        expect(name.getAttribute('placeholder')).toBe('Metadata');
        expect(name.getAttribute('maxlength')).toBe('24');
        typeInto(name, '  Facts  ');
        name.dispatchEvent(new Event('change'));
        await vi.waitFor(() => {
            if (lastLayout(calls).tabs[0].name !== 'Facts') throw new Error('rename not saved');
        });
        await flush();
        expect(text('[data-testid="layout-summary"]')).toContain('Tabs: Facts, References');

        // The name belongs to the tab: it stays when a block moves out of it.
        await menu(calls, 'metadata', 'tab-1');
        expect(lastLayout(calls).tabs).toEqual([
            { name: 'Facts', blocks: ['customFields', 'categories', 'collections'] },
            { name: '', blocks: ['references', 'metadata'] },
        ]);

        // --- up / down inside a zone, left / right in Hidden --------------------
        await menu(calls, 'annotations', 'up');
        expect(lastLayout(calls).main).toEqual(['annotations', 'abstract', 'notes']);

        // --- hide -------------------------------------------------------------------
        await menu(calls, 'abstract', 'hide');
        expect(lastLayout(calls).hidden).toEqual(['abstract']);
        expect(lastLayout(calls).main).toEqual(['annotations', 'notes']);
        expect(text('[data-testid="layout-summary"]')).toContain('Hidden: Abstract');
        q('[data-testid="layout-menu-abstract"]').click();
        await flush();
        expect(actions('abstract')).toEqual(['main', 'tab-0', 'tab-1', 'newtab']);
        q('[data-testid="layout-menu-abstract"]').click();
        await flush();

        // --- four tabs: no drop for a fifth, no "new tab" in the menu -----------
        await menu(calls, 'annotations', 'newtab');
        await menu(calls, 'notes', 'newtab');
        expect(lastLayout(calls).tabs.length).toBe(4);
        expect(q('[data-testid="layout-drop-newtab"]')).toBeNull();
        expect(text('[data-testid="layout-tabs-full"]')).toBe('Four tabs is the most that fits.');
        q('[data-testid="layout-menu-abstract"]').click();
        await flush();
        expect(actions('abstract')).toEqual(['main', 'tab-0', 'tab-1', 'tab-2', 'tab-3']);
        q('[data-testid="layout-menu-abstract"]').click();
        await flush();

        // --- reset to the default ---------------------------------------------------
        const resets = layoutPuts(calls).length;
        q('[data-testid="layout-reset"]').click();
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== resets + 1) throw new Error('reset not saved');
        });
        await flush();
        expect(lastLayout(calls)).toEqual({
            v: 1,
            main: ['abstract', 'annotations'],
            tabs: [
                { name: '', blocks: ['metadata', 'customFields', 'categories', 'collections'] },
                { name: '', blocks: ['references'] },
                { name: '', blocks: ['notes'] },
            ],
            hidden: [],
        });
        expect(q('[data-testid="layout-drop-newtab"]')).toBeTruthy();

        // --- dragging ends in the same rule: References dropped on "Hidden" ----
        const fire = (el, type) => el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
        const before = layoutPuts(calls).length;
        fire(q('[data-block="references"] .lb-lay-card'), 'dragstart');
        await flush();
        expect(q('[data-block="references"] .lb-lay-card').classList.contains('is-dim')).toBe(true);
        fire(q('[data-testid="layout-drop-hidden"]'), 'dragover');
        await flush();
        expect(q('[data-testid="layout-drop-hidden"]').classList.contains('is-over')).toBe(true);
        fire(q('[data-testid="layout-drop-hidden"]'), 'drop');
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== before + 1) throw new Error('drop not saved');
        });
        expect(lastLayout(calls).hidden).toEqual(['references']);
        expect(lastLayout(calls).tabs.length).toBe(2);

        // --- done closes the editor; nothing leaked a missing key ---------------
        q('[data-testid="layout-toggle"]').click();
        await flush();
        expect(q('[data-testid="layout-editor"]')).toBeNull();
        expect(document.body.textContent).not.toContain('⟦');
    });
});
