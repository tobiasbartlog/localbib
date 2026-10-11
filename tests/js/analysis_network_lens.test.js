// Knowledge network, the lens and the tools (#242): "Shared references" is a
// chip with aria-pressed whose number is the number of nodes drawn as shared
// - it follows "Path between two items" in the "View" popover. Pressed, every
// node but the shared references and the own Items steps back; a selection
// releases it. The category select has a hidden label and names a
// subcategory with its path; "Own items" lists them as links.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, preferReducedMotion, typeInto } from './helpers.js';
import { card, clickNode, dimmedIds, netNode, network, nodesOfKind, pressKey } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.replace(/\s+/g, ' ').trim();

// One more edge, w3 -> d1: two steps away from both own Items, so d1 turns
// shared once the path may be two steps long. p1 sits in a subcategory.
function lensNetwork() {
    const base = network();
    base.nodes = base.nodes.map((n) => (n.id === 'p1' ? { ...n, categories: [{ id: 7, name: 'Materials' }] } : n));
    base.edges = [...base.edges, { source: 'w3', target: 'd1', edge_type: 'cites' }];
    return base;
}

describe('analysis: the lens and the tools', () => {
    it('counts the shared references, follows the path slider and yields to a selection', async () => {
        preferReducedMotion();
        await bootAnalysis([
            { method: 'POST', url: /^\/api\/analysis\/build\?depth=\d/, reply: lensNetwork() },
            { method: 'GET', url: '/api/categories', reply: [{ id: 5, name: 'Earth', parent_id: null }, { id: 7, name: 'Materials', parent_id: 5 }] },
            { method: 'GET', url: /^\/api\/analysis\/node-abstract\?/, reply: { abstract: '' } },
        ]);
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });

        // The lens: a chip whose number is the number of shared nodes.
        const lens = q('[data-testid="analysis-lens"]');
        expect(lens.getAttribute('aria-pressed')).toBe('false');
        expect(lens.textContent).toContain('Shared references');
        expect(text('[data-testid="analysis-lens-count"]')).toBe(String(nodesOfKind('shared').length));
        expect(nodesOfKind('shared').map((c) => c.getAttribute('data-node-id'))).toEqual(['w1']);
        lens.click();
        await flush();
        expect(lens.getAttribute('aria-pressed')).toBe('true');
        expect(dimmedIds()).toEqual(['d1', 'w2', 'w3']);

        // "Path between two items" at two steps makes d1 shared as well:
        // the number and the drawing follow, the lens stays on.
        q('[data-testid="analysis-view-key"]').click();
        await flush();
        const slider = q('[data-testid="analysis-filter-minrefs"]');
        expect(text('[data-testid="analysis-filter-minrefs-value"]')).toBe('1 step');
        typeInto(slider, '2');
        await flush();
        expect(text('[data-testid="analysis-filter-minrefs-value"]')).toBe('2 steps');
        expect(nodesOfKind('shared').map((c) => c.getAttribute('data-node-id')).sort()).toEqual(['d1', 'w1']);
        expect(text('[data-testid="analysis-lens-count"]')).toBe('2');
        expect(dimmedIds()).toEqual(['w2', 'w3']);
        // The bridges follow too: two edges lead to w1, four (two steps each
        // side) to d1.
        expect(document.querySelectorAll('.lb-analyse line[data-edge-kind="bridge"]')).toHaveLength(6);
        typeInto(slider, '1');
        await flush();
        expect(text('[data-testid="analysis-lens-count"]')).toBe('1');
        expect(dimmedIds()).toEqual(['d1', 'w2', 'w3']);
        pressKey('Escape');
        await flush();
        expect(q('[data-testid="analysis-view-pop"]')).toBeNull();

        // A selection releases the lens.
        clickNode('w2');
        await vi.waitFor(() => { if (!card()) throw new Error('card not open'); });
        expect(lens.getAttribute('aria-pressed')).toBe('false');
        expect(dimmedIds()).toEqual(['p2', 'w1', 'w3']);
        // ... and the lens ends the selection.
        lens.click();
        await flush();
        expect(card()).toBeNull();
        expect(lens.getAttribute('aria-pressed')).toBe('true');
        lens.click();
        await flush();
        expect(dimmedIds()).toEqual([]);

        // Category: a hidden label, a subcategory with its path; choosing it
        // brings its Items and their neighbours forward.
        const select = q('[data-testid="analysis-category"]');
        const label = document.querySelector(`label[for="${select.id}"]`);
        expect(label.textContent).toBe('Category');
        expect(label.classList.contains('lb-an-sr')).toBe(true);
        await vi.waitFor(() => {
            const opts = [...select.options].map((o) => o.textContent.trim());
            if (!opts.includes('Earth › Materials')) throw new Error('path missing: ' + opts.join('|'));
        });
        expect(select.options[0].textContent.trim()).toBe('All categories');
        select.value = '7';
        select.dispatchEvent(new Event('change'));
        await flush();
        expect(dimmedIds()).toEqual(['d1', 'p2', 'w3']);
        select.value = '';
        select.dispatchEvent(new Event('change'));
        await flush();
        expect(dimmedIds()).toEqual([]);

        // "Own items (n)": its list links to the Items.
        const own = q('[data-testid="analysis-own-items"]');
        expect(own.textContent.replace(/\s+/g, ' ').trim()).toBe('Own items 2');
        own.click();
        await flush();
        expect(own.getAttribute('aria-expanded')).toBe('true');
        const ownLinks = [...q('[data-testid="analysis-own-list"]').querySelectorAll('a')];
        expect(ownLinks.map((a) => a.getAttribute('href'))).toEqual(['#/paper/1', '#/paper/2']);
        // Under 20 Items no filter field.
        expect(q('[data-testid="analysis-own-list"] input')).toBeNull();
        document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        await flush();
        expect(q('[data-testid="analysis-own-list"]')).toBeNull();
    });
});
