// Settings -> AI models shows the Startup check (#209): the same report as the
// popup at the top — one line per failing role, the time it was checked and
// "Check again" — and the affected role cards marked. Checking again replaces
// the report; once everything works, the hint gives way to a short all-clear.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, flush } from './helpers.js';
import { BROKEN_REPORT, CONFIG, OK_REPORT } from './startup_check_fixtures.js';

const q = (sel) => document.querySelector(sel);
const status = (tier) => q(`[data-testid="llm-role-status-${tier}"]`).getAttribute('data-status');

describe('settings: AI models — startup check', () => {
    it('shows the report, marks the roles and checks again', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/llm/config', reply: CONFIG },
            { method: 'GET', url: '/api/llm/startup-check', reply: BROKEN_REPORT },
            { method: 'POST', url: '/api/llm/startup-check', reply: OK_REPORT },
        ], 'ai-models');

        const alert = await vi.waitFor(() => {
            const el = q('[data-testid="llm-check-alert"]');
            if (!el) throw new Error('hint not shown');
            return el;
        });
        await flush();
        expect(alert.getAttribute('role')).toBe('alert');
        expect(alert.textContent).toContain('Startup check: 2 roles are not working right now');
        expect(q('[data-testid="llm-check-item-reasoning"]').textContent)
            .toBe('Reasoning: The model gpt-4o is no longer offered by OpenAI.');
        expect(q('[data-testid="llm-check-item-embedding"]').textContent)
            .toContain('No answer at localhost:11434.');
        expect(q('[data-testid="llm-check-item-fast"]')).toBeNull();
        expect(q('[data-testid="llm-check-time"]').textContent).toMatch(/^Checked .*2026/);

        // The labels on the cards: the problems in red, the working role ready.
        expect(status('reasoning')).toBe('model_missing');
        expect(q('[data-testid="llm-role-status-reasoning"]').textContent.trim()).toBe('Model missing');
        expect(q('[data-testid="llm-role-reasoning"]').classList.contains('is-danger')).toBe(true);
        expect(status('embedding')).toBe('unreachable');
        expect(q('[data-testid="llm-role-status-embedding"]').textContent.trim()).toBe('Unreachable');
        expect(status('fast')).toBe('ready');

        q('[data-testid="llm-check-again"]').click();
        await vi.waitFor(() => {
            if (q('[data-testid="llm-check-alert"]')) throw new Error('hint still shown');
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/llm/startup-check')).toBe(true);
        expect(q('[data-testid="llm-check-ok"]').textContent).toContain('every bound role works');
        expect(status('reasoning')).toBe('ready');
        expect(status('embedding')).toBe('ready');
        expect(document.body.textContent).not.toContain('⟦');
    });
});
