// Settings -> General (#208): every control saves itself with its own PUT and
// shows its own status; a refused save puts the display back and says why.
// The thesis area is a server setting shared with the sidebar entry (light or
// dark moved to Appearance, see settings_appearance.test.js); the logo in the
// sidebar is the fixed LocalBib mark, and the old icon endpoints are never called.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const puts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings');
const status = (key) => {
    const el = q(`[data-field-status="${key}"]`);
    return el ? el.getAttribute('data-status') : null;
};

describe('settings: General autosave', () => {
    it('saves each control on its own and reports at the field', async () => {
        let refuse = false;
        const calls = await bootSettings([
            {
                method: 'PUT', url: '/api/settings',
                get status() { return refuse ? 422 : 200; },
                reply: () => (refuse
                    ? { detail: { code: 'error.settings.invalidBoolean', params: {} } }
                    : { status: 'ok' }),
            },
        ]);

        // --- the sidebar mark is the fixed one, no custom icon is ever asked for
        expect(q('.lb-logo .lb-logo-mark')).toBeTruthy();
        expect(q('.lb-logo img')).toBeNull();
        expect(calls.some((c) => c.url.includes('/api/appearance'))).toBe(false);

        // --- language: English / German toggle, PUT UI_LANGUAGE only ---------
        q('[data-testid="settings-language-de"]').click();
        await vi.waitFor(() => {
            if (status('UI_LANGUAGE') !== 'saved') throw new Error('language not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ UI_LANGUAGE: 'de' });
        expect(q('[data-field-status="UI_LANGUAGE"]').textContent).toContain('Gespeichert ✓');
        q('[data-testid="settings-language-en"]').click();
        await vi.waitFor(() => {
            if (puts(calls).at(-1).body.UI_LANGUAGE !== 'en') throw new Error('not saved');
        });

        // --- thesis area: switch, PUT THESIS_MODE, sidebar entry appears ------
        expect(q('a[href="#/thesis"]')).toBeNull();
        q('[data-testid="settings-thesis-mode"]').click();
        await vi.waitFor(() => {
            if (status('thesisMode') !== 'saved') throw new Error('thesis not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ THESIS_MODE: 'true' });
        await vi.waitFor(() => {
            if (!q('a[href="#/thesis"]')) throw new Error('thesis entry missing');
        });

        // --- a refused save: back to the saved state, the reason at the field -
        refuse = true;
        q('[data-testid="settings-thesis-mode"]').click();
        await vi.waitFor(() => {
            if (status('thesisMode') !== 'error') throw new Error('no error shown');
        });
        expect(q('[data-field-status="thesisMode"]').textContent).toContain('on or off');
        expect(q('[data-testid="settings-thesis-mode"]').getAttribute('aria-checked')).toBe('true');
        expect(localStorage.getItem('thesisMode')).toBe('true');
        expect(q('a[href="#/thesis"]')).toBeTruthy();

        // Light or dark lives under Appearance now.
        expect(q('[data-testid="settings-theme-light"]')).toBeNull();

        // The old icon section is gone for good.
        expect(q('input[type="file"]')).toBeNull();
        expect(document.body.textContent).not.toContain('⟦');
        expect(calls.some((c) => c.url.includes('/api/appearance'))).toBe(false);
    });
});
