// Startup check (#209): without any bound role the AI features are off on
// purpose (#140) — no popup.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush } from './helpers.js';
import { UNBOUND_REPORT, setUpRoutes } from './startup_check_fixtures.js';

describe('startup check: no bound role', () => {
    it('opens no popup', async () => {
        sessionStorage.clear();
        const calls = await bootApp(setUpRoutes(UNBOUND_REPORT));
        await vi.waitFor(() => {
            if (!calls.some((c) => c.url === '/api/llm/startup-check')) throw new Error('report not read');
        });
        await flush(); await flush();
        expect(document.querySelector('[data-testid="startup-check-dialog"]')).toBeNull();
    });
});
