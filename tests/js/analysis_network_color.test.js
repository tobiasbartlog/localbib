// Knowledge network, legend, "View" and colour (#242): the legend in the bar
// lists what is drawn - own Item, shared reference, reference, further
// level, size, cited and PDF edges (solid, with their own class). "View"
// opens a popover with "Colour by" and the filters; Esc, a click beside it or
// its key close it. Coloured by publication year the own Items stay ink with
// their ring, the others are warm grey and no node is filled orange; the
// colouring survives a selection, closing it and "Show the whole network",
// and comes back after a detail view, the popover with it. The zoom keys and
// their shortcuts zoom.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, goTo, preferReducedMotion } from './helpers.js';
import { ABSTRACT_ROUTE, camScale, card, clickNode, dimmedIds, netNode, network, pressKey } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.replace(/\s+/g, ' ').trim();
const legend = () => [...q('[data-testid="analysis-legend"]').querySelectorAll('li')].map((li) => li.textContent.replace(/\s+/g, ' ').trim());
const style = (id) => netNode(id).getAttribute('style');
const yearColoured = () => ['w2', 'w3', 'd1'].every((id) => style(id).includes('color-mix(in oklab, var(--lb-ink-2)'));

describe('analysis: legend, view and colour', () => {
    it('colours by year in place and keeps it through selection, reset and a detail view', async () => {
        preferReducedMotion();
        await bootAnalysis([
            { method: 'POST', url: /^\/api\/analysis\/build\?depth=\d/, reply: network() },
            ABSTRACT_ROUTE,
        ]);
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });

        // The legend says what is drawn; PDF edges are solid, with a class.
        const list = q('[data-testid="analysis-legend"]');
        expect(list.getAttribute('aria-label')).toBe('Legend');
        expect(legend()).toEqual(['Own item', 'Shared reference', 'Reference', 'Further level', 'Size = citations', 'Cites', 'From PDF bibliography']);
        const pdfEdges = [...document.querySelectorAll('.lb-analyse line.lb-an-edge-pdf')];
        expect(pdfEdges).toHaveLength(1);
        for (const line of pdfEdges) {
            expect(line.getAttribute('stroke-dasharray')).toBeNull();
            expect(line.getAttribute('style')).not.toContain('dasharray');
            expect(line.getAttribute('marker-end')).toBe('url(#lb-an-arrow-pdf)');
        }
        expect(text('[data-testid="analysis-hint"]')).toBe('Scroll zooms · Drag pans · / searches');
        expect(q('[data-testid="analysis-hint"] kbd').textContent).toBe('/');

        // Zoom keys with their shortcuts; + zooms in, - out, about the free area.
        expect(q('[data-testid="analysis-zoom-in"]').getAttribute('aria-keyshortcuts')).toBe('+');
        expect(q('[data-testid="analysis-zoom-out"]').getAttribute('aria-keyshortcuts')).toBe('-');
        expect(q('[data-testid="analysis-zoom-fit"]').getAttribute('aria-keyshortcuts')).toBe('0');
        expect(q('[data-testid="analysis-zoom-fit"]').getAttribute('aria-label')).toBe('Show the whole network');
        const k0 = camScale();
        q('[data-testid="analysis-zoom-in"]').click();
        expect(camScale()).toBeCloseTo(k0 * 1.25, 5);
        document.activeElement.blur();
        pressKey('-');
        expect(camScale()).toBeCloseTo(k0, 5);
        pressKey('+');
        expect(camScale()).toBeCloseTo(k0 * 1.25, 5);
        pressKey('0');
        expect(camScale()).toBeCloseTo(k0, 5);

        // "View": a popover with "Colour by" and the filters.
        const viewKey = q('[data-testid="analysis-view-key"]');
        expect(viewKey.getAttribute('aria-expanded')).toBe('false');
        viewKey.click();
        await flush();
        const pop = q('[data-testid="analysis-view-pop"]');
        expect(viewKey.getAttribute('aria-expanded')).toBe('true');
        expect(viewKey.getAttribute('aria-controls')).toBe(pop.id);
        expect(pop.getAttribute('role')).toBe('dialog');
        const group = q('[data-testid="analysis-color-by"]');
        expect(group.getAttribute('role')).toBe('radiogroup');
        expect(document.getElementById(group.getAttribute('aria-labelledby')).textContent).toBe('Colour by');
        const radios = [...group.querySelectorAll('[role="radio"]')];
        expect(radios.map((r) => r.textContent.trim())).toEqual(['Kind', 'Publication year']);
        expect(radios.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false']);
        const filters = [...pop.querySelectorAll('.lb-an-range')].map((row) => ({
            label: row.querySelector('label').textContent.trim(),
            value: row.querySelector('.lb-an-n').textContent.trim(),
        }));
        expect(filters).toEqual([
            { label: 'Path between two items', value: '1 step' },
            { label: 'Cited at least', value: 'off' },
            { label: 'References per item', value: 'all' },
            { label: 'Node spacing', value: '25' },
        ]);
        const path = q('[data-testid="analysis-filter-minrefs"]');
        expect(document.getElementById(path.getAttribute('aria-describedby')).textContent)
            .toBe('How many steps away a reference may be and still count as shared.');

        // Esc, a click beside it and its key close it.
        pressKey('Escape');
        await flush();
        expect(q('[data-testid="analysis-view-pop"]')).toBeNull();
        expect(document.activeElement).toBe(viewKey);
        viewKey.click();
        await flush();
        document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        await flush();
        expect(q('[data-testid="analysis-view-pop"]')).toBeNull();
        viewKey.click();
        await flush();
        viewKey.click();
        await flush();
        expect(q('[data-testid="analysis-view-pop"]')).toBeNull();

        // By publication year: the legend changes, the own Items do not.
        const ownBefore = [style('p1'), style('p2')];
        viewKey.click();
        await flush();
        q('[data-testid="analysis-color-by"] [data-color-by="year"]').click();
        await flush();
        expect(legend()).toEqual(['Own item', 'older newer', 'Shared reference', 'Size = citations', 'Cites', 'From PDF bibliography']);
        expect(text('[data-testid="analysis-view-key"]')).toBe('View Year');
        expect([style('p1'), style('p2')]).toEqual(ownBefore);
        expect(netNode('p1').getAttribute('data-node-kind')).toBe('own');
        expect(yearColoured()).toBe(true);
        // Orange fills nothing; the shared reference keeps an orange outline.
        for (const c of document.querySelectorAll('.lb-analyse circle[data-node-id]')) {
            expect(c.getAttribute('style')).not.toContain('fill: var(--lb-accent)');
        }
        expect(style('w1')).toContain('stroke: var(--lb-accent)');
        expect(netNode('w1').getAttribute('data-node-kind')).toBe('shared');

        // It survives a selection, closing it and "Show the whole network".
        clickNode('w2');
        await vi.waitFor(() => { if (!card()) throw new Error('card not open'); });
        expect(q('[data-testid="analysis-view-pop"]')).toBeNull();
        expect(yearColoured()).toBe(true);
        expect([style('p1'), style('p2')].map((s) => s.replace(/opacity:[^;]*;?/, ''))).toEqual(ownBefore.map((s) => s.replace(/opacity:[^;]*;?/, '')));
        pressKey('Escape');
        await flush();
        expect(yearColoured()).toBe(true);
        clickNode('w3');
        await vi.waitFor(() => { if (!card()) throw new Error('card not open'); });
        q('[data-testid="analysis-zoom-fit"]').click();
        await flush();
        expect(card()).toBeNull();
        expect(yearColoured()).toBe(true);
        expect(legend()[1]).toBe('older newer');

        // ... and a detail view and back, with the popover, the lens and the
        // chosen search depth as they were; the camera fits anew.
        viewKey.click();
        await flush();
        q('[data-testid="analysis-lens"]').click();
        q('[data-testid="analysis-head-depth"] [data-depth="4"]').click();
        await flush();
        goTo('#/');
        await vi.waitFor(() => { if (q('.lb-analyse')) throw new Error('still on the analysis page'); });
        goTo('#/analyse');
        await vi.waitFor(() => { if (!netNode('w2')) throw new Error('net not redrawn'); });
        expect(q('[data-testid="analysis-view-pop"]')).not.toBeNull();
        expect(q('[data-testid="analysis-color-by"] [data-color-by="year"]').getAttribute('aria-checked')).toBe('true');
        expect(legend()[1]).toBe('older newer');
        expect(yearColoured()).toBe(true);
        expect(q('[data-testid="analysis-lens"]').getAttribute('aria-pressed')).toBe('true');
        expect(dimmedIds()).toEqual(['d1', 'w2', 'w3']);
        expect(q('[data-testid="analysis-head-depth"] [data-depth="4"]').getAttribute('aria-checked')).toBe('true');
        expect(text('[data-testid="analysis-run"]')).toBe('Analyse at depth 4');

        // Back to kind: the type colours return in place.
        q('[data-testid="analysis-color-by"] [data-color-by="type"]').click();
        await flush();
        expect(style('w1')).toContain('fill: var(--lb-accent)');
        expect(style('d1')).toContain('fill: var(--lb-bg-elev)');
        expect(style('w2')).toContain('fill: var(--lb-node-ext)');
        expect(legend()[2]).toBe('Reference');
    });
});
