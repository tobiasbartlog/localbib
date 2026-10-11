// Knowledge network before the first run (#241): the empty state is the one
// place to start - search depth with a sentence per depth, "Start analysis"
// and what goes to OpenAlex - while the head carries only "PDF references".
// Runs without matchMedia and ResizeObserver and on a 0 × 0 layout (jsdom),
// so the page and the drawing must cope with all three.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, goTo } from './helpers.js';
import { analysisText, network, nodesOfKind } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);

describe('analysis: before the first run', () => {
    it('starts only from the empty state, with depth, sentence and scope', async () => {
        delete window.matchMedia;
        delete window.ResizeObserver;
        expect(typeof window.matchMedia).toBe('undefined');
        expect(typeof window.ResizeObserver).toBe('undefined');

        const coll = { id: 4, name: 'Paper X', description: '', archived: false, item_count: 2, pdf_count: 1 };
        const calls = await bootAnalysis([
            { method: 'GET', url: '/api/stats', reply: { paper_count: 34, with_doi: 30 } },
            { method: 'GET', url: '/api/stats?collection_id=4', reply: { paper_count: 2, with_doi: 1 } },
            { method: 'GET', url: '/api/collections/4', reply: coll },
            { method: 'POST', url: '/api/analysis/build?depth=3', reply: { detail: 'Upstream down' }, status: 502 },
            {
                method: 'POST', url: '/api/analysis/build?depth=3&collection_id=4',
                reply: network({ stats: { own_papers: 2, papers_found_in_openalex: 2, total_references: 4, pdf_reference_edges: 1, missing_sources: 1, depth: 3 } }),
            },
        ]);

        const empty = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-empty"]');
            if (!el) throw new Error('empty state missing');
            return el;
        });
        const head = q('.lb-an-head');
        // The head: only "PDF references", no depth, no run key.
        expect(head.querySelector('[role="radiogroup"]')).toBeNull();
        expect(q('[data-testid="analysis-run"]')).toBeNull();
        expect(q('[data-testid="analysis-pdf-refs"]').textContent).toContain('PDF references');
        expect(head.querySelector('.lb-eyebrow').textContent).toBe('Analysis');
        expect(head.querySelector('h1').textContent).toBe('Knowledge network');
        // The meta line counts the scope's items before any network exists.
        await vi.waitFor(() => {
            const el = q('[data-testid="analysis-meta-items"]');
            if (!el) throw new Error('item count missing');
            expect(el.textContent).toBe('34 own items, 30 of them with a DOI');
        });
        // No tiles, no toolbar, no mode switch, no tabs before a network.
        expect(q('[data-testid="analysis-tabs"]')).toBeNull();
        expect(document.querySelector('.lb-analyse .lb-toolbar, .lb-analyse .lb-segmented')).toBeNull();

        // Search depth lives in the empty state: a radiogroup with a label in
        // mono caps, a roving tabindex and the depth's name beside it.
        const group = empty.querySelector('[role="radiogroup"]');
        expect(group).not.toBeNull();
        const label = document.getElementById(group.getAttribute('aria-labelledby'));
        expect(label.textContent).toBe('Search depth');
        expect(label.classList.contains('lb-an-label')).toBe(true);
        const radios = [...group.querySelectorAll('[role="radio"]')];
        expect(radios.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false', 'false', 'false']);
        expect(radios.map((r) => r.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1', '-1']);
        const name = () => document.getElementById(group.getAttribute('aria-describedby')).textContent;
        const note = () => q('[data-testid="analysis-depth-note"]').textContent;
        expect(name()).toBe('Direct references');
        expect(note()).toBe('Only the works your items cite themselves. The fastest run.');

        // Arrow keys move the choice and the focus.
        radios[0].focus();
        radios[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        await flush();
        group.querySelector('[data-depth="2"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        await flush();
        const third = group.querySelector('[data-depth="3"]');
        expect(third.getAttribute('aria-checked')).toBe('true');
        expect(third.getAttribute('tabindex')).toBe('0');
        expect(document.activeElement).toBe(third);
        expect(name()).toBe('Three levels');
        // One sentence per depth, none with minutes.
        const notes = [];
        for (const d of [1, 2, 3, 4, 5]) {
            group.querySelector(`[data-depth="${d}"]`).click();
            await flush();
            notes.push(note());
        }
        expect(new Set(notes).size).toBe(5);
        for (const n of notes) expect(n).not.toMatch(/minute|\bmin\b|\d/i);
        group.querySelector('[data-depth="3"]').click();
        await flush();

        // What goes to OpenAlex stands under the start key.
        const start = q('[data-testid="analysis-start"]');
        expect(start.textContent.trim()).toBe('Start analysis');
        const fine = [...empty.querySelectorAll('p')].find((p) => p.textContent.includes('OpenAlex receives'));
        expect(fine.textContent).toBe('OpenAlex receives the DOIs and titles of your items, nothing else.');
        expect(start.compareDocumentPosition(fine) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

        // Start builds at the chosen depth; a failure says why above the
        // surface, and the start stays where it was.
        start.click();
        const alertBox = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-error"]');
            if (!el) throw new Error('no error notice');
            return el;
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/analysis/build?depth=3')).toBe(true);
        expect(alertBox.getAttribute('role')).toBe('alert');
        expect(alertBox.textContent).toContain('Analysis failed: Upstream down');
        expect(q('[data-testid="analysis-empty"]')).not.toBeNull();
        expect(analysisText()).not.toContain('⟦');

        // In a Collection the build carries its id, and the network is drawn
        // on a 0 × 0 sheet without matchMedia (fallback sizes, timer layout).
        goTo('#/analyse?collection_id=4');
        await vi.waitFor(() => {
            const el = q('[data-testid="analysis-scope"]');
            if (!el || !el.textContent.includes('Paper X')) throw new Error('scope not switched');
            const items = q('[data-testid="analysis-meta-items"]');
            if (!items || items.textContent !== '2 own items, 1 of them with a DOI') throw new Error('scope count not loaded');
        });
        expect(q('[data-testid="analysis-scope-clear"]').textContent).toBe('Use whole library');
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => {
            if (nodesOfKind('own').length !== 2) throw new Error('network not drawn');
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/analysis/build?depth=3&collection_id=4')).toBe(true);
        expect(q('[data-testid="analysis-error"]')).toBeNull();
        expect(q('[data-testid="analysis-empty"]')).toBeNull();
        expect(q('.lb-an-head [role="radiogroup"]')).not.toBeNull();
        expect(q('[data-testid="analysis-run"]').textContent.trim()).toBe('Analyse again');
        const svg = q('.lb-an-canvas svg');
        expect(svg.getAttribute('width')).toBe('960');
        expect(svg.getAttribute('height')).toBe('600');

        // Leaving the page stops the running layout.
        goTo('#/');
        await vi.waitFor(() => {
            if (q('.lb-analyse')) throw new Error('still on the analysis page');
        });
    });
});
