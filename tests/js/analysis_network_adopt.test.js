// Knowledge network, adopting a cited work (#242): in the Library scope the
// card's foot adds it to the library (POST /api/papers/by-doi), in a
// Collection to that Collection (POST /api/collections/{id}/items/by-doi) -
// only with a DOI. The answer says what happened and whether a PDF came; the
// key then opens the new Item. Without an AI model the chat tab explains it
// and leads to the settings, and nothing is sent.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, goTo, preferReducedMotion } from './helpers.js';
import { ABSTRACT_ROUTE, card, clickNode, netNode, network } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.replace(/\s+/g, ' ').trim();

async function openCardOn(id) {
    clickNode(id);
    return vi.waitFor(() => {
        const el = card();
        if (!el) throw new Error('card not open');
        return el;
    });
}

describe('analysis: adopting a cited work', () => {
    it('adds to the library or the Collection, and the chat says when no model is there', async () => {
        preferReducedMotion();
        const coll = { id: 4, name: 'Paper X', description: '', archived: false, item_count: 2, pdf_count: 1 };
        const calls = await bootAnalysis([
            { method: 'POST', url: /^\/api\/analysis\/build\?depth=\d/, reply: network() },
            ABSTRACT_ROUTE,
            { method: 'POST', url: '/api/papers/by-doi', reply: { doi: '10.1000/earth', created: true, paper_id: 9, citekey: 'Minke2006', pdf: 'fetched' } },
            { method: 'GET', url: '/api/collections/4', reply: coll },
            { method: 'POST', url: '/api/collections/4/items/by-doi', reply: { paper_id: 9, doi: '10.1000/earth', citekey: 'Minke2006', created: false, added: true, has_pdf: false } },
        ]);
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });

        // Library scope: "Add to the library" calls the DOI intake.
        await openCardOn('w1');
        const add = q('[data-testid="node-add-to-library"]');
        expect(add.textContent.trim()).toBe('Add to the library');
        add.click();
        const note = await vi.waitFor(() => {
            const el = q('[data-testid="node-add-result"]');
            if (!el || !el.textContent) throw new Error('no answer');
            return el;
        });
        const post = calls.find((c) => c.method === 'POST' && c.url === '/api/papers/by-doi');
        expect(post.body).toMatchObject({ doi: '10.1000/earth', title: 'Building with earth', year: 2006 });
        expect(note.getAttribute('role')).toBe('status');
        expect(note.textContent).toBe('Added · PDF found');
        // The key now opens the new Item.
        expect(q('[data-testid="node-add-to-library"]')).toBeNull();
        expect(text('[data-testid="node-open-details"]')).toBe('Open details');
        expect(q('[data-testid="node-open-details"]').getAttribute('href')).toBe('#/paper/9');

        // Without an AI model the chat tab says so - no input, no call.
        q('[data-testid="analysis-card-tab-chat"]').click();
        const box = await vi.waitFor(() => {
            const el = q('[data-testid="analysis-chat-nomodel"]');
            if (!el) throw new Error('no-model note missing');
            return el;
        });
        expect(box.textContent).toContain('No AI model connected');
        expect(box.textContent).toContain('For the chat, LocalBib needs an AI model. The network and everything else work without one.');
        expect(q('[data-testid="analysis-chat-setup"]').getAttribute('href')).toBe('#/settings/ai-models');
        expect(q('[data-testid="analysis-chat-input"]')).toBeNull();
        expect(q('[data-testid="analysis-chat-quick"]')).toBeNull();
        expect(calls.some((c) => c.url === '/api/analysis/chat')).toBe(false);
        expect(card().getAttribute('data-card-tab')).toBe('chat');

        // In a Collection the key adds to it.
        goTo('#/analyse?collection_id=4');
        await vi.waitFor(() => {
            const el = q('[data-testid="analysis-scope"]');
            if (!el || !el.textContent.includes('Paper X')) throw new Error('scope not switched');
            if (!q('[data-testid="analysis-start"]')) throw new Error('no start');
        });
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });
        await openCardOn('w1');
        const toColl = q('[data-testid="node-add-to-collection"]');
        // The key names no Collection (a long name left the card); the
        // tooltip still does.
        expect(toColl.textContent.trim()).toBe('Add to the collection');
        expect(toColl.getAttribute('title')).toBe('Add to “Paper X”');
        toColl.click();
        await vi.waitFor(() => {
            const el = q('[data-testid="node-add-result"]');
            if (!el || !el.textContent) throw new Error('no answer');
        });
        const collPost = calls.find((c) => c.method === 'POST' && c.url === '/api/collections/4/items/by-doi');
        expect(collPost.body).toMatchObject({ doi: '10.1000/earth', title: 'Building with earth' });
        expect(text('[data-testid="node-add-result"]')).toBe('Already in the library, added · without PDF');
        expect(q('[data-testid="node-open-details"]').getAttribute('href')).toBe('#/paper/9');
        await flush();
    });
});
