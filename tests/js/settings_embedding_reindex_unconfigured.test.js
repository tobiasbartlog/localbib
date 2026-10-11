// Counterpart of settings_embedding_reindex: without an embedding role the
// search index block says that semantic search is off and where a model is
// bound — no buttons that could do nothing (#213). Once a model is bound and
// the index lacks entries, the "incomplete" hint offers the same rebuild.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, flush, goTo } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('settings: search index without a model', () => {
    it('names the missing role instead of offering buttons, then the incomplete hint', async () => {
        let status = { indexed: 0, total: 12, model: '', dim: 0, chunks_indexed: 0, chunks_total: 0, stale: false, stale_model: '' };
        await bootSettings([
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: () => status },
        ], 'ai-models');

        const block = q('[data-testid="search-index"]');
        expect(block.getAttribute('data-state')).toBe('off');
        expect(q('[data-testid="search-index-off"]').textContent).toContain('Semantic search is off');
        expect(q('[data-testid="search-index-off"]').textContent).toContain('Embedding role');
        expect(block.querySelector('button')).toBeNull();
        expect(q('[data-testid="search-index-pill"]')).toBeNull();

        // Bound, but items and passages without a vector: "incomplete".
        status = { indexed: 7, total: 12, model: 'nomic-embed-text', dim: 768, chunks_indexed: 40, chunks_total: 40, stale: false, stale_model: '' };
        goTo('#/settings/general');
        await flush();
        goTo('#/settings/ai-models');
        await vi.waitFor(() => {
            if (!q('[data-testid="search-index-hint-incomplete"]')) throw new Error('incomplete hint missing');
        });
        expect(q('[data-testid="search-index-pill"]').textContent).toContain('Incomplete');
        expect(q('[data-testid="search-index-hint-incomplete"]').textContent).toContain('The search index is incomplete');
        expect(q('[data-testid="search-index-rebuild"]')).toBeTruthy();
        expect(q('[data-testid="search-index-items"]').textContent).toContain('7 / 12');
    });
});
