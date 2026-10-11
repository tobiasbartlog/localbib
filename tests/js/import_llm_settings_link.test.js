// An import whose AI steps failed at the provider says so and leads to the AI
// models section by its address (#207) — "Settings → LLM" was a tab nobody
// could link to.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, goTo, settingsSection } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('import: failed AI steps', () => {
    it('name the AI models section and link to it', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/import/pending', reply: { files: [], count: 0 } },
            {
                method: 'POST', url: /^\/api\/import\/upload-smart/,
                stream: [
                    { type: 'progress', step: 'hash', message: 'import.step.hash', percent: 5 },
                    {
                        type: 'complete', paper_id: 9, title: 'An item', categories: [],
                        llm_failures: [{ kind: 'auth', model: 'gpt-4o', status: 401, step: 'categories' }],
                    },
                ],
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        goTo('#/import');
        await vi.waitFor(() => {
            if (!q('[data-testid="import-bib-section"]')) throw new Error('import page not rendered');
        });

        window.dispatchEvent(new CustomEvent('global-pdf-drop', {
            detail: { files: [new File(['%PDF-1.4'], 'a.pdf', { type: 'application/pdf' })] },
        }));
        const start = await vi.waitFor(() => {
            const el = q('[data-testid="upload-start"]');
            if (!el) throw new Error('upload dialog not open');
            return el;
        });
        start.click();

        const link = await vi.waitFor(() => {
            const el = q('[data-testid="import-llm-settings-link"]');
            if (!el) throw new Error('no AI failure note');
            return el;
        });
        expect(link.parentElement.textContent).toContain('Check the connection under Settings → AI models.');
        expect(link.getAttribute('href')).toBe('#/settings/ai-models');

        link.click();
        await vi.waitFor(() => {
            if (settingsSection() !== 'ai-models') throw new Error('AI models not open');
        });
        expect(q('[data-testid="llm-panel"]')).toBeTruthy();
    });
});
