// Review of #235 on the layout editor: a card dropped on a tab name does not
// become its text (6), a slow answer never undoes a newer edit (7), and the
// default and limits come from the server (14). Own file - one boot per file.
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const qa = (sel) => [...document.querySelectorAll(sel)];
const layoutPuts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings' && c.body.DETAIL_LAYOUT !== undefined);
const summary = () => q('[data-testid="layout-summary"]').textContent.trim();

// What the server's first paint says (detail_layout.rules()): a smaller name
// limit and a default of its own, so nothing here can come from a JS constant.
const SERVER_DEFAULT = {
    v: 1,
    main: ['abstract', 'annotations', 'notes'],
    tabs: [{ name: '', blocks: ['metadata', 'customFields', 'categories', 'collections', 'references'] }],
    hidden: [],
};
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
window.LB_PREFS = { detailLayout: STANDARD, detailLayoutRules: { default: SERVER_DEFAULT, max_tabs: 4, max_name: 5 } };

async function menuClick(block, action) {
    q(`[data-testid="layout-menu-${block}"]`).click();
    await flush();
    q(`[data-block="${block}"] [data-action="${action}"]`).click();
    await flush();
}

describe('settings: Appearance, review of #235', () => {
    it('ignores a card dropped on a tab name, counts the name in code points, uses the served rules and keeps the newest edit', async () => {
        let release = null;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/settings', reply: {} },
            {
                method: 'PUT', url: '/api/settings',
                reply: ({ body }) => {
                    if (body.DETAIL_LAYOUT === undefined) return { status: 'ok' };
                    const sent = JSON.parse(body.DETAIL_LAYOUT);
                    // The first edit's answer is held back (a slow server).
                    if (sent.main.includes('notes') && layoutPuts(calls).length === 2) {
                        return new Promise((resolve) => { release = () => resolve({ status: 'ok', detail_layout: sent }); });
                    }
                    return { status: 'ok', detail_layout: sent };
                },
            },
        ], 'appearance');
        const getsAtStart = calls.filter((c) => c.method === 'GET' && c.url === '/api/settings').length;
        q('[data-testid="layout-toggle"]').click();
        await flush();

        // --- 14: the limit is the served one --------------------------------------
        const name = q('[data-testid="layout-tabname-0"]');
        expect(name.getAttribute('maxlength')).toBe('5');

        // --- 6: dropping a card on the name field is swallowed ----------------------
        const drop = new Event('drop', { bubbles: true, cancelable: true });
        drop.dataTransfer = { getData: () => 'customFields', types: ['text/plain'] };
        name.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
        expect(name.dispatchEvent(drop)).toBe(false);   // default prevented: no text lands in the field
        await flush();
        expect(layoutPuts(calls).length).toBe(0);

        // --- 14: code points, not UTF-16 units ---------------------------------------
        typeInto(name, '\u{1F600}\u{1F601}\u{1F602}\u{1F603}\u{1F604}\u{1F605}\u{1F606}');
        name.dispatchEvent(new Event('change'));
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== 1) throw new Error('rename not saved');
        });
        expect(Array.from(JSON.parse(layoutPuts(calls)[0].body.DETAIL_LAYOUT).tabs[0].name).length).toBe(5);
        expect(name.value).toBe(Array.from('\u{1F600}\u{1F601}\u{1F602}\u{1F603}\u{1F604}').join(''));

        // --- 7: a stale answer never undoes the newer edit ---------------------------
        await menuClick('notes', 'main');   // edit 1, its answer is held back
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== 2) throw new Error('edit 1 not sent');
        });
        await menuClick('annotations', 'up');   // edit 2, answered at once
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== 3) throw new Error('edit 2 not sent');
        });
        await flush();
        const mine = 'Main column: Annotations, Abstract, Notes';
        expect(summary()).toContain(mine);
        release();   // edit 1's (now stale) answer arrives last
        await flush(); await flush();
        expect(summary()).toContain(mine);
        // and the next edit is computed from what is on screen, not from the stale answer
        await menuClick('annotations', 'down');
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== 4) throw new Error('edit 3 not sent');
        });
        expect(JSON.parse(layoutPuts(calls)[3].body.DETAIL_LAYOUT).main).toEqual(['abstract', 'annotations', 'notes']);
        // no GET /api/settings after the saves (the answer carries the layout)
        expect(calls.filter((c) => c.method === 'GET' && c.url === '/api/settings').length).toBe(getsAtStart);

        // --- 14: reset goes to the server's default -----------------------------------
        q('[data-testid="layout-reset"]').click();
        await vi.waitFor(() => {
            if (layoutPuts(calls).length !== 5) throw new Error('reset not saved');
        });
        expect(JSON.parse(layoutPuts(calls)[4].body.DETAIL_LAYOUT)).toEqual(SERVER_DEFAULT);
        expect(qa('[data-tab]').length).toBe(1);
    });
});
