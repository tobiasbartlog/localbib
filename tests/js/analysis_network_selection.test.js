// Knowledge network, selection (#242): a click on a cited work opens the card
// on the left - year, kind, title as h2, authors, citations, the own Items
// citing it (as links) and the abstract - and everything not involved steps
// back. The focus lands on the title; Esc closes the card and the focus goes
// back to the net. A plain cited work says so, a deeper one has no "Cited
// by". Every node has a hit area of at least 24 px on screen and none is a
// tab stop. A click on an own Item opens its detail view.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, preferReducedMotion } from './helpers.js';
import {
    ABSTRACT_ROUTE, analysisText, camScale, card, clickNode, dimmedIds, netNode, network, pressKey,
} from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.replace(/\s+/g, ' ').trim();

async function openCardOn(id) {
    clickNode(id);
    return vi.waitFor(() => {
        const el = card();
        if (!el || !el.querySelector('h2')) throw new Error('card not open');
        return el;
    });
}

describe('analysis: selecting a work in the net', () => {
    it('opens the card, dims the rest, closes with Esc and returns the focus', async () => {
        preferReducedMotion();
        await bootAnalysis([
            { method: 'POST', url: /^\/api\/analysis\/build\?depth=\d/, reply: network() },
            ABSTRACT_ROUTE,
        ]);
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });

        // Hit areas: at least 24 px on screen for every node; no tab stops.
        const hits = [...document.querySelectorAll('.lb-analyse .lb-an-hit')];
        expect(hits).toHaveLength(6);
        const k = camScale();
        for (const hit of hits) expect(Number(hit.getAttribute('r')) * 2 * k).toBeGreaterThanOrEqual(24 - 1e-9);
        expect(document.querySelector('.lb-analyse svg [tabindex]')).toBeNull();
        expect(dimmedIds()).toEqual([]);

        // A shared reference: the card with its head, the citing own Items
        // and the abstract; the focus is on the title.
        const c = await openCardOn('w1');
        const title = c.querySelector('h2');
        expect(title.textContent).toBe('Building with earth');
        expect(c.getAttribute('aria-labelledby')).toBe(title.id);
        await vi.waitFor(() => { if (document.activeElement !== title) throw new Error('focus not on the title'); });
        expect(text('[data-testid="analysis-card-year"]')).toBe('2006');
        expect(text('[data-testid="analysis-card-kind"]')).toBe('Shared reference');
        expect(text('[data-testid="analysis-card-citations"]')).toBe('412 citations according to OpenAlex');
        expect(q('[data-testid="analysis-card-citations"] .lb-an-n').textContent).toBe('412');
        const citedBy = [...c.querySelectorAll('h3')].map((h) => h.textContent.trim());
        expect(citedBy).toContain('Cited by 2 own items');
        const links = [...q('[data-testid="analysis-card-citers"]').querySelectorAll('a')];
        expect(links.map((a) => a.textContent.trim())).toEqual(['Earth construction basics', 'Printing with clay']);
        expect(links.map((a) => a.getAttribute('href'))).toEqual(['#/paper/1', '#/paper/2']);
        await vi.waitFor(() => {
            if (!q('[data-testid="analysis-card-abstract"]')) throw new Error('abstract not loaded');
        });
        expect(text('[data-testid="analysis-card-abstract"]')).toBe('How earth is built.');
        // Details | Chat with roles, a close key with its shortcut.
        const tabs = [...c.querySelectorAll('[role="tablist"] [role="tab"]')];
        expect(tabs.map((tab) => tab.textContent.trim())).toEqual(['Details', 'Chat']);
        expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['true', 'false']);
        const close = q('[data-testid="analysis-card-close"]');
        expect(close.getAttribute('aria-label')).toBe('Close');
        expect(close.getAttribute('aria-keyshortcuts')).toBe('Escape');
        // The foot: a DOI with its link, the key to add it.
        expect(text('[data-testid="node-link"]')).toBe('DOI ↗');
        expect(q('[data-testid="node-link"]').getAttribute('href')).toBe('https://doi.org/10.1000/earth');
        expect(text('[data-testid="node-add-to-library"]')).toBe('Add to the library');
        expect(analysisText()).not.toContain('Ask in chat');
        // The rest steps back; the selection carries its ring and title.
        expect(dimmedIds()).toEqual(['d1', 'w2', 'w3']);
        expect(q('.lb-analyse .lb-an-sel').getAttribute('display')).toBe('inline');
        expect(q('.lb-analyse .lb-an-sel-label').textContent).toBe('Building with earth');

        // Esc closes; the focus goes back to the net, nothing stays dimmed.
        pressKey('Escape');
        await flush();
        expect(card()).toBeNull();
        expect(document.activeElement).toBe(q('[data-testid="analysis-net"]'));
        expect(dimmedIds()).toEqual([]);
        expect(q('.lb-analyse .lb-an-sel').getAttribute('display')).toBe('none');

        // A plain cited work without a DOI: "Cited work", one citer, no
        // abstract from OpenAlex, Scholar instead of the DOI, nothing to add.
        await openCardOn('w2');
        expect(text('[data-testid="analysis-card-kind"]')).toBe('Cited work');
        expect([...card().querySelectorAll('h3')].map((h) => h.textContent.trim())).toContain('Cited by 1 own item');
        await vi.waitFor(() => {
            if (!q('[data-testid="analysis-card-abstract-none"]')) throw new Error('no-abstract note missing');
        });
        expect(text('[data-testid="analysis-card-abstract-none"]')).toBe('OpenAlex has no abstract for this work.');
        expect(text('[data-testid="node-link"]')).toBe('Scholar ↗');
        expect(q('[data-testid="node-add-to-library"]')).toBeNull();
        expect(dimmedIds()).toEqual(['p2', 'w1', 'w3']);

        // A deeper level: no own Item cites it directly, so no "Cited by".
        await openCardOn('d1');
        expect(card().querySelector('h2').textContent).toBe('Soil mechanics');
        expect(q('[data-testid="analysis-card-citers"]')).toBeNull();
        q('[data-testid="analysis-card-close"]').click();
        await flush();
        expect(card()).toBeNull();

        // An own Item opens its detail view.
        clickNode('p1');
        await vi.waitFor(() => {
            if (!window.location.hash.startsWith('#/paper/1')) throw new Error('no detail view');
        });
    });
});
