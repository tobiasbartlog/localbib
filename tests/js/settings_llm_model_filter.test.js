// Settings -> LLM tab: a connection with a long model list (OpenRouter has
// hundreds) gets a combobox instead of the native dropdown: typing filters
// AND opens the list at once (a native <select> cannot be opened by script),
// a click picks, Escape closes. Every word must match, case-insensitively; the
// bound model never drops out of the list. A short list keeps the native select.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, flush, typeInto } from './helpers.js';

const MANY = [
    'anthropic/claude-sonnet-4', 'anthropic/claude-haiku-4', 'openai/gpt-4o', 'openai/gpt-4o-mini',
    'google/gemini-2.5-pro', 'google/gemini-2.5-flash', 'meta-llama/llama-3.3-70b', 'mistralai/mistral-large',
    'deepseek/deepseek-r1', 'qwen/qwen3-235b', 'x-ai/grok-4', 'cohere/command-r-plus',
    'openai/text-embedding-3-small', 'perplexity/sonar',
];

const CONFIG = {
    connections: [
        { id: 'or', label: 'OpenRouter', provider: 'openrouter', base_url: '', has_key: true, key_hint: 'sk-…1234' },
        { id: 'local', label: 'Ollama', provider: 'custom', base_url: 'http://localhost:11434/v1', has_key: false, key_hint: '' },
    ],
    roles: {
        reasoning: { connection_id: 'or', model: 'x-ai/grok-4' },
        fast: { connection_id: '', model: '' },
        embedding: { connection_id: '', model: '' },
    },
    status: { reasoning: true, fast: false, embedding: false, connections: 2 },
};

const optionTexts = (row) => [...row.querySelectorAll('[data-testid="llm-model-option"]')].map((li) => li.textContent.trim());

describe('settings: LLM model combobox', () => {
    it('opens the filtered list while typing, picks on click, keeps the native select for short lists', async () => {
        await bootSettings([
            { method: 'GET', url: '/api/llm/config', reply: CONFIG },
            {
                method: 'POST', url: '/api/llm/models',
                reply: ({ body }) => (body.connection_id === 'local'
                    ? { models: ['llama3', 'nomic-embed-text'], error: null }
                    : { models: MANY, error: null }),
            },
        ], 'llm-tab');
        const panel = document.querySelector('[data-testid="llm-panel"]');

        // Models are loaded from the connection card, not on tab open.
        panel.querySelector('[data-testid="llm-connection-or"] [data-testid="llm-conn-load-models"]').click();
        const row = panel.querySelector('[data-testid="llm-role-reasoning"]');
        await vi.waitFor(() => {
            if (!row.querySelector('[data-testid="llm-model-filter-reasoning"]')) throw new Error('combobox not rendered');
        });
        const input = row.querySelector('[data-testid="llm-model-filter-reasoning"]');
        // Closed: the input shows the bound model, no list.
        expect(input.value).toBe('x-ai/grok-4');
        expect(row.querySelector('[data-testid="llm-model-panel-reasoning"]')).toBeNull();

        // Typing opens the list already filtered — no extra click needed.
        typeInto(input, 'GEMINI flash');
        await flush();
        expect(row.querySelector('[data-testid="llm-model-panel-reasoning"]')).toBeTruthy();
        // The bound model stays selectable even though it does not match.
        expect(optionTexts(row)).toEqual(['google/gemini-2.5-flash', 'x-ai/grok-4']);

        typeInto(input, 'nothing-like-this');
        await flush();
        expect(row.querySelector('[data-testid="llm-model-panel-reasoning"]').textContent).toContain('No model matches');

        // Click picks, closes, and the input shows the new model.
        typeInto(input, 'haiku');
        await flush();
        row.querySelector('[data-testid="llm-model-option"]').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        await flush();
        expect(row.querySelector('[data-testid="llm-model-panel-reasoning"]')).toBeNull();
        expect(input.value).toBe('anthropic/claude-haiku-4');

        // Escape closes without changing the pick.
        input.dispatchEvent(new FocusEvent('focus'));
        await flush();
        expect(row.querySelector('[data-testid="llm-model-panel-reasoning"]')).toBeTruthy();
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await flush();
        expect(row.querySelector('[data-testid="llm-model-panel-reasoning"]')).toBeNull();
        expect(input.value).toBe('anthropic/claude-haiku-4');

        // A short list (Ollama) keeps the native dropdown, no combobox.
        const fastRow = panel.querySelector('[data-testid="llm-role-fast"]');
        const connSelect = fastRow.querySelectorAll('select')[0];
        connSelect.value = 'local';
        connSelect.dispatchEvent(new Event('change'));
        await vi.waitFor(() => {
            if (!fastRow.querySelector('[data-testid="llm-model-select-fast"]')) throw new Error('select not rendered');
        });
        expect(fastRow.querySelector('[data-testid="llm-model-filter-fast"]')).toBeNull();
    });
});
