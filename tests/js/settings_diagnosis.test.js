// Settings → Diagnosis: runs when the section opens, groups Findings by kind, and a
// fix sends exactly the Finding's item + chosen file, then reloads without
// the LLM probe (whose Finding stays on screen).
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, settingsSection } from './helpers.js';

const MISSING = {
    id: 'missing_pdf:20', kind: 'missing_pdf', severity: 'error', paper_id: 20,
    title: 'First RILEM Conference', params: { filename: 'gone.pdf' },
    fixes: ['assign_file', 'clear_pdf', 'delete_item'],
    candidates: ['2019_Wangler_First_RILEM.pdf', 'other.pdf'],
    suggestion: '2019_Wangler_First_RILEM.pdf',
};
const LLM = {
    id: 'llm_auth:default', kind: 'llm_auth', severity: 'error',
    params: { connection: 'OpenAI', tiers: 'reasoning, fast' }, fixes: ['open_llm_settings'],
};

const q = (sel) => document.querySelector(sel);

describe('settings: Diagnose', () => {
    it('zeigt Befunde und behebt einen per Klick', async () => {
        let fixed = false;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: {} },
            { method: 'GET', url: '/api/diagnosis?llm=true',
              reply: { findings: [LLM, MISSING], counts: { error: 2, warning: 0, info: 0 } } },
            { method: 'GET', url: '/api/diagnosis?llm=false',
              reply: () => ({ findings: fixed ? [] : [MISSING], counts: {} }) },
            { method: 'POST', url: '/api/diagnosis/fix',
              reply: () => { fixed = true; return { status: 'ok', filename: 'x.pdf' }; } },
        ], 'diagnosis');

        await vi.waitFor(() => {
            if (!q('[data-finding="missing_pdf:20"]')) throw new Error('Befund fehlt');
        });
        expect(q('[data-kind="llm_auth"]').textContent).toContain('LLM key rejected');
        const row = q('[data-finding="missing_pdf:20"]');
        expect(row.textContent).toContain('Expected: gone.pdf');
        // Best guess preselected.
        expect(row.querySelector('select').value).toBe('2019_Wangler_First_RILEM.pdf');

        row.querySelector('[data-fix="assign_file"]').click();
        await vi.waitFor(() => {
            if (q('[data-finding="missing_pdf:20"]')) throw new Error('Befund noch da');
        });
        const sent = calls.find((c) => c.url === '/api/diagnosis/fix');
        expect(sent.body).toEqual({ action: 'assign_file', paper_id: 20, file: '2019_Wangler_First_RILEM.pdf' });
        // The LLM Finding survives the probe-less reload.
        expect(q('[data-finding="llm_auth:default"]')).toBeTruthy();

        // Its fix leads to the AI models section, by address (#207).
        q('[data-finding="llm_auth:default"] [data-fix="open_llm_settings"]').click();
        await vi.waitFor(() => {
            if (settingsSection() !== 'ai-models') throw new Error('AI models not open');
        });
        expect(window.location.hash).toBe('#/settings/ai-models');
        expect(q('[data-testid="llm-panel"]')).toBeTruthy();
        expect(q('[data-testid="settings-nav-ai-models"]').classList.contains('is-active')).toBe(true);
    });
});
