// Settings -> License & About (architecture review 2026-10-09, SPA-6): a
// failed update check says so. The message used to live inside the "update
// found" block, which a failed check never shows.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('settings: About update check', () => {
    it('shows the error of a failed check', async () => {
        let down = true;
        await bootSettings([
            { method: 'GET', url: '/api/system/info', reply: { version: '0.8.0', is_frozen: true, folders: {}, autostart_available: false } },
            { method: 'GET', url: '/api/license/status', reply: { activated: true } },
            {
                method: 'GET', url: '/api/version-check',
                reply: () => (down ? {} : { latest: '0.8.0', update_available: false }),
                get status() { return down ? 502 : 200; },
            },
        ], 'about');

        expect(q('[data-testid="about-update-check-error"]')).toBeNull();
        q('[data-testid="about-check-update"]').click();
        await vi.waitFor(() => { if (!q('[data-testid="about-update-check-error"]')) throw new Error('no error shown'); });
        expect(q('[data-testid="about-update-check-error"]').textContent.trim()).toBe('Request failed (HTTP 502)');
        expect(q('[data-testid="about-update-found"]')).toBeNull();

        // A later successful check clears it.
        down = false;
        q('[data-testid="about-check-update"]').click();
        await vi.waitFor(() => { if (!q('[data-testid="about-update-status"]').textContent.includes('up to date')) throw new Error('not checked'); });
        expect(q('[data-testid="about-update-check-error"]')).toBeNull();
    });
});
