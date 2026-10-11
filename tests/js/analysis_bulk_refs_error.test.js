// Analysis (SPA-5, bulk path; the dialog since #241): a refused bulk
// reference extraction - JSON instead of a stream - keeps the dialog open and
// says why in the UI language, never as the code and never in an alert();
// "Resume" is a new run over the same scope. After the run, the close key,
// Esc and the backdrop close it, and focus returns to the key it came from.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const dialog = () => q('[data-testid="bulk-dialog"]');

async function waitState(state) {
    return vi.waitFor(() => {
        const el = dialog();
        if (!el || el.getAttribute('data-state') !== state) throw new Error('dialog not in ' + state);
        return el;
    });
}

describe('analysis: bulk reference extraction refused', () => {
    it('shows the translated reason in the dialog and offers to resume', async () => {
        const alerts = vi.spyOn(window, 'alert').mockImplementation(() => {});
        const calls = await bootAnalysis([
            { method: 'GET', url: '/api/llm/status', reply: { reasoning: true, fast: true, embedding: false, connections: 1 } },
            { method: 'POST', url: '/api/papers/bulk-extract-references', reply: { detail: 'error.no_pdf_for_item' }, status: 404 },
        ]);
        const key = q('[data-testid="analysis-pdf-refs"]');
        const runs = () => calls.filter((c) => c.method === 'POST' && c.url === '/api/papers/bulk-extract-references').length;

        key.click();
        await waitState('confirm');
        q('[data-testid="bulk-start"]').click();
        let dlg = await waitState('aborted');
        expect(runs()).toBe(1);
        const outcome = q('[data-testid="bulk-outcome"]');
        expect(outcome.getAttribute('role')).toBe('alert');
        expect(outcome.textContent).toContain('The run broke off.');
        expect(outcome.textContent).toContain('No PDF for this item');
        expect(dlg.textContent).not.toContain('error.no_pdf_for_item');
        expect(dlg.textContent).toContain('Resuming reads only the items that have no references yet.');

        // Resume: a new run with the same scope.
        q('[data-testid="bulk-resume"]').click();
        await vi.waitFor(() => { if (runs() !== 2) throw new Error('no second run'); });
        dlg = await waitState('aborted');

        // After the run: Close, Esc and the backdrop close; focus goes back.
        q('[data-testid="bulk-close"]').click();
        await flush();
        expect(dialog()).toBeNull();
        expect(document.activeElement).toBe(key);

        key.click();
        await waitState('confirm');
        q('[data-testid="bulk-start"]').click();
        await waitState('aborted');
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await flush();
        expect(dialog()).toBeNull();

        key.click();
        await waitState('confirm');
        q('[data-testid="bulk-start"]').click();
        await waitState('aborted');
        q('[data-testid="bulk-scrim"]').click();
        await flush();
        expect(dialog()).toBeNull();
        expect(alerts).not.toHaveBeenCalled();
    });
});
