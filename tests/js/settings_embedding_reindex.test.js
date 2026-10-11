// AI models → Search index (#153, #213): the model, a bar each for items and
// passages, and the context hint. Vectors of another model than the bound one
// make the index "stale": "The search index no longer matches the model" with
// "Rebuild now" — the recompute of Refresh block ② with its search index step,
// no second path. After the run the status is read again and the hint is gone.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, findByText } from './helpers.js';

const STALE = {
    indexed: 0, total: 412, model: 'qwen3-embedding-8b', dim: 4096,
    chunks_indexed: 0, chunks_total: 12870, stale: true, stale_model: 'text-embedding-3-small',
};
const FRESH = {
    indexed: 412, total: 412, model: 'qwen3-embedding-8b', dim: 4096,
    chunks_indexed: 12870, chunks_total: 12870, stale: false, stale_model: '',
};

const q = (sel) => document.querySelector(sel);

describe('settings: search index in AI models', () => {
    it('shows the stale hint and rebuilds the index through the recompute', async () => {
        let rebuilt = false;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: () => (rebuilt ? FRESH : STALE) },
            {
                method: 'POST',
                url: '/api/maintenance/recompute',
                stream: () => {
                    rebuilt = true;
                    return [
                        { type: 'progress', step: 'search_index', index: 1, steps: ['search_index'],
                          current: 50, total: 13282, percent: 0, stats: {} },
                        { type: 'done', steps: ['search_index'], errors: 0, total: 412,
                          stats: { search_index: { updated: 13282, errors: 0, items: 412, passages: 12870 } } },
                    ];
                },
            },
        ], 'ai-models');

        // The block sits between the roles and the connections.
        const sections = [...document.querySelectorAll('[data-settings-section="ai-models"] > section')];
        const at = sections.indexOf(q('[data-testid="search-index"]'));
        expect(at).toBeGreaterThan(0);
        expect(sections[at + 1].textContent).toContain('Connections');

        expect(q('[data-testid="search-index-model"]').textContent).toBe('qwen3-embedding-8b');
        expect(q('[data-testid="search-index-pill"]').textContent).toContain('Stale');
        const hint = q('[data-testid="search-index-hint-stale"]');
        expect(hint.textContent).toContain('The search index no longer matches the model');
        expect(hint.textContent).toContain('text-embedding-3-small');
        expect(q('[data-testid="search-index-items"]').textContent).toContain('0 / 412');
        expect(q('[data-testid="search-index-passages"]').textContent).toContain('0 / 12,870');

        q('[data-testid="search-index-rebuild"]').click();
        await vi.waitFor(() => {
            if (q('[data-testid="search-index-hint-stale"]')) throw new Error('hint still there');
            if (!q('[data-testid="search-index-pill"]').textContent.includes('Ready')) throw new Error('not ready');
        });
        const run = calls.find((c) => c.url === '/api/maintenance/recompute');
        expect(run.method).toBe('POST');
        expect(run.body).toEqual({ steps: ['search_index'] });
        expect(calls.filter((c) => c.url === '/api/maintenance/embeddings/status').length).toBeGreaterThanOrEqual(2);
        expect(q('[data-testid="search-index-items"]').textContent).toContain('412 / 412');
        expect(q('[data-testid="search-index-error"]')).toBeNull();
        // The single index runs of the old block are gone.
        expect(calls.some((c) => c.url.startsWith('/api/maintenance/embeddings/reindex'))).toBe(false);
        expect(findByText('button', 'Index passages')).toBeFalsy();
        expect(document.body.textContent).not.toContain('⟦');
    });
});
