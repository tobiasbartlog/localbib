// Settings -> AI models: roles as cards on top (connection + model + status
// label per role), the connections as a list below, one Save for the whole
// section (PUT /api/llm/config) in the bar that appears with the first change.
// Keys never come from the server (has_key/key_hint only); a connection whose
// key was not touched sends api_key: null ("unchanged"). Loading a saved
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

const status = (tier) => document.querySelector(`[data-testid="llm-role-status-${tier}"]`).getAttribute('data-status');

describe('settings: AI models', () => {
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
        ], 'ai-models');

        const panel = document.querySelector('[data-testid="llm-panel"]');
        expect(panel).toBeTruthy();

        // Three role cards, each with the connection dropdown listing both connections.
        for (const tier of ['reasoning', 'fast', 'embedding']) {
            const row = panel.querySelector(`[data-testid="llm-role-${tier}"]`);
            expect(row).toBeTruthy();
            const options = [...row.querySelectorAll('select')[0].options].map((o) => o.value);
            expect(options).toEqual(['', 'oai', 'local']);
        }
        // Readiness comes from the server status, not from a client guess.
        expect(status('reasoning')).toBe('ready');
        expect(status('embedding')).toBe('off');
        expect(findByText('[data-testid="llm-role-reasoning"] span', 'Ready')).toBeTruthy();
        expect(findByText('[data-testid="llm-role-embedding"] span', 'Off')).toBeTruthy();

        // The connection list: name, bound roles, address and key hint.
        const oai = panel.querySelector('[data-testid="llm-connection-oai"]');
        expect(oai.querySelector('[data-testid="llm-conn-summary"]').textContent).toContain('api.openai.com');
        expect(oai.querySelector('[data-testid="llm-conn-summary"]').textContent).toContain('sk-…1234');
        expect(findByText('[data-testid="llm-connection-oai"] span', 'Reasoning')).toBeTruthy();
        const local = panel.querySelector('[data-testid="llm-connection-local"]');
        expect(local.querySelector('[data-testid="llm-conn-summary"]').textContent)
            .toBe('http://localhost:11434/v1 · no key');
        // The form opens with "Edit": the key is masked, the keyless one says so.
        expect(oai.querySelector('[data-testid="llm-conn-api-key"]')).toBeNull();
        oai.querySelector('[data-testid="llm-conn-edit"]').click();
        local.querySelector('[data-testid="llm-conn-edit"]').click();
        await flush();
        const keyInput = oai.querySelector('[data-testid="llm-conn-api-key"]');
        expect(keyInput.value).toBe('');
        expect(keyInput.getAttribute('placeholder')).toContain('sk-…1234');
        expect(local.querySelector('[data-testid="llm-conn-api-key"]').getAttribute('placeholder')).toContain('no key');
        // A bound connection cannot be removed.
        expect(oai.querySelector('[data-testid="llm-conn-remove"]').disabled).toBe(true);

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
        expect(status('embedding')).toBe('changed');

        // Save the whole section: untouched keys go as null, the roles as edited.
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
        // The response's status drives the label.
        await flush();
        expect(status('embedding')).toBe('ready');

        // A new connection starts as a draft with its form open: its models
        // are probed with the typed key, and it can be removed while unbound.
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
        expect(draft.querySelector('[data-testid="llm-conn-summary"]').textContent).toContain('new key');
    });
});
