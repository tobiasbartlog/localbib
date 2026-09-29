// Settings -> LLM tab: roles on top (connection + model per role), the
// connection cards below, one Save for the whole tab (PUT /api/llm/config).
// Keys never come from the server (has_key/key_hint only); a card whose key
// was not touched sends api_key: null ("unchanged"). Loading a saved
// connection's models goes by id, so the key does not travel through the SPA.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, findByText, flush, typeInto } from './helpers.js';

const CONFIG = {
    connections: [
        { id: 'oai', label: 'OpenAI', provider: 'openai', base_url: '', has_key: true, key_hint: 'sk-…1234' },
        { id: 'local', label: 'Ollama', provider: 'custom', base_url: 'http://localhost:11434/v1', has_key: false, key_hint: '' },
    ],
    roles: {
        reasoning: { connection_id: 'oai', model: 'gpt-4o' },
        fast: { connection_id: 'local', model: 'llama3' },
        embedding: { connection_id: '', model: '' },
    },
    status: { reasoning: true, fast: true, embedding: false, connections: 2 },
};

describe('settings: LLM tab', () => {
    it('renders roles + connections, loads models by id and saves the document', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/llm/config', reply: CONFIG },
            {
                method: 'POST', url: '/api/llm/models',
                reply: ({ body }) => (body.connection_id === 'local'
                    ? { models: ['llama3', 'nomic-embed-text'], error: null }
                    : { models: ['gpt-4o', 'gpt-4o-mini'], error: null }),
            },
            { method: 'PUT', url: '/api/llm/config', reply: { ...CONFIG, status: { ...CONFIG.status, embedding: true } } },
        ], 'llm-tab');

        const panel = document.querySelector('[data-testid="llm-panel"]');
        expect(panel).toBeTruthy();

        // Three roles, each with the connection dropdown listing both connections.
        for (const tier of ['reasoning', 'fast', 'embedding']) {
            const row = panel.querySelector(`[data-testid="llm-role-${tier}"]`);
            expect(row).toBeTruthy();
            const options = [...row.querySelectorAll('select')[0].options].map((o) => o.value);
            expect(options).toEqual(['', 'oai', 'local']);
        }
        // Readiness comes from the server status, not from a client guess.
        expect(findByText('[data-testid="llm-role-reasoning"] span', 'ready')).toBeTruthy();
        expect(findByText('[data-testid="llm-role-embedding"] span', 'not ready')).toBeTruthy();

        // Connection cards: the key is masked, the keyless one says so.
        const oai = panel.querySelector('[data-testid="llm-connection-oai"]');
        const keyInput = oai.querySelector('[data-testid="llm-conn-api-key"]');
        expect(keyInput.value).toBe('');
        expect(keyInput.getAttribute('placeholder')).toContain('sk-…1234');
        const local = panel.querySelector('[data-testid="llm-connection-local"]');
        expect(local.querySelector('[data-testid="llm-conn-api-key"]').getAttribute('placeholder')).toContain('no key');

        // A bound connection cannot be removed; the hint names the roles.
        expect(oai.querySelector('[data-testid="llm-conn-remove"]').disabled).toBe(true);
        expect(findByText('[data-testid="llm-connection-oai"] span', 'Reasoning')).toBeTruthy();

        // Bind the embedding role to Ollama: the model list is fetched BY ID
        // (no key in the request) and the model dropdown lists embed models first.
        const embRow = panel.querySelector('[data-testid="llm-role-embedding"]');
        const connSelect = embRow.querySelectorAll('select')[0];
        connSelect.value = 'local';
        connSelect.dispatchEvent(new Event('change'));
        await vi.waitFor(() => {
            if (embRow.querySelectorAll('select').length < 2) throw new Error('model dropdown not rendered');
        });
        const probe = calls.find((c) => c.method === 'POST' && c.url === '/api/llm/models');
        expect(probe.body).toMatchObject({ connection_id: 'local' });
        expect(probe.body.api_key).toBeUndefined();
        const modelSelect = embRow.querySelectorAll('select')[1];
        const modelOptions = [...modelSelect.options].map((o) => o.value);
        expect(modelOptions).toEqual(['', 'nomic-embed-text', 'llama3']);
        modelSelect.value = 'nomic-embed-text';
        modelSelect.dispatchEvent(new Event('change'));
        await flush();

        // Save the whole tab: untouched keys go as null, the roles as edited.
        panel.querySelector('[data-testid="llm-save"]').click();
        await flush(); await flush();
        const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/llm/config');
        expect(put).toBeTruthy();
        expect(put.body.connections).toEqual([
            { id: 'oai', label: 'OpenAI', provider: 'openai', base_url: '', api_key: null },
            { id: 'local', label: 'Ollama', provider: 'custom', base_url: 'http://localhost:11434/v1', api_key: null },
        ]);
        expect(put.body.roles.embedding).toEqual({ connection_id: 'local', model: 'nomic-embed-text' });
        expect(put.body.roles.reasoning).toEqual({ connection_id: 'oai', model: 'gpt-4o' });
        // The response's status drives the badge.
        await flush();
        expect(findByText('[data-testid="llm-role-embedding"] span', 'ready')).toBeTruthy();

        // A new connection starts as a draft: its models are probed with the
        // typed key, and it can be removed while unbound.
        panel.querySelector('[data-testid="llm-add-connection"]').click();
        await flush();
        const cards = panel.querySelectorAll('[data-testid^="llm-connection-"]');
        expect(cards.length).toBe(3);
        const draft = cards[2];
        typeInto(draft.querySelector('[data-testid="llm-conn-api-key"]'), 'sk-new');
        draft.querySelector('[data-testid="llm-conn-load-models"]').click();
        await flush();
        const draftProbe = calls.filter((c) => c.method === 'POST' && c.url === '/api/llm/models').pop();
        expect(draftProbe.body).toMatchObject({ provider: 'openai', api_key: 'sk-new' });
        expect(draftProbe.body.connection_id).toBeUndefined();
        expect(draft.querySelector('[data-testid="llm-conn-remove"]').disabled).toBe(false);
    });
});
