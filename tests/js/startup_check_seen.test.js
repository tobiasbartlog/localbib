// Startup check (#209): a result already dismissed in this browser session
// (same checked_at — a reload, not a new server start) opens no popup.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush } from './helpers.js';
import { BROKEN_REPORT, CHECKED_AT, setUpRoutes } from './startup_check_fixtures.js';

describe('startup check: dismissed result', () => {
    it('stays away after a reload', async () => {
        sessionStorage.setItem('lb.startupCheck.seen', CHECKED_AT);
        const calls = await bootApp(setUpRoutes(BROKEN_REPORT));
        await vi.waitFor(() => {
            if (!calls.some((c) => c.url === '/api/llm/startup-check')) throw new Error('report not read');
        });
        await flush(); await flush();
        expect(document.querySelector('[data-testid="startup-check-dialog"]')).toBeNull();
    });
});
