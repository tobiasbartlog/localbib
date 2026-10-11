// Startup check of the AI models (#209): after the boot the SPA reads the
// server's report; a role that does not work opens a popup with one line per
// role (problems first), the features that are off until it is fixed, and
// "Go to AI models", which lands on /settings/ai-models — where the same
// report stands at the top. A result dismissed in an EARLIER server start
// (another checked_at) does not keep this one away.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush, settingsSection } from './helpers.js';
import { BROKEN_REPORT, CHECKED_AT, setUpRoutes } from './startup_check_fixtures.js';

const q = (sel) => document.querySelector(sel);

describe('startup check: popup', () => {
    it('reports the failing roles and leads to AI models', async () => {
        sessionStorage.setItem('lb.startupCheck.seen', '2026-10-07T08:00:00+00:00');
        await bootApp(setUpRoutes(BROKEN_REPORT));

        const dialog = await vi.waitFor(() => {
            const el = q('[data-testid="startup-check-dialog"]');
            if (!el) throw new Error('popup not shown');
            return el;
        });
        expect(dialog.querySelector('[role="alertdialog"]')).toBeTruthy();
        expect(dialog.textContent).toContain('AI features are limited right now');
        expect(dialog.textContent).toContain('2 roles are not working');

        // The failing roles first, then the one that works.
        const rows = [...dialog.querySelectorAll('[data-testid^="startup-check-role-"]')]
            .map((el) => el.getAttribute('data-testid').replace('startup-check-role-', ''));
        expect(rows).toEqual(['reasoning', 'embedding', 'fast']);
        const reasoning = q('[data-testid="startup-check-role-reasoning"]').textContent;
        expect(reasoning).toContain('Reasoning');
        expect(reasoning).toContain('OpenAI');
        expect(reasoning).toContain('The model gpt-4o is no longer offered by OpenAI.');
        expect(reasoning).toContain('Model missing');
        const embedding = q('[data-testid="startup-check-role-embedding"]').textContent;
        expect(embedding).toContain('Ollama (local)');
        expect(embedding).toContain('No answer at localhost:11434. Is the local server running?');
        expect(embedding).toContain('Unreachable');
        const fast = q('[data-testid="startup-check-role-fast"]').textContent;
        expect(fast).toContain('Simple tasks');
        expect(fast).toContain('Working');
        expect(fast).not.toContain('Model missing');

        // What is off follows the roles' tasks: reasoning's and embedding's,
        // not the simple tasks', which still work.
        const impact = q('[data-testid="startup-check-impact"]').textContent;
        expect(impact).toContain('Research Chat');
        expect(impact).toContain('Thesis Analysis');
        expect(impact).toContain('semantic search');
        expect(impact).toContain('Everything else works normally.');
        expect(impact).not.toContain('categorisation');
        expect(document.body.textContent).not.toContain('⟦');

        q('[data-testid="startup-check-open"]').click();
        await vi.waitFor(() => {
            if (settingsSection() !== 'ai-models') throw new Error('AI models not open');
        });
        expect(window.location.hash).toBe('#/settings/ai-models');
        expect(q('[data-testid="startup-check-dialog"]')).toBeNull();
        expect(sessionStorage.getItem('lb.startupCheck.seen')).toBe(CHECKED_AT);

        // The same report at the top of AI models, the affected cards marked.
        const alert = await vi.waitFor(() => {
            const el = q('[data-testid="llm-check-alert"]');
            if (!el) throw new Error('hint not shown');
            return el;
        });
        await flush();
        expect(alert.textContent).toContain('Startup check: 2 roles are not working right now');
        expect(q('[data-testid="llm-role-status-reasoning"]').getAttribute('data-status')).toBe('model_missing');
        expect(q('[data-testid="llm-role-status-embedding"]').getAttribute('data-status')).toBe('unreachable');
    });
});
