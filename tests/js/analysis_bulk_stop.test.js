// "Read references from PDFs" in a Collection (#241): the dialog names the
// Collection, the run carries exactly its Items' `paper_ids`, "Stop" aborts
// the stream through its AbortController, and a stream that ends without its
// `complete` frame counts as broken off - both offer to resume.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, holdRequests, liveStream, preferReducedMotion } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const dialog = () => q('[data-testid="bulk-dialog"]');

async function waitState(state) {
    return vi.waitFor(() => {
        const el = dialog();
        if (!el || el.getAttribute('data-state') !== state) throw new Error('dialog not in ' + state);
        return el;
    });
}

describe('analysis: PDF references in a Collection', () => {
    it('sends the Collection, stops on request and reports a cut stream', async () => {
        preferReducedMotion();
        const coll = { id: 4, name: 'Paper X', description: '', archived: false, item_count: 2, pdf_count: 2 };
        await bootAnalysis([
            { method: 'GET', url: '/api/collections/4', reply: coll },
            { method: 'GET', url: '/api/papers?collection_id=4', reply: [{ id: 7, title: 'A' }, { id: 9, title: 'B' }] },
            { method: 'GET', url: '/api/llm/status', reply: { reasoning: true, fast: true, embedding: false, connections: 1 } },
        ], '#/analyse?collection_id=4');
        await vi.waitFor(() => {
            if (!q('[data-testid="analysis-scope"]').textContent.includes('Paper X')) throw new Error('scope missing');
        });
        const runs = holdRequests((method, url) => method === 'POST' && url === '/api/papers/bulk-extract-references');

        q('[data-testid="analysis-pdf-refs"]').click();
        await waitState('confirm');
        expect(q('[data-testid="bulk-scope"]').textContent).toBe('This reads the items in “Paper X” that have a PDF but no references yet.');
        q('[data-testid="bulk-start"]').click();
        await vi.waitFor(() => { if (runs.length !== 1) throw new Error('run not started'); });
        expect(runs[0].body).toEqual({ paper_ids: [7, 9] });
        expect(runs[0].signal).not.toBeNull();

        // Stop: the stream is aborted, what was read stays, resume is offered.
        const first = liveStream(runs[0].signal);
        runs[0].respond(first.response);
        first.push({ type: 'progress', step: 'paper_start', percent: 0, current_paper: 1, total_papers: 2 });
        first.push({ type: 'paper_result', paper_id: 7, title: 'A', status: 'ok', total_extracted: 12, in_library: 1 });
        await vi.waitFor(() => {
            if (document.querySelectorAll('[data-testid="bulk-rows"] li').length !== 1) throw new Error('row missing');
        });
        q('[data-testid="bulk-stop"]').click();
        let dlg = await waitState('stopped');
        expect(first.aborted).toBe(true);
        expect(dlg.textContent).toContain('The run is stopped.');
        expect(dlg.textContent).toContain('Stopped after 1 of 2 items. What had been read by then is saved.');
        expect(q('[data-testid="bulk-resume"]').textContent.trim()).toBe('Resume');
        // A frame that still arrives changes nothing.
        first.push({ type: 'complete', processed: 2, total_references: 12, total_in_library: 1, results: [] });
        await flush();
        expect(dialog().getAttribute('data-state')).toBe('stopped');

        // Resume: a new run with the same Items; this stream just ends.
        q('[data-testid="bulk-resume"]').click();
        await vi.waitFor(() => { if (runs.length !== 2) throw new Error('no second run'); });
        expect(runs[1].body).toEqual({ paper_ids: [7, 9] });
        const second = liveStream(runs[1].signal);
        runs[1].respond(second.response);
        second.push({ type: 'progress', step: 'paper_start', percent: 0, current_paper: 1, total_papers: 2 });
        second.push({ type: 'paper_result', paper_id: 9, title: 'B', status: 'no_text' });
        second.end();
        dlg = await waitState('aborted');
        const outcome = q('[data-testid="bulk-outcome"]');
        expect(outcome.getAttribute('role')).toBe('alert');
        expect(outcome.textContent).toContain('The run broke off.');
        expect(outcome.textContent).toContain('After 1 of 2 items no further answer came. What had been read by then is saved.');
        expect(dlg.textContent).not.toContain('⟦');
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await flush();
        expect(dialog()).toBeNull();
    });
});
