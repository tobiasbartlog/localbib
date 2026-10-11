// Knowledge network, search (#242): an ARIA combobox. Typing lists the hits
// (own Items first, then shared references, then cited works) with a status
// line; ↓ moves the active option, Enter picks it like a click - the card
// opens - and Esc on the card brings the focus back to the search. A hit is
// picked by `click`, never by `mousedown`; a click elsewhere closes the
// list, no blur timer does. "/" focuses the search while no field has it,
// and Enter on an own Item opens its detail view.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, preferReducedMotion, typeInto } from './helpers.js';
import { ABSTRACT_ROUTE, card, netNode, network, pressKey } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const options = () => [...document.querySelectorAll('[data-testid="analysis-search-list"] [role="option"]')];
const optionTitles = () => options().map((o) => o.querySelector('.lb-an-opt-title').textContent);

describe('analysis: searching the net', () => {
    it('chooses with the arrows, jumps with Enter and closes without a timer', async () => {
        preferReducedMotion();
        await bootAnalysis([
            { method: 'POST', url: /^\/api\/analysis\/build\?depth=\d/, reply: network() },
            ABSTRACT_ROUTE,
        ]);
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });

        const input = q('[data-testid="analysis-search"]');
        expect(input.getAttribute('role')).toBe('combobox');
        expect(input.getAttribute('aria-autocomplete')).toBe('list');
        expect(input.getAttribute('aria-expanded')).toBe('false');
        expect(input.getAttribute('aria-keyshortcuts')).toBe('/');
        expect(input.getAttribute('aria-label')).toBe('Search the network');
        expect(input.getAttribute('placeholder')).toBe('Search the network and jump there…');

        // "/" jumps into the search while no field has the focus.
        document.activeElement.blur();
        pressKey('/');
        expect(document.activeElement).toBe(input);

        // Typing: three hits, own Item first, the first one active.
        typeInto(input, 'earth');
        await flush();
        const list = q('[data-testid="analysis-search-list"]');
        expect(list.getAttribute('role')).toBe('listbox');
        expect(input.getAttribute('aria-controls')).toBe(list.id);
        expect(input.getAttribute('aria-expanded')).toBe('true');
        expect(optionTitles()).toEqual(['Earth construction basics', 'Building with earth', 'Rammed earth walls']);
        expect(input.getAttribute('aria-activedescendant')).toBe(options()[0].id);
        expect(options().map((o) => o.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
        const status = q('[data-testid="analysis-search-status"]');
        expect(status.getAttribute('role')).toBe('status');
        expect(status.textContent).toBe('3 matches · ↑ ↓ to choose · Enter jumps there');

        // ↓ makes the second hit active, Enter picks it like a click.
        pressKey('ArrowDown', input);
        await flush();
        expect(input.getAttribute('aria-activedescendant')).toBe(options()[1].id);
        expect(options()[1].getAttribute('aria-selected')).toBe('true');
        pressKey('Enter', input);
        const c = await vi.waitFor(() => {
            const el = card();
            if (!el) throw new Error('card not open');
            return el;
        });
        expect(c.querySelector('h2').textContent).toBe('Building with earth');
        await vi.waitFor(() => { if (document.activeElement !== c.querySelector('h2')) throw new Error('focus not on the title'); });
        expect(input.value).toBe('');
        expect(input.getAttribute('aria-expanded')).toBe('false');
        expect(q('[data-testid="analysis-search-list"]')).toBeNull();

        // Esc closes the card; the focus returns to the search it came from.
        pressKey('Escape');
        await flush();
        expect(card()).toBeNull();
        expect(document.activeElement).toBe(input);

        // A click (and only a click) picks a hit.
        typeInto(input, 'clay');
        await flush();
        expect(optionTitles()).toEqual(['Printing with clay', 'Clay plasters']);
        options()[1].dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        await flush();
        expect(card()).toBeNull();
        options()[1].click();
        await vi.waitFor(() => { if (!card()) throw new Error('card not open'); });
        expect(card().querySelector('h2').textContent).toBe('Clay plasters');

        // The list stays open on blur and closes on a click elsewhere or Esc.
        typeInto(input, 'earth');
        await flush();
        input.blur();
        await new Promise((r) => setTimeout(r, 250));
        expect(input.getAttribute('aria-expanded')).toBe('true');
        document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        await flush();
        expect(input.getAttribute('aria-expanded')).toBe('false');
        input.focus();
        typeInto(input, 'earth soil');
        await flush();
        expect(q('[data-testid="analysis-search-status"]').textContent).toBe('No matches');
        typeInto(input, 'rammed');
        await flush();
        expect(q('[data-testid="analysis-search-status"]').textContent).toBe('1 match · ↑ ↓ to choose · Enter jumps there');
        pressKey('Escape', input);
        await flush();
        expect(input.getAttribute('aria-expanded')).toBe('false');
        // Esc on the open list closed only the list, not the card.
        expect(card()).not.toBeNull();

        // Enter on an own Item does what its click does: its detail view.
        typeInto(input, 'printing');
        await flush();
        pressKey('Enter', input);
        await vi.waitFor(() => {
            if (!window.location.hash.startsWith('#/paper/2')) throw new Error('no detail view');
        });
    });
});
