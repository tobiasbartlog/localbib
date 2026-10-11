// Startup check (#209): every bound role works — or could not be checked
// because its provider has no model list, which is no alarm — so no popup.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush } from './helpers.js';
import { OK_REPORT, setUpRoutes } from './startup_check_fixtures.js';

describe('startup check: all fine', () => {
    it('opens no popup', async () => {
        sessionStorage.clear();
        const calls = await bootApp(setUpRoutes(OK_REPORT));
        await vi.waitFor(() => {
            if (!calls.some((c) => c.url === '/api/llm/startup-check')) throw new Error('report not read');
        });
        await flush(); await flush();
        expect(document.querySelector('[data-testid="startup-check-dialog"]')).toBeNull();
    });
});
