// Smoke (#140): the onboarding save path writes the scalar answers through
// the settings endpoint and the chosen provider as ONE connection "default"
// bound to all three roles (models are picked later under Settings -> LLM).
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('onboarding save', () => {
    it('persists mailto via settings and the provider as a default connection', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: {} },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            {
                method: 'PUT', url: '/api/llm/config',
                reply: { connections: [], roles: {}, status: { reasoning: false, fast: false, embedding: false, connections: 1 } },
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        const select = document.querySelector('[data-testid="onboarding-provider"]');
        select.value = 'openai';
        select.dispatchEvent(new Event('change'));
        const key = document.querySelector('[data-testid="onboarding-api-key"]');
        key.value = 'sk-user-key';
        key.dispatchEvent(new Event('input'));
        const mailto = document.querySelector('[data-testid="onboarding-mailto"]');
        mailto.value = 'me@uni.example';
        mailto.dispatchEvent(new Event('input'));
        await flush();

        document.querySelector('[data-testid="onboarding-save"]').click();
        await flush(); await flush(); await flush();

        const settingsPut = calls.find((c) => c.method === 'PUT' && c.url === '/api/settings');
        expect(settingsPut.body).toMatchObject({
            CROSSREF_MAILTO: 'me@uni.example',
            ONBOARDING_COMPLETED: 'true',
        });
        // The key never travels through the flat settings any more.
        expect(settingsPut.body.LLM_API_KEY).toBeUndefined();

        const llmPut = calls.find((c) => c.method === 'PUT' && c.url === '/api/llm/config');
        expect(llmPut).toBeTruthy();
        expect(llmPut.body.connections).toEqual([
            { id: 'default', label: 'OpenAI', provider: 'openai', base_url: '', api_key: 'sk-user-key' },
        ]);
        for (const tier of ['reasoning', 'fast', 'embedding']) {
            expect(llmPut.body.roles[tier]).toEqual({ connection_id: 'default', model: '' });
        }
        // Saving ends the configuration page; the migration offer (#178) takes
        // its place and closes on "Not now".
        expect(document.querySelector('[data-testid="onboarding-provider"]')).toBeFalsy();
        document.querySelector('[data-testid="onboarding-migrate-later"]').click();
        await flush();
        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
        // No model chosen yet -> the app says the LLM features are still off.
        expect(document.querySelector('[data-testid="llm-off-hint"]')).toBeTruthy();
    });
});
