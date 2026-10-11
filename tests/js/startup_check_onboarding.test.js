// Startup check (#209): never during the setup. While the first-run dialog is
// open, the popup does not cover it.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush } from './helpers.js';
import { BROKEN_REPORT } from './startup_check_fixtures.js';

describe('startup check: during the onboarding', () => {
    it('stays hidden behind the setup dialog', async () => {
        sessionStorage.clear();
        // Fresh install: settings without ONBOARDING_COMPLETED, no connection.
        const calls = await bootApp([
            { method: 'GET', url: '/api/llm/startup-check', reply: BROKEN_REPORT },
        ]);
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="onboarding-dialog"]')) throw new Error('onboarding not shown');
            if (!calls.some((c) => c.url === '/api/llm/startup-check')) throw new Error('report not read');
        });
        await flush(); await flush();
        expect(document.querySelector('[data-testid="startup-check-dialog"]')).toBeNull();
    });
});
