// Settings -> Library (SPA-12): when the restarted app does not answer within
// about a minute, the base-folder dialog leaves its "restarting" phase for
// the manual one, with a close button - it never traps the page.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);

afterEach(() => { vi.useRealTimers(); });

describe('settings: base folder restart that never comes back', () => {
    it('switches to the manual phase with a close button', async () => {
        let down = false;
        await bootSettings([
            { method: 'GET', url: '/api/settings', reply: { LITERATUR_BASE_DIR: 'D:\\Lib' } },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            { method: 'POST', url: '/api/app/restart', reply: () => { down = true; return { status: 'restarting' }; } },
            { method: 'GET', url: '/api/stats', reply: () => (down ? { detail: 'down' } : {}), get status() { return down ? 503 : 200; } },
        ], 'library');

        q('[data-testid="settings-base-dir-change"]').click();
        await flush();
        typeInto(q('[data-testid="settings-base-dir-input"]'), 'E:\\NewLib');
        await flush();
        // Fake clock from the click on, so the minute of waiting passes at once.
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
        q('[data-testid="settings-base-dir-confirm"]').click();
        for (let i = 0; i < 20 && !q('[data-testid="settings-base-dir-restarting"]'); i++) {
            await vi.advanceTimersByTimeAsync(10);
        }
        expect(q('[data-testid="settings-base-dir-restarting"]')).toBeTruthy();
        await vi.advanceTimersByTimeAsync(70000);
        vi.useRealTimers();
        await flush();
        expect(q('[data-testid="settings-base-dir-restarting"]')).toBeNull();
        expect(q('[data-testid="settings-base-dir-manual"]')).toBeTruthy();
        const close = [...q('[data-testid="settings-base-dir-dialog"]').querySelectorAll('button')]
            .find((b) => b.textContent.trim() === 'Close');
        close.click();
        await flush();
        expect(q('[data-testid="settings-base-dir-dialog"]')).toBeNull();
    });
});
