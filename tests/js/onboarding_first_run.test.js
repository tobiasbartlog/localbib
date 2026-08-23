// Smoke (#140): on a fresh install the onboarding dialog appears, and
// skipping it records that we asked without configuring anything.
// app.js self-mounts once per test file, so the "already onboarded" case lives
// in its own file (onboarding_done.test.js).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('first-run onboarding', () => {
    it('shows the dialog and skipping persists ONBOARDING_COMPLETED', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            // Fresh install: nothing configured, onboarding never answered.
            { method: 'GET', url: '/api/settings', reply: {} },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        const dialog = document.querySelector('[data-testid="onboarding-dialog"]');
        expect(dialog).toBeTruthy();
        // The three things onboarding asks for.
        expect(dialog.querySelector('[data-testid="onboarding-provider"]')).toBeTruthy();
        expect(dialog.querySelector('[data-testid="onboarding-api-key"]')).toBeTruthy();
        expect(dialog.querySelector('[data-testid="onboarding-mailto"]')).toBeTruthy();

        document.querySelector('[data-testid="onboarding-skip"]').click();
        await flush(); await flush();

        const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/settings');
        expect(put).toBeTruthy();
        expect(put.body.ONBOARDING_COMPLETED).toBe('true');
        // Skipping configures nothing.
        expect(put.body.LLM_API_KEY).toBeFalsy();
        // Dialog is gone; the app underneath stays usable.
        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
    });
});
