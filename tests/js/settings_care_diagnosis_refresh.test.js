// Pflege I (#210): the Diagnosis counter in the nav comes from the cheap
// `?llm=false` pass (opening Settings never probes the LLM), the full
// Diagnosis runs only in its section, `uncategorized` jumps to Refresh with
// categories ticked and "only fill gaps" on, and the Refresh block shows how
// many items a run affects before it starts.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, flush, goTo, settingsSection } from './helpers.js';

const MISSING = {
    id: 'missing_pdf:20', kind: 'missing_pdf', severity: 'error', paper_id: 20,
    title: 'First RILEM Conference', params: { filename: 'gone.pdf' },
    fixes: ['clear_pdf', 'delete_item'],
};
const UNCATEGORIZED = {
    id: 'uncategorized', kind: 'uncategorized', severity: 'info',
    params: { count: 14 }, fixes: ['suggest_categories'],
};
const LLM = {
    id: 'llm_auth:default', kind: 'llm_auth', severity: 'warning',
    params: { connection: 'OpenAI', tiers: 'reasoning' }, fixes: ['open_llm_settings'],
};

const q = (sel) => document.querySelector(sel);
const urls = (calls) => calls.map((c) => c.url);

describe('settings: Pflege I', () => {
    it('counter from llm=false, full diagnosis only in its section, jump into Refresh', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: {} },
            { method: 'GET', url: '/api/diagnosis?llm=false',
              reply: { findings: [MISSING, UNCATEGORIZED], counts: {} } },
            { method: 'GET', url: '/api/diagnosis?llm=true',
              reply: { findings: [MISSING, LLM, UNCATEGORIZED], counts: {} } },
            { method: 'POST', url: '/api/maintenance/refresh-review/count',
              reply: ({ body }) => ({ count: body.only_gaps ? 14 : 400 }) },
            { method: 'POST', url: '/api/maintenance/refresh-review/propose',
              stream: [{ type: 'done', rows: [], stats: { items: 400, errors: 0, no_text: 0 }, warnings: [] }] },
        ], 'general');

        // Opening Settings: the cheap pass only, the counter shows problem kinds.
        await vi.waitFor(() => {
            if (!q('[data-testid="diagnosis-count"]')) throw new Error('counter missing');
        });
        expect(urls(calls)).toContain('/api/diagnosis?llm=false');
        expect(urls(calls)).not.toContain('/api/diagnosis?llm=true');
        expect(q('[data-testid="diagnosis-count"]').textContent.trim()).toBe('2');
        expect(q('[data-testid="diagnosis-count"]').className).toContain('is-error');

        // The section runs the full check, with the LLM probe.
        goTo('#/settings/diagnosis');
        await vi.waitFor(() => {
            if (!q('[data-kind="llm_auth"]')) throw new Error('full diagnosis missing');
        });
        expect(urls(calls)).toContain('/api/diagnosis?llm=true');
        expect(q('[data-testid="diagnosis-total"]').textContent.trim()).toBe('3');
        expect(q('[data-testid="diagnosis-errors"]').textContent).toContain('1 error');
        expect(q('[data-testid="diagnosis-warnings"]').textContent).toContain('1 warning');
        expect(q('[data-testid="diagnosis-infos"]').textContent).toContain('1 notice');
        expect(q('[data-testid="diagnosis-checked"]')).toBeTruthy();
        expect(q('[data-testid="diagnosis-count"]').textContent.trim()).toBe('3');
        // The group shows the number of items, not of Findings.
        expect(q('[data-kind="uncategorized"] [data-testid="diagnosis-group-count"]').textContent.trim()).toBe('14');
        // The checks without a finding are named in one line.
        const clean = q('[data-testid="diagnosis-clean"]').textContent;
        expect(clean).toContain('PDF without an item');
        expect(clean).not.toContain('PDF file missing');

        // Uncategorised: the fix is a jump, with the choice made.
        q('[data-kind="uncategorized"] button').click();            // opens the (folded) info group
        await flush();
        q('[data-finding="uncategorized"] [data-fix="suggest_categories"]').click();
        await vi.waitFor(() => {
            if (settingsSection() !== 'refresh') throw new Error('Refresh not open');
        });
        expect(window.location.hash).toContain('/settings/refresh');
        expect(q('[data-scope="categories"]').checked).toBe(true);
        expect(q('[data-scope="metadata"]').checked).toBe(false);
        expect(q('[data-testid="refresh-review-only-gaps"]').checked).toBe(true);
        await vi.waitFor(() => {
            if (!q('[data-testid="refresh-review-affected"]')) throw new Error('count missing');
        });
        expect(q('[data-testid="refresh-review-affected"]').textContent).toContain('Affects 14 items');
        const count = calls.filter((c) => c.url === '/api/maintenance/refresh-review/count').pop();
        expect(count.body).toEqual({ scopes: ['categories'], only_gaps: true });

        // The two numbered blocks with their tags.
        expect(q('[data-testid="refresh-block-1-tag"]').textContent).toContain('with review');
        expect(q('[data-testid="refresh-block-2-tag"]').textContent).toContain('without review');

        // Switching "only fill gaps" off recounts, and the run carries the flag.
        q('[data-testid="refresh-review-only-gaps"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="refresh-review-affected"]').textContent.includes('400')) throw new Error('not recounted');
        });
        q('[data-testid="refresh-review-start"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.url === '/api/maintenance/refresh-review/propose')) throw new Error('no propose');
        });
        const propose = calls.find((c) => c.url === '/api/maintenance/refresh-review/propose');
        expect(propose.body).toEqual({ scopes: ['categories'], only_gaps: false });
    });
});
