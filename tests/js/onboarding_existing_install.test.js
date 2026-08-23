// Smoke (#140): an installation that already has an LLM key is never asked
// again — the onboarding dialog is for fresh installs only. Own file because
// app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('onboarding on a configured installation', () => {
    it('stays hidden when a key is already configured', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { LLM_PROVIDER: 'openai', LLM_API_KEY: 'sk-existing' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
        // ...and a configured install is not told its LLM features are off.
        expect(document.querySelector('[data-testid="llm-off-hint"]')).toBeFalsy();
    });
});
