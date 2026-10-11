// Knowledge network, first run and after (#241): while the build answer is
// out a placeholder in the shape of the network shows the depth and the
// running time; then the head carries the meta line (the build's numbers in
// the UI locale), depth and the primary key ("Analyse at depth n" when the
// chosen depth is not the built one), the views are a tablist with counts,
// and the network is really drawn - nodes say what they are. A re-run keeps
// the network and shows a pill; a failure keeps it too. "Refresh citations"
// answers next to its key. Under reduced motion, as the tests run.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, holdRequests, preferReducedMotion } from './helpers.js';
import { analysisText, network, nodesOfKind } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.replace(/\s+/g, ' ').trim();

describe('analysis: first run, head, views and the drawn network', () => {
    it('shows each state and keeps the network through a failed re-run', async () => {
        preferReducedMotion();
        const alerts = vi.spyOn(window, 'alert').mockImplementation(() => {});
        const calls = await bootAnalysis([
            { method: 'POST', url: '/api/analysis/update-citations', reply: { updated: 3 } },
        ]);
        const builds = holdRequests((method, url) => method === 'POST' && url.startsWith('/api/analysis/build'));

        await vi.waitFor(() => { if (!q('[data-testid="analysis-empty"]')) throw new Error('empty state missing'); });
        q('[data-testid="analysis-empty"] [data-depth="3"]').click();
        await flush();
        q('[data-testid="analysis-start"]').click();

        // First run: the placeholder, announced politely, with the depth and
        // a clock that counts from the start in the SPA.
        const placeholder = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-first-run"]');
            if (!el) throw new Error('no placeholder');
            return el;
        });
        expect(builds.length).toBe(1);
        expect(builds[0].url).toBe('/api/analysis/build?depth=3');
        const live = placeholder.querySelector('[aria-live="polite"]');
        expect(live.querySelector('h2').textContent).toBe('Building the network');
        expect(live.textContent).toContain('three levels deep');
        expect(text('[data-testid="analysis-elapsed"]')).toMatch(/^Running for 0:0\d$/);
        expect(placeholder.querySelector('[role="timer"]')).not.toBeNull();
        expect(q('[data-testid="analysis-empty"]')).toBeNull();
        // Depth and the primary key sit in the head now, locked.
        const headRadios = [...document.querySelectorAll('[data-testid="analysis-head-depth"] [role="radio"]')];
        expect(headRadios).toHaveLength(5);
        expect(headRadios.every((r) => r.disabled)).toBe(true);
        expect(q('[data-testid="analysis-run"]').disabled).toBe(true);
        expect(text('[data-testid="analysis-run"]')).toBe('Analysing…');

        builds[0].reply(network());
        await vi.waitFor(() => { if (!nodesOfKind('own').length) throw new Error('network not drawn'); });
        expect(q('[data-testid="analysis-first-run"]')).toBeNull();

        // Meta line: scope, coverage, references with the PDF share, depth;
        // numbers in mono, formatted for the UI locale (en-US).
        expect(text('[data-testid="analysis-scope"]')).toBe('Scope: library');
        expect(text('[data-testid="analysis-meta-items"]')).toBe('34 own items, 21 found in OpenAlex');
        expect(text('[data-testid="analysis-meta-references"]')).toBe('3,749 references, 2,969 from PDF bibliographies');
        expect(text('[data-testid="analysis-meta-depth"]')).toBe('Network at depth 3');
        const nums = [...q('[data-testid="analysis-meta"]').querySelectorAll('.lb-an-n')].map((n) => n.textContent);
        expect(nums).toEqual(['34', '21', '3,749', '2,969', '3']);
        // The old tiles and toolbar are gone.
        expect(document.querySelector('.lb-analyse .lb-toolbar, .lb-analyse .lb-segmented, .lb-analyse [data-testid^="analysis-stat"]')).toBeNull();

        // Views: a tablist with a sliding thumb and counts in mono.
        const tablist = q('[data-testid="analysis-tabs"]');
        expect(tablist.getAttribute('role')).toBe('tablist');
        expect(tablist.querySelector('.lb-an-seg-thumb')).not.toBeNull();
        const tabs = [...tablist.querySelectorAll('[role="tab"]')];
        expect(tabs.map((tab) => tab.childNodes[0].textContent.trim())).toEqual(['Network', 'Own items', 'Cited works', 'Missing']);
        expect(tabs.map((tab) => (tab.querySelector('.lb-an-n') || { textContent: '' }).textContent)).toEqual(['', '2', '500+', '13']);
        expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false', 'false']);

        // The network is drawn: every node says what it is, nothing dimmed.
        const circles = [...document.querySelectorAll('.lb-analyse circle[data-node-id]')];
        expect(circles.map((c) => c.getAttribute('data-node-id')).sort()).toEqual(['d1', 'p1', 'p2', 'w1', 'w2', 'w3']);
        expect(nodesOfKind('own').map((c) => c.getAttribute('data-node-id')).sort()).toEqual(['p1', 'p2']);
        expect(nodesOfKind('shared').map((c) => c.getAttribute('data-node-id'))).toEqual(['w1']);
        expect(nodesOfKind('external').map((c) => c.getAttribute('data-node-id')).sort()).toEqual(['w2', 'w3']);
        expect(nodesOfKind('deeper').map((c) => c.getAttribute('data-node-id'))).toEqual(['d1']);
        expect(circles.every((c) => c.getAttribute('data-dimmed') === 'false')).toBe(true);
        expect(circles.every((c) => Number.isFinite(Number(c.getAttribute('cx'))))).toBe(true);

        // The primary key names a depth that differs from the built one.
        const run = q('[data-testid="analysis-run"]');
        expect(text('[data-testid="analysis-run"]')).toBe('Analyse again');
        q('[data-testid="analysis-head-depth"] [data-depth="4"]').click();
        await flush();
        expect(text('[data-testid="analysis-run"]')).toBe('Analyse at depth 4');
        expect(text('[data-testid="analysis-head-depth"] .lb-an-depth-name')).toBe('Four levels');
        q('[data-testid="analysis-head-depth"] [data-depth="3"]').click();
        await flush();
        expect(text('[data-testid="analysis-run"]')).toBe('Analyse again');

        // A re-run leaves the network standing with a pill and its clock ...
        q('[data-testid="analysis-head-depth"] [data-depth="4"]').click();
        await flush();
        run.click();
        const pill = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-rerun-pill"]');
            if (!el) throw new Error('no re-run pill');
            return el;
        });
        expect(builds.length).toBe(2);
        expect(builds[1].url).toBe('/api/analysis/build?depth=4');
        expect(pill.textContent).toMatch(/Analysing…\s*0:0\d/);
        expect(nodesOfKind('own')).toHaveLength(2);
        expect(q('[data-testid="analysis-first-run"]')).toBeNull();
        // ... and a failure says why above the surface - the network stays.
        builds[1].reply({ detail: 'Upstream down' }, 502);
        const notice = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-error"]');
            if (!el) throw new Error('no error notice');
            return el;
        });
        expect(notice.getAttribute('role')).toBe('alert');
        expect(notice.textContent).toContain('Analysis failed: Upstream down');
        expect(q('[data-testid="analysis-rerun-pill"]')).toBeNull();
        expect(nodesOfKind('own')).toHaveLength(2);
        expect(text('[data-testid="analysis-meta-depth"]')).toBe('Network at depth 3');
        expect(text('[data-testid="analysis-run"]')).toBe('Analyse at depth 4');

        // Arrow keys walk the views; "Refresh citations" answers beside its key.
        tabs[0].focus();
        tabs[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        await flush();
        expect(tabs[1].getAttribute('aria-selected')).toBe('true');
        expect(document.activeElement).toBe(tabs[1]);
        q('[data-testid="analysis-refresh-citations"]').click();
        await vi.waitFor(() => { if (builds.length < 3) throw new Error('no rebuild after refresh'); });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/analysis/update-citations')).toBe(true);
        builds[2].reply(network());
        const note = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-citations-note"]');
            if (!el || !el.textContent) throw new Error('no note');
            return el;
        });
        expect(note.getAttribute('role')).toBe('status');
        expect(note.textContent).toBe('3 items updated.');
        await vi.waitFor(() => { if (q('[data-testid="analysis-error"]')) throw new Error('error still shown'); });

        // The tables keep their content; the column formerly "Source" names
        // the provenance now, and nothing on the page lacks its text.
        tabs[2].click();
        await flush();
        expect(text('#lb-an-panel-references thead')).toContain('Provenance');
        expect(document.querySelectorAll('#lb-an-panel-references tbody tr')).toHaveLength(500);
        expect(analysisText()).not.toContain('⟦');
        expect(analysisText()).not.toMatch(/\bLLM\b/);
        expect(alerts).not.toHaveBeenCalled();
    });
});
