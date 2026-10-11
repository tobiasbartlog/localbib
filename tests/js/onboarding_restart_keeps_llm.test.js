// "Restart setup" (#211) over a saved AI-model setup (architecture review
// 2026-10-09, SPA-1): the dialog's provider becomes the connection "default",
// and nothing else in llm.json is lost - other connections and their keys
// (`api_key: null` = unchanged), and every bound role with its model. A
// document it cannot read is never overwritten. The first run (empty
// document) is covered by onboarding_save.test.js.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);

const SAVED = {
    version: 1,
    connections: [
        { id: 'default', label: 'My OpenAI', provider: 'openai', base_url: '', has_key: true, key_hint: 'sk-…abcd' },
        { id: 'local1', label: 'Local', provider: 'custom', base_url: 'http://localhost:11434/v1', has_key: false, key_hint: '' },
    ],
    roles: {
        reasoning: { connection_id: 'local1', model: 'llama3' },
        fast: { connection_id: 'default', model: 'gpt-4o-mini' },
        embedding: { connection_id: '', model: '' },
    },
    status: { reasoning: true, fast: true, embedding: false, connections: 2 },
};

const KEPT_LOCAL = { id: 'local1', label: 'Local', provider: 'custom', base_url: 'http://localhost:11434/v1', api_key: null };

async function restartAndSave({ provider, baseUrl = '', key = '' }) {
    q('[data-testid="about-restart-setup"]').click();
    await vi.waitFor(() => { if (!q('[data-testid="onboarding-provider"]')) throw new Error('dialog not open'); });
    const select = q('[data-testid="onboarding-provider"]');
    select.value = provider;
    select.dispatchEvent(new Event('change'));
    await flush();
    if (baseUrl) typeInto(q('[data-testid="onboarding-base-url"]'), baseUrl);
    if (key) typeInto(q('[data-testid="onboarding-api-key"]'), key);
    await flush();
    q('[data-testid="onboarding-save"]').click();
    await vi.waitFor(() => { if (!q('[data-testid="onboarding-migrate-later"]')) throw new Error('not saved'); });
    q('[data-testid="onboarding-migrate-later"]').click();
    await flush();
}

describe('restart setup keeps the AI-model setup', () => {
    it('merges the dialog provider into the saved document', async () => {
        let configDown = false;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/system/info', reply: { version: '0.8.0', is_frozen: false, folders: {}, autostart_available: false } },
            { method: 'GET', url: '/api/license/status', reply: { activated: true } },
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            {
                method: 'GET', url: '/api/llm/config',
                reply: () => (configDown ? { detail: 'boom' } : SAVED),
                get status() { return configDown ? 500 : 200; },
            },
            { method: 'PUT', url: '/api/llm/config', reply: SAVED },
        ], 'about');
        const llmPuts = () => calls.filter((c) => c.method === 'PUT' && c.url === '/api/llm/config');

        // --- same provider, no key typed: the stored key, label and every
        // binding stay; only the unbound role points at "default" ---------------
        await restartAndSave({ provider: 'openai' });
        expect(llmPuts()).toHaveLength(1);
        expect(llmPuts()[0].body).toEqual({
            connections: [
                { id: 'default', label: 'My OpenAI', provider: 'openai', base_url: '', api_key: null },
                KEPT_LOCAL,
            ],
            roles: {
                reasoning: { connection_id: 'local1', model: 'llama3' },
                fast: { connection_id: 'default', model: 'gpt-4o-mini' },
                embedding: { connection_id: 'default', model: '' },
            },
        });

        // --- another provider: "default" is replaced, its old key is not sent
        // on to the new host; the other connection still survives --------------
        await restartAndSave({ provider: 'custom', baseUrl: 'http://lmstudio:1234/v1' });
        expect(llmPuts()).toHaveLength(2);
        const second = llmPuts()[1].body;
        expect(second.connections).toEqual([
            { id: 'default', label: 'Custom (OpenAI-compatible)', provider: 'custom', base_url: 'http://lmstudio:1234/v1', api_key: '' },
            KEPT_LOCAL,
        ]);
        expect(second.roles.reasoning).toEqual({ connection_id: 'local1', model: 'llama3' });
        // the old provider's model means nothing to the new one
        expect(second.roles.fast).toEqual({ connection_id: 'default', model: '' });

        // --- a typed key replaces the stored one --------------------------------
        await restartAndSave({ provider: 'openai', key: 'sk-new' });
        expect(llmPuts()[2].body.connections[0]).toEqual(
            { id: 'default', label: 'My OpenAI', provider: 'openai', base_url: '', api_key: 'sk-new' });

        expect(q('[data-testid="llm-save-error-hint"]')).toBeNull();

        // --- the saved document cannot be read: nothing is written over it -----
        configDown = true;
        await restartAndSave({ provider: 'openai', key: 'sk-other' });
        expect(llmPuts()).toHaveLength(3);
        // ... and the user hears that saving failed, not that AI is off: the
        // old llm.json still stands (SPA-1 feedback).
        const hint = await vi.waitFor(() => {
            const el = q('[data-testid="llm-save-error-hint"]');
            if (!el) throw new Error('no save error shown');
            return el;
        });
        expect(hint.textContent).toContain('The AI setup could not be saved');
        expect(q('[data-testid="llm-off-hint"]')).toBeNull();
    });
});
