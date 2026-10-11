// Pflege II (#213): Refresh block ② "Recalculate technical data". The step
// list (the search index only with an embedding role, marked "stale" when the
// index no longer matches the model), the three states — ready, running (the
// current step, its bar, the steps done and to come, Cancel), done (one tile
// per step, the error count, run again) — and the run going out as
// POST /api/maintenance/recompute with exactly the ticked steps.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, goTo } from './helpers.js';
import { done, installRecomputeStream, progress } from './recompute_stream.js';

const q = (sel) => document.querySelector(sel);
const block = () => q('[data-testid="recompute"]');
const box = (step) => q(`[data-step="${step}"]`);
const foot = () => q('[data-testid="recompute-foot"]').textContent.trim();

const OFF = { indexed: 0, total: 412, model: '', dim: 0, chunks_indexed: 0, chunks_total: 0, stale: false, stale_model: '' };
const STALE = {
    indexed: 0, total: 412, model: 'nomic-embed-text', dim: 768, chunks_indexed: 0, chunks_total: 12870,
    stale: true, stale_model: 'text-embedding-3-small',
};

describe('settings: recompute technical data', () => {
    it('offers the steps, runs, cancels, runs again and shows the numbers', async () => {
        let indexStatus = OFF;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: () => indexStatus },
        ], 'refresh');
        const stream = installRecomputeStream(calls);
        const runs = () => calls.filter((c) => c.url === '/api/maintenance/recompute');

        // --- ready: five steps, the search index needs an embedding role ----
        expect(block().getAttribute('data-state')).toBe('ready');
        expect([...document.querySelectorAll('[data-step]')].map((el) => el.getAttribute('data-step'))).toEqual(
            ['page_counts', 'citations', 'passages', 'search_index', 'category_folders']);
        expect(box('page_counts').checked).toBe(true);
        expect(box('citations').checked).toBe(true);
        expect(box('passages').checked).toBe(false);
        expect(box('category_folders').checked).toBe(false);
        expect(box('search_index').checked).toBe(false);
        expect(box('search_index').disabled).toBe(true);
        expect(q('[data-testid="recompute-step-search_index"]').textContent).toContain('Needs an embedding model');
        expect(q('[data-testid="recompute-stale"]')).toBeNull();
        expect(foot()).toBe('2 of 5 steps chosen');

        // With a bound but stale index the step is selectable and marked.
        indexStatus = STALE;
        goTo('#/settings/general');
        await flush();
        goTo('#/settings/refresh');
        await vi.waitFor(() => {
            if (!q('[data-testid="recompute-stale"]')) throw new Error('stale marker missing');
        });
        expect(q('[data-testid="recompute-stale"]').textContent).toContain('stale');
        expect(box('search_index').disabled).toBe(false);
        expect(box('search_index').checked).toBe(true);
        expect(foot()).toBe('3 of 5 steps chosen');

        box('passages').click();
        await flush();
        expect(foot()).toBe('4 of 5 steps chosen');

        // --- running: current step, bar, done and coming steps, Cancel ------
        const steps = ['page_counts', 'citations', 'passages', 'search_index'];
        q('[data-testid="recompute-start"]').click();
        await vi.waitFor(() => {
            if (!runs().length) throw new Error('no run');
        });
        expect(runs()[0].body).toEqual({ steps });
        await vi.waitFor(() => {
            if (block().getAttribute('data-state') !== 'running') throw new Error('not running');
        });
        // Before the first frame the run stands on its first step.
        expect(q('[data-testid="recompute-current"]').textContent).toContain('Page counts');
        stream.current().push(progress(steps, 'citations', 212, 412));
        await vi.waitFor(() => {
            if (!q('[data-testid="recompute-current"]').textContent.includes('Citation counts')) throw new Error('no progress');
        });
        expect(q('[data-testid="recompute-progress"]').textContent).toContain('212 / 412 · 51 %');
        expect(q('[data-trail="page_counts"]').getAttribute('data-trail-state')).toBe('done');
        expect(q('[data-trail="citations"]').getAttribute('data-trail-state')).toBe('current');
        expect(q('[data-trail="search_index"]').getAttribute('data-trail-state')).toBe('next');
        expect(foot()).toBe('Step 2 of 4 running');
        expect(box('page_counts').disabled).toBe(true);
        expect(q('[data-testid="recompute-start"]')).toBeNull();

        // Cancel aborts the request and goes back to the list, no numbers.
        q('[data-testid="recompute-cancel"]').click();
        await vi.waitFor(() => {
            if (block().getAttribute('data-state') !== 'ready') throw new Error('not back to ready');
        });
        expect(q('[data-testid="recompute-result"]')).toBeNull();
        expect(q('[data-testid="recompute-error"]')).toBeNull();
        expect(box('passages').checked).toBe(true);

        // --- done: one tile per step with its number, the error count -------
        q('[data-testid="recompute-start"]').click();
        await vi.waitFor(() => {
            if (runs().length < 2) throw new Error('no second run');
        });
        const statusReads = calls.filter((c) => c.url === '/api/maintenance/embeddings/status').length;
        stream.current().push(progress(steps, 'search_index', 400, 412));
        stream.current().push(done(steps, {
            page_counts: { updated: 389, errors: 0 },
            citations: { updated: 401, errors: 1 },
            passages: { updated: 12870, errors: 0 },
            search_index: { updated: 13282, errors: 0, items: 412, passages: 12870 },
        }, 1));
        stream.current().close();
        await vi.waitFor(() => {
            if (block().getAttribute('data-state') !== 'done') throw new Error('not done');
        });
        const tile = (s) => q(`[data-tile="${s}"]`).textContent;
        expect(tile('page_counts')).toContain('389');
        expect(tile('page_counts')).toContain('Page counts');
        expect(tile('passages')).toContain('12,870');
        expect(tile('citations')).toContain('1 error');
        expect(foot()).toBe('Done · 1 error');
        expect(q('[data-testid="recompute-start"]').textContent).toContain('Recalculate again');
        expect(document.querySelectorAll('[data-step]').length).toBe(0);
        // The run may have changed the index: its status is read again.
        await vi.waitFor(() => {
            const now = calls.filter((c) => c.url === '/api/maintenance/embeddings/status').length;
            if (now <= statusReads) throw new Error('status not reread');
        });

        // "Recalculate again" runs the same steps once more.
        q('[data-testid="recompute-start"]').click();
        await vi.waitFor(() => {
            if (runs().length < 3) throw new Error('no third run');
        });
        expect(runs()[2].body).toEqual({ steps });
        stream.current().push(done(steps, {}, 0));
        stream.current().close();
        await vi.waitFor(() => {
            if (foot() !== 'Done · 0 errors') throw new Error('third run not done');
        });
        expect(document.body.textContent).not.toContain('⟦');
    });

    it('shows a refusal and stays ready', async () => {
        // The same app as above (one mount per file): the server refuses.
        const calls = [];
        const base = globalThis.fetch;
        globalThis.fetch = vi.fn(async (url, options = {}) => {
            if (url === '/api/maintenance/recompute') {
                calls.push(JSON.parse(options.body));
                return {
                    ok: false, status: 409,
                    json: async () => ({ detail: 'error.recompute_embedding_not_ready' }),
                };
            }
            return base(url, options);
        });
        goTo('#/settings/general');
        await flush();
        goTo('#/settings/refresh');
        await vi.waitFor(() => {
            if (!q('[data-testid="recompute-start"]')) throw new Error('no start');
        });
        q('[data-testid="recompute-start"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="recompute-error"]')) throw new Error('no error');
        });
        expect(q('[data-testid="recompute-error"]').textContent).toContain('needs an embedding model');
        expect(calls.length).toBe(1);
    });
});
