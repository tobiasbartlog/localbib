// Settings -> General -> Features (#214): "Start with Windows" exists only
// where GET /api/system/autostart says `available` (the Windows .exe); it
// saves itself with PUT /api/system/autostart and shows what the Windows
// entry holds afterwards. A refused switch goes back and says why.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, goTo } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const puts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/system/autostart');
const status = () => q('[data-field-status="autostart"]').getAttribute('data-status');

async function reopenGeneral() {
    goTo('#/settings/library');
    await vi.waitFor(() => {
        if (!q('[data-settings-section="library"]')) throw new Error('library not open');
    });
    goTo('#/settings/general');
    await vi.waitFor(() => {
        if (!q('[data-settings-section="general"]')) throw new Error('general not open');
    });
    await flush();
}

describe('settings: Start with Windows', () => {
    it('shows the switch only when available and saves it through the Windows entry', async () => {
        const state = { available: false, enabled: false };
        let refuse = false;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/system/autostart', reply: () => ({ ...state }) },
            {
                method: 'PUT', url: '/api/system/autostart',
                get status() { return refuse ? 409 : 200; },
                reply: ({ body }) => {
                    if (refuse) return { detail: { code: 'error.autostart_unavailable' } };
                    state.enabled = body.enabled;
                    return { ...state };
                },
            },
        ]);

        // --- source install: nothing to offer, nothing rendered --------------
        await flush();
        expect(calls.some((c) => c.method === 'GET' && c.url === '/api/system/autostart')).toBe(true);
        expect(q('[data-testid="settings-autostart"]')).toBeNull();
        expect(q('[data-testid="settings-thesis-mode"]')).toBeTruthy();

        // --- the Windows .exe: the switch shows the entry's real state -------
        state.available = true;
        await reopenGeneral();
        const sw = await vi.waitFor(() => {
            const el = q('[data-testid="settings-autostart"]');
            if (!el) throw new Error('switch not rendered');
            return el;
        });
        expect(sw.getAttribute('aria-checked')).toBe('false');
        expect(q('[data-testid="settings-autostart-row"]').textContent).toContain('Start with Windows');

        sw.click();
        await vi.waitFor(() => {
            if (status() !== 'saved') throw new Error('not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ enabled: true });
        expect(q('[data-testid="settings-autostart"]').getAttribute('aria-checked')).toBe('true');
        expect(calls.some((c) => c.method === 'PUT' && c.url === '/api/settings')).toBe(false);

        // --- a refused switch goes back and says why -------------------------
        refuse = true;
        q('[data-testid="settings-autostart"]').click();
        await vi.waitFor(() => {
            if (status() !== 'error') throw new Error('no error shown');
        });
        expect(puts(calls).at(-1).body).toEqual({ enabled: false });
        expect(q('[data-field-status="autostart"]').textContent).toContain('only available in the installed version');
        expect(q('[data-testid="settings-autostart"]').getAttribute('aria-checked')).toBe('true');
        expect(document.body.textContent).not.toContain('⟦');
    });
});
