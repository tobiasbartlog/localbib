// Smoke (#140): after skipping onboarding the app stays usable, but says so —
// a hint points at the settings instead of letting LLM features fail silently.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('LLM-off hint after skipping onboarding', () => {
    it('shows a hint linking to the settings, and no onboarding dialog', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        // Asked once, never again — neither the configuration page nor the
        // migration offer that follows it (#178) comes back on a reload.
        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
        expect(document.querySelector('[data-testid="onboarding-migrate-zotero_rdf"]')).toBeFalsy();

        const hint = document.querySelector('[data-testid="llm-off-hint"]');
        expect(hint).toBeTruthy();
        // Straight to the AI models section, by its address (#207).
        expect(hint.querySelector('a').getAttribute('href')).toBe('#/settings/ai-models');
    });
});
