// Settings -> LLM tab, rejected save: the server's error code (a role bound to
// a connection that is not in the document) is rendered translated, and the
// empty state tells a fresh install what to do first.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect } from 'vitest';
import { bootSettings, findByText, flush } from './helpers.js';

describe('settings: LLM tab errors + empty state', () => {
    it('shows the empty state and renders a 422 error code from the server', async () => {
        await bootSettings([
            {
                method: 'PUT', url: '/api/llm/config', status: 422,
                reply: { detail: { code: 'error.llm.connectionUnknown', params: { tier: 'fast', id: 'ghost' } } },
            },
        ], 'llm-tab');

        const panel = document.querySelector('[data-testid="llm-panel"]');
        expect(panel.querySelector('[data-testid="llm-empty"]')).toBeTruthy();
        // With no connection the role dropdowns are disabled.
        expect(panel.querySelector('[data-testid="llm-role-reasoning"] select').disabled).toBe(true);

        panel.querySelector('[data-testid="llm-save"]').click();
        await flush(); await flush();
        const err = panel.querySelector('[data-testid="llm-error"]');
        expect(err).toBeTruthy();
        expect(err.textContent).toContain('fast');
        expect(err.textContent).toContain('ghost');
        expect(findByText('[data-testid="llm-error"]', '⟦')).toBeFalsy();
    });
});
