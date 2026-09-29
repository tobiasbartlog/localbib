// Smoke (#140): an installation that already has an LLM connection is never
// asked again — the onboarding dialog is for fresh installs only. Whether the
// connection has a key is not the question (a local Ollama has none). Own file
// because app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('onboarding on a configured installation', () => {
    it('stays hidden when a connection with a ready reasoning role exists', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: {} },
            { method: 'GET', url: '/api/llm/status', reply: { reasoning: true, fast: true, embedding: false, connections: 1 } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
        // ...and a configured install is not told its LLM features are off.
        expect(document.querySelector('[data-testid="llm-off-hint"]')).toBeFalsy();
    });
});
