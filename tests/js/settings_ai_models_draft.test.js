// Settings -> AI models keeps ONE atomic save (#209, ADR-0019): a change is a
// draft until the bar at the bottom — "Unsaved change · <summary>" with
// "Discard" and "Save" — saves or drops it. The bar and the dot at the
// section's entry in the Settings list exist only while the draft differs
// from the saved document; the draft survives a visit to another section.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, flush, goTo, settingsSection, typeInto } from './helpers.js';
import { CONFIG } from './startup_check_fixtures.js';

const q = (sel) => document.querySelector(sel);
const bar = () => q('[data-testid="llm-save-bar"]');
const dot = () => q('[data-testid="settings-nav-ai-models"] [data-testid="ai-models-dirty-dot"]');
const status = (tier) => q(`[data-testid="llm-role-status-${tier}"]`).getAttribute('data-status');

async function open(slug) {
    goTo('#/settings/' + slug);
    await vi.waitFor(() => {
        if (settingsSection() !== slug) throw new Error(`${slug} not open`);
    });
    await flush();
}

async function pickReasoning(model) {
    const select = await vi.waitFor(() => {
        const el = q('[data-testid="llm-model-select-reasoning"]');
        if (!el) throw new Error('model dropdown not rendered');
        return el;
    });
    select.value = model;
    select.dispatchEvent(new Event('change'));
    await flush();
}

describe('settings: AI models — unsaved draft', () => {
    it('shows the bar and the dot only while the draft differs', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/llm/config', reply: CONFIG },
            { method: 'POST', url: '/api/llm/models', reply: { models: ['gpt-4o', 'gpt-4.1', 'gpt-4o-mini'], error: null } },
            {
                method: 'PUT', url: '/api/llm/config',
                reply: ({ body }) => ({ ...CONFIG, connections: CONFIG.connections.map((c, i) => ({ ...c, label: body.connections[i].label })), roles: body.roles }),
            },
        ], 'ai-models');

        // Nothing changed yet: no bar, no dot.
        expect(bar()).toBeNull();
        expect(dot()).toBeNull();

        q('[data-testid="llm-connection-oai"] [data-testid="llm-conn-load-models"]').click();
        await pickReasoning('gpt-4.1');
        expect(bar()).toBeTruthy();
        expect(bar().getAttribute('role')).toBe('region');
        expect(bar().textContent).toContain('Unsaved change');
        expect(q('[data-testid="llm-save-summary"]').textContent).toBe('Reasoning: gpt-4o → gpt-4.1');
        expect(dot()).toBeTruthy();
        expect(status('reasoning')).toBe('changed');
        expect(q('[data-testid="llm-role-status-reasoning"]').textContent.trim()).toBe('Changed');

        // Another section: the draft stays, so does the dot.
        await open('general');
        expect(dot()).toBeTruthy();
        await open('ai-models');
        expect(q('[data-testid="llm-model-select-reasoning"]').value).toBe('gpt-4.1');
        expect(bar()).toBeTruthy();

        // Discard: back to the saved document.
        q('[data-testid="llm-discard"]').click();
        await flush();
        expect(bar()).toBeNull();
        expect(dot()).toBeNull();
        expect(q('[data-testid="llm-model-select-reasoning"]').value).toBe('gpt-4o');
        expect(status('reasoning')).toBe('ready');
        expect(calls.some((c) => c.method === 'PUT' && c.url === '/api/llm/config')).toBe(false);

        // Two changes are named both; Save sends the whole document.
        await pickReasoning('gpt-4.1');
        q('[data-testid="llm-connection-local"] [data-testid="llm-conn-edit"]').click();
        await flush();
        typeInto(q('[data-testid="llm-connection-local"] [data-testid="llm-conn-label"]'), 'Ollama');
        await flush();
        expect(bar().textContent).toContain('Unsaved changes');
        expect(q('[data-testid="llm-save-summary"]').textContent).toBe('Reasoning: gpt-4o → gpt-4.1 · Ollama edited');

        q('[data-testid="llm-save"]').click();
        await vi.waitFor(() => {
            if (bar()) throw new Error('bar still shown');
        });
        const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/llm/config');
        expect(put.body.roles.reasoning).toEqual({ connection_id: 'oai', model: 'gpt-4.1' });
        expect(put.body.connections[1]).toMatchObject({ id: 'local', label: 'Ollama', api_key: null });
        expect(q('[data-testid="llm-saved"]').textContent).toContain('AI models saved');
        expect(dot()).toBeNull();
        expect(document.body.textContent).not.toContain('⟦');
    });
});
