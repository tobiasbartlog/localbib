// Smoke (#140): the onboarding save path sends exactly the three answers.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('onboarding save', () => {
    it('persists provider, key and mailto through the settings endpoint', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: {} },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            { method: 'GET', url: '/api/llm/providers', reply: { providers: [{ id: 'openai', label: 'OpenAI' }] } },
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
        await flush(); await flush();

        const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/settings');
        expect(put.body).toMatchObject({
            LLM_PROVIDER: 'openai',
            LLM_API_KEY: 'sk-user-key',
            CROSSREF_MAILTO: 'me@uni.example',
            ONBOARDING_COMPLETED: 'true',
        });
        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
    });
});
