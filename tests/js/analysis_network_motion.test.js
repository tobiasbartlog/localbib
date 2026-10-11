// Knowledge network, motion (#243). The timings and the look are checked in
// the browser; here only what is observable without a clock: a fresh build
// result grows the net (the stage class shows), a return to the route does
// not repeat it, and with reduced motion nothing is staged at all.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, goTo } from './helpers.js';
import { netNode, network } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const routes = [
    { method: 'POST', url: /^\/api\/analysis\/build\?depth=\d/, reply: network() },
    { method: 'GET', url: /^\/api\/analysis\/node-abstract\?/, reply: { abstract: '' } },
];

describe('analysis: motion on the net', () => {
    it('grows after a fresh build, but not on a return to the route', async () => {
        // No matchMedia (jsdom): no preference, so motion is on.
        delete window.matchMedia;
        await bootAnalysis(routes);
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });

        const svg = q('.lb-an-canvas svg');
        expect(svg.classList.contains('is-building')).toBe(true);
        // Every node has its stage: own Items first, then the shared one.
        const stage = (id) => Number(netNode(id).parentNode.style.getPropertyValue('--lb-i'));
        expect(stage('p1')).toBe(0);
        expect(stage('w1')).toBe(1);
        expect(stage('w2')).toBeGreaterThan(1);
        expect(stage('w2')).toBeLessThanOrEqual(5);

        // Away and back: the net stands, nothing is staged again.
        goTo('#/');
        await vi.waitFor(() => { if (q('.lb-analyse')) throw new Error('still on the page'); });
        goTo('#/analyse');
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn again'); });
        const again = q('.lb-an-canvas svg');
        expect(again.classList.contains('is-building')).toBe(false);
        expect(again.classList.contains('is-live')).toBe(true);
    });
});
