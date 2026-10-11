// Settings -> Sources (SET-B9): the OpenAlex key never comes back to the
// page. The field stays empty and shows the server's hint; a typed key is
// saved and the field empties again; "Remove" clears the stored key — with
// exactly one PUT even while the input still holds typed text. A refused
// save keeps what was typed, and a failed refetch after a save keeps what
// the section already shows.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const puts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings');
const status = (key) => {
    const el = q(`[data-field-status="${key}"]`);
    return el ? el.getAttribute('data-status') : null;
};

// A real click on a button while an input has focus: the browser fires
// mousedown first and moves the focus (blurring the input) unless the
// handler prevents that default, then click.
function clickFrom(input, button) {
    const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
    button.dispatchEvent(down);
    if (!down.defaultPrevented) input.dispatchEvent(new Event('blur'));
    button.click();
}

describe('settings: the OpenAlex key is masked', () => {
    it('shows the hint, never the key, and can remove it', async () => {
        let putDown = false;
        let getDown = false;
        const calls = await bootSettings([
            {
                method: 'GET', url: '/api/settings',
                get status() { return getDown ? 500 : 200; },
                reply: () => (getDown
                    ? { detail: 'down' }
                    : { CROSSREF_MAILTO: 'me@example.com', OPENALEX_API_KEY: '', OPENALEX_API_KEY_HINT: 'oa-…abcd' }),
            },
            {
                method: 'PUT', url: '/api/settings',
                get status() { return putDown ? 500 : 200; },
                reply: () => (putDown ? { detail: 'down' } : { status: 'ok' }),
            },
        ], 'sources');
        q('[data-testid="source-head-openalex"]').click();
        await flush();
        const key = await vi.waitFor(() => {
            const el = q('#env-OPENALEX_API_KEY');
            if (!el || !el.placeholder.includes('oa-…abcd')) throw new Error('hint not shown');
            return el;
        });
        expect(key.value).toBe('');
        expect(key.placeholder).toContain('Saved (oa-…abcd)');
        const mailto = q('#env-CROSSREF_MAILTO');
        expect(mailto.value).toBe('me@example.com');

        // Leaving the empty field changes nothing.
        key.dispatchEvent(new Event('blur'));
        await flush();
        expect(puts(calls)).toHaveLength(0);

        typeInto(key, 'new-secret');
        key.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (!puts(calls).length) throw new Error('not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ OPENALEX_API_KEY: 'new-secret' });
        await vi.waitFor(() => { if (key.value !== '') throw new Error('key kept in the page'); });

        q('[data-testid="env-remove-OPENALEX_API_KEY"]').click();
        await vi.waitFor(() => {
            if (puts(calls).length < 2) throw new Error('not removed');
        });
        expect(puts(calls).at(-1).body).toEqual({ OPENALEX_API_KEY: '' });

        // --- Remove while text is typed: one PUT with '', no race with blur -
        await flush();
        let before = puts(calls).length;
        typeInto(key, 'half-typed');
        clickFrom(key, q('[data-testid="env-remove-OPENALEX_API_KEY"]'));
        await vi.waitFor(() => {
            if (puts(calls).length === before) throw new Error('not removed');
        });
        await flush();
        await flush();
        expect(puts(calls).slice(before)).toHaveLength(1);
        expect(puts(calls).at(-1).body).toEqual({ OPENALEX_API_KEY: '' });
        expect(key.value).toBe('');
        // The input kept its focus, so a later blur has nothing new to save.
        key.dispatchEvent(new Event('blur'));
        await flush();
        expect(puts(calls).slice(before)).toHaveLength(1);

        // --- a refused save keeps the typed key, the retry sends it again ----
        putDown = true;
        typeInto(key, 'kept-key');
        key.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (status('OPENALEX_API_KEY') !== 'error') throw new Error('no error shown');
        });
        expect(key.value).toBe('kept-key');
        putDown = false;
        before = puts(calls).length;
        key.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (status('OPENALEX_API_KEY') !== 'saved') throw new Error('retry not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ OPENALEX_API_KEY: 'kept-key' });
        expect(key.value).toBe('');

        // --- a failed refetch after a save keeps what the section shows ------
        await flush();
        const gets = () => calls.filter((c) => c.method === 'GET' && c.url === '/api/settings').length;
        const getsBefore = gets();
        getDown = true;
        typeInto(key, 'another-key');
        key.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (puts(calls).at(-1).body.OPENALEX_API_KEY !== 'another-key') throw new Error('not saved');
            if (gets() === getsBefore) throw new Error('no refetch yet');
        });
        await flush();
        await flush();
        expect(mailto.value).toBe('me@example.com');
        expect(key.placeholder).toContain('Saved (oa-…abcd)');
        expect(document.body.textContent).not.toContain('⟦');
    });
});
