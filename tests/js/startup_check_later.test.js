// Startup check (#209): "Later" closes the popup and remembers the report's
// checked_at for this browser session — the app does not ask again, and the
// address does not change.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush, goTo, settingsSection } from './helpers.js';
import { BROKEN_REPORT, CHECKED_AT, setUpRoutes } from './startup_check_fixtures.js';

const q = (sel) => document.querySelector(sel);

describe('startup check: later', () => {
    it('hides the popup for this result', async () => {
        sessionStorage.clear();
        await bootApp(setUpRoutes(BROKEN_REPORT));
        await vi.waitFor(() => {
            if (!q('[data-testid="startup-check-dialog"]')) throw new Error('popup not shown');
        });
        const before = window.location.hash;

        q('[data-testid="startup-check-later"]').click();
        await flush();
        expect(q('[data-testid="startup-check-dialog"]')).toBeNull();
        expect(sessionStorage.getItem('lb.startupCheck.seen')).toBe(CHECKED_AT);
        expect(window.location.hash).toBe(before);

        // Moving around the app does not bring it back.
        goTo('#/settings/general');
        await vi.waitFor(() => {
            if (settingsSection() !== 'general') throw new Error('settings not open');
        });
        goTo('#/');
        await flush(); await flush();
        expect(q('[data-testid="startup-check-dialog"]')).toBeNull();
    });
});
