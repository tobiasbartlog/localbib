// "Read references from PDFs" (#241), library scope: the dialog asks
// GET /api/llm/status first - without a ready fast role it says so and
// leads to the AI-model settings; with one it asks before it starts, shows
// the run (progress bar, "Item n of m", time, one row per Item with a reason
// in words, never the status code), cannot be closed while it runs, ends in
// a summary and rebuilds the network from there. No alert() anywhere.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, goTo, holdRequests, liveStream, preferReducedMotion } from './helpers.js';
import { analysisText, network } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const dialog = () => q('[data-testid="bulk-dialog"]');
const press = (key) => document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));

async function openDialog(state) {
    q('[data-testid="analysis-pdf-refs"]').click();
    return vi.waitFor(() => {
        const el = dialog();
        if (!el || el.getAttribute('data-state') !== state) throw new Error('dialog not in ' + state);
        return el;
    });
}

describe('analysis: PDF references dialog', () => {
    it('walks no model, ask first, running, done and rebuild', async () => {
        preferReducedMotion();
        const alerts = vi.spyOn(window, 'alert').mockImplementation(() => {});
        // Reference extraction runs on the fast role: a ready reasoning role
        // alone does not open the run.
        let fast = false;
        const calls = await bootAnalysis([
            {
                method: 'GET', url: '/api/llm/status',
                reply: () => ({ reasoning: true, fast, embedding: false, connections: 1 }),
            },
            { method: 'POST', url: '/api/analysis/build?depth=1', reply: network() },
        ]);
        const runs = holdRequests((method, url) => method === 'POST' && url === '/api/papers/bulk-extract-references');

        // No AI model: say so, and lead to the settings.
        const statusCalls = () => calls.filter((c) => c.url === '/api/llm/status').length;
        const before = statusCalls();
        let dlg = await openDialog('nomodel');
        expect(statusCalls()).toBe(before + 1);
        expect(dlg.getAttribute('role')).toBe('dialog');
        expect(dlg.getAttribute('aria-modal')).toBe('true');
        expect(dlg.querySelector('h2').textContent).toBe('Read references from PDFs');
        expect(dlg.textContent).toContain('LocalBib needs an AI model. None is connected yet.');
        expect(dlg.textContent).toContain('Its references then come from OpenAlex only.');
        expect(q('[data-testid="bulk-setup-models"]').textContent.trim()).toBe('Set up AI models');
        q('[data-testid="bulk-setup-models"]').click();
        await vi.waitFor(() => {
            if (window.location.hash !== '#/settings/ai-models') throw new Error('not in the settings');
        });
        expect(dialog()).toBeNull();
        expect(runs).toHaveLength(0);

        goTo('#/analyse');
        await vi.waitFor(() => { if (!q('[data-testid="analysis-pdf-refs"]')) throw new Error('analysis not back'); });
        await flush();

        // With a model: ask first - the scope as words, no number - and
        // Cancel or Esc close without a run.
        fast = true;
        dlg = await openDialog('confirm');
        expect(q('[data-testid="bulk-scope"]').textContent).toBe('This reads the items in your library that have a PDF but no references yet.');
        expect(q('[data-testid="bulk-scope"]').textContent).not.toMatch(/\d/);
        expect(q('[data-testid="bulk-cancel"]').textContent.trim()).toBe('Cancel');
        expect(q('[data-testid="bulk-start"]').textContent.trim()).toBe('Start reading');
        press('Escape');
        await flush();
        expect(dialog()).toBeNull();
        dlg = await openDialog('confirm');
        q('[data-testid="bulk-cancel"]').click();
        await flush();
        expect(dialog()).toBeNull();
        expect(runs).toHaveLength(0);

        // Running: the whole library (no body), progress and one row per Item.
        dlg = await openDialog('confirm');
        q('[data-testid="bulk-start"]').click();
        await vi.waitFor(() => { if (runs.length !== 1) throw new Error('run not started'); });
        expect(runs[0].body).toBeNull();
        const stream = liveStream(runs[0].signal);
        runs[0].respond(stream.response);
        stream.push({ type: 'progress', step: 'start', percent: 0, current_paper: 0, total_papers: 3 });
        stream.push({ type: 'progress', step: 'paper_start', percent: 0, current_paper: 1, total_papers: 3 });
        stream.push({ type: 'paper_result', paper_id: 11, title: 'Massive earth walls', status: 'ok', total_extracted: 38, in_library: 6 });
        stream.push({ type: 'progress', step: 'paper_start', percent: 33, current_paper: 2, total_papers: 3 });
        stream.push({ type: 'paper_result', paper_id: 12, title: 'Building in the existing stock', status: 'no_ref_section' });
        const bar = await vi.waitFor(() => {
            const el = q('[data-testid="bulk-progress"]');
            if (!el || el.getAttribute('aria-valuenow') !== '2') throw new Error('progress not shown');
            if (document.querySelectorAll('[data-testid="bulk-rows"] li').length !== 2) throw new Error('rows missing');
            return el;
        });
        expect(dialog().getAttribute('data-state')).toBe('running');
        expect(bar.getAttribute('role')).toBe('progressbar');
        expect(bar.getAttribute('aria-valuemax')).toBe('3');
        expect(q('[data-testid="bulk-count"]').textContent).toBe('Item 2 of 3');
        expect(q('[data-testid="bulk-elapsed"]').textContent).toMatch(/^for 0:0\d$/);
        const rows = [...document.querySelectorAll('[data-testid="bulk-rows"] li')];
        expect(rows[0].querySelector('.lb-an-row-title').textContent).toBe('Massive earth walls');
        expect(rows[0].querySelector('.lb-an-row-why').textContent).toBe('38 refs · 6 in the library');
        expect(rows[0].querySelector('svg').getAttribute('aria-label')).toBe('read');
        expect(rows[1].querySelector('.lb-an-row-why').textContent).toBe('No bibliography found');
        expect(rows[1].querySelector('svg').getAttribute('aria-label')).toBe('skipped');
        expect(dialog().textContent).not.toContain('no_ref_section');
        expect(q('[data-testid="bulk-stop"]').textContent.trim()).toBe('Stop');
        expect(dialog().textContent).toContain('Stopping ends the run. What has been read stays.');
        // Neither Esc nor the backdrop closes a running read.
        press('Escape');
        q('[data-testid="bulk-scrim"]').click();
        await flush();
        expect(dialog()).not.toBeNull();
        expect(dialog().getAttribute('data-state')).toBe('running');

        // Done: the summary from `results`, the skipped rows, rebuild.
        stream.push({ type: 'progress', step: 'paper_start', percent: 66, current_paper: 3, total_papers: 3 });
        stream.push({ type: 'paper_result', paper_id: 13, title: 'Earth building materials', status: 'file_not_found' });
        stream.push({
            type: 'complete', processed: 3, total_references: 2969, total_in_library: 214,
            results: [
                { paper_id: 11, title: 'Massive earth walls', status: 'ok', total_extracted: 38, in_library: 6 },
                { paper_id: 12, title: 'Building in the existing stock', status: 'no_ref_section', total_extracted: 0, in_library: 0 },
                { paper_id: 13, title: 'Earth building materials', status: 'file_not_found', total_extracted: 0, in_library: 0 },
            ],
        });
        stream.end();
        dlg = await vi.waitFor(() => {
            const el = dialog();
            if (!el || el.getAttribute('data-state') !== 'done') throw new Error('not done');
            return el;
        });
        expect(dlg.querySelector('h2').textContent).toBe('References read from PDFs');
        const facts = [...dlg.querySelectorAll('[data-testid="bulk-facts"] dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]);
        expect(facts).toEqual([
            ['3', 'items processed'],
            ['2,969', 'references found'],
            ['214', 'of them already in the library'],
            ['2', 'items skipped'],
        ]);
        const skipped = [...dlg.querySelectorAll('[data-testid="bulk-skipped"] li')].map((li) => [
            li.querySelector('.lb-an-row-title').textContent, li.querySelector('.lb-an-row-why').textContent]);
        expect(skipped).toEqual([
            ['Building in the existing stock', 'No bibliography found'],
            ['Earth building materials', 'PDF not found'],
        ]);
        expect(dlg.textContent).not.toMatch(/no_ref_section|file_not_found/);
        expect(dlg.textContent).toContain('The new references appear in the network once it is rebuilt.');
        expect(analysisText()).not.toContain('⟦');
        q('[data-testid="bulk-rebuild"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'POST' && c.url === '/api/analysis/build?depth=1')) throw new Error('no rebuild');
        });
        expect(dialog()).toBeNull();
        expect(alerts).not.toHaveBeenCalled();
    });
});
