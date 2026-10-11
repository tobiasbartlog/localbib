// Dark mode and the thesis area live on the server (#208). A browser that held
// a value from before the update hands it over once - but only for what the
// server does not know yet; where the server has a value it wins and is
// mirrored back into the browser.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootApp } from './helpers.js';

const puts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings');

describe('server-side theme and thesis mode', () => {
    it('adopts the browser value once where the server has none and mirrors the server otherwise', async () => {
        // This browser used dark mode and the thesis area before the update.
        localStorage.setItem('darkMode', 'true');
        localStorage.setItem('thesisMode', 'true');
        // The server already holds THESIS_MODE=false, but nothing for DARK_MODE.
        const calls = await bootApp([
            { method: 'GET', url: '/api/settings', reply: { THESIS_MODE: 'false' } },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
        ]);

        await vi.waitFor(() => {
            if (!puts(calls).length) throw new Error('nothing handed over');
        });
        // Only the missing key travels, once.
        expect(puts(calls)).toHaveLength(1);
        expect(puts(calls)[0].body).toEqual({ DARK_MODE: 'true' });
        // The server wins for the other one: the browser follows.
        await vi.waitFor(() => {
            if (localStorage.getItem('thesisMode') !== 'false') throw new Error('mirror not updated');
        });
        expect(document.querySelector('a[href="#/thesis"]')).toBeNull();
        expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    });
});
