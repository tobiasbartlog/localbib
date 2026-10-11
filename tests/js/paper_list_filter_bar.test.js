// The item list's filter bar: one pill per filter, each with its popover.
// Activating a filter shows the pill's ×, the × clears it, Esc and a press
// outside close a popover, a year thumb back on its bound is no filter, the
// category rail toggles, and semantic search locks what it ignores.
// One boot per file (the SPA mounts itself once), so the cases run in sequence.
import { describe, expect, it, vi } from 'vitest';
import {
    defaultRoutes, filterPill, filterPillActive, flush, installFetchMock, openFilterPill,
} from './helpers.js';

const PAPERS = [
    { id: 1, title: 'Alpha', authors: 'A', year: 2015, doi: '10.1/a', filename: 'a.pdf', original_filename: 'a.pdf',
      categories: [{ id: 1, name: 'Methods' }] },
    { id: 2, title: 'Beta', authors: 'B', year: 2019, doi: '', filename: '', original_filename: 'BibTeX-Import: refs.bib',
      categories: [{ id: 1, name: 'Methods' }, { id: 2, name: 'Theory' }] },
    { id: 3, title: 'Gamma', authors: 'C', year: 2022, doi: '10.1/c', filename: 'c.pdf', original_filename: 'c.pdf',
      categories: [{ id: 2, name: 'Theory' }] },
    { id: 4, title: 'Delta', authors: 'D', year: 2024, doi: '10.1/d', filename: '', original_filename: 'RIS-Import: x.ris',
      categories: [{ id: 2, name: 'Theory' }] },
];

const q = (sel) => document.querySelector(sel);
const shown = () => [...document.querySelectorAll('li[data-paper-id]')].map((li) => Number(li.getAttribute('data-paper-id'))).sort();
const meta = () => q('.lb-page-meta').textContent.replace(/\s+/g, ' ').trim();
const wrap = (key) => filterPill(key).closest('.lb-fbar-pw');
const pop = (key) => document.getElementById(`lb-fbar-pop-${key}`);

function setRange(input, value) {
    input.value = String(value);
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new Event('change'));
}

describe('item list: filter bar', () => {
    it('applies, clears and closes like a filter bar should', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/llm/status', reply: { reasoning: true, fast: true, embedding: true, connections: 1 } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await vi.waitFor(() => {
            if (shown().length !== 4) throw new Error('list not rendered');
        });
        expect(meta()).toMatch(/^4 items/);

        // --- Activating a filter: accent, ×, live list, "x of y" ------------
        const pdfValue = filterPill('pdf').querySelector('.lb-fbar-pv');
        const slotWidth = pdfValue.style.width;
        expect(slotWidth).toMatch(/ch$/);
        expect(pdfValue.textContent.trim()).toBe('any');
        expect(wrap('pdf').querySelector('.lb-fbar-clr')).toBeNull();

        const pdfPop = await openFilterPill('pdf');
        expect(filterPill('pdf').getAttribute('aria-expanded')).toBe('true');
        expect(filterPill('pdf').getAttribute('aria-controls')).toBe('lb-fbar-pop-pdf');
        await flush();
        // Focus moved into the popover, onto the checked option.
        expect(document.activeElement.getAttribute('data-value')).toBe('');
        expect(pdfPop.textContent).toContain('With PDF');
        pdfPop.querySelector('[role="radio"][data-value="yes"]').click();
        await flush();
        expect(shown()).toEqual([1, 3]);
        expect(filterPillActive('pdf')).toBe(true);
        expect(pdfValue.textContent.trim()).toBe('yes');
        expect(pdfValue.style.width).toBe(slotWidth);   // the pill keeps its width
        expect(wrap('pdf').querySelector('.lb-fbar-clr')).not.toBeNull();
        expect(meta()).toMatch(/^2 of 4 items/);
        expect(q('[data-testid="filter-reset"]')).not.toBeNull();

        // --- The × clears exactly that filter and hands focus to the pill ---
        wrap('pdf').querySelector('.lb-fbar-clr').click();
        await flush();
        expect(filterPillActive('pdf')).toBe(false);
        expect(wrap('pdf').querySelector('.lb-fbar-clr')).toBeNull();
        expect(pdfValue.textContent.trim()).toBe('any');
        expect(pop('pdf')).toBeNull();
        expect(document.activeElement).toBe(filterPill('pdf'));
        expect(shown()).toEqual([1, 2, 3, 4]);
        expect(meta()).toMatch(/^4 items/);
        expect(q('[data-testid="filter-reset"]')).toBeNull();

        // --- Esc closes the popover and returns focus to its pill -----------
        await openFilterPill('provenance');
        await flush();
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await flush();
        expect(pop('provenance')).toBeNull();
        expect(filterPill('provenance').getAttribute('aria-expanded')).toBe('false');
        expect(document.activeElement).toBe(filterPill('provenance'));

        // --- Only one open at a time; a press inside keeps it, outside closes
        await openFilterPill('doi');
        await openFilterPill('provenance');
        expect(pop('doi')).toBeNull();
        pop('provenance').querySelector('[role="radio"]').dispatchEvent(new Event('pointerdown', { bubbles: true }));
        await flush();
        expect(pop('provenance')).not.toBeNull();
        // A pick from a radio list applies and closes.
        pop('provenance').querySelector('[role="radio"][data-value="bibtex"]').click();
        await flush();
        expect(pop('provenance')).toBeNull();
        expect(shown()).toEqual([2]);
        expect(filterPill('provenance').querySelector('.lb-fbar-pv').textContent.trim()).toBe('BibTeX');
        await openFilterPill('doi');
        q('.lb-page-title').dispatchEvent(new Event('pointerdown', { bubbles: true }));
        await flush();
        expect(pop('doi')).toBeNull();
        q('[data-testid="filter-reset"]').click();
        await flush();
        expect(shown()).toEqual([1, 2, 3, 4]);

        // --- Year: histogram of the list; a thumb on its bound is no filter -
        const yearPop = await openFilterPill('year');
        expect(yearPop.querySelectorAll('.lb-fbar-hb').length).toBe(10);   // 2015 … 2024
        expect(filterPill('year').querySelector('.lb-fbar-pv').textContent.trim()).toBe('2015–2024');
        const from = yearPop.querySelector('[data-testid="filter-year-from-range"]');
        setRange(from, 2019);
        await flush();
        expect(shown()).toEqual([2, 3, 4]);
        expect(filterPillActive('year')).toBe(true);
        expect(filterPill('year').querySelector('.lb-fbar-pv').textContent.trim()).toBe('2019–2024');
        expect(yearPop.querySelectorAll('.lb-fbar-hb.is-out').length).toBe(4);   // 2015 … 2018 fade
        // The upper thumb cannot pass the lower one.
        const to = yearPop.querySelector('[data-testid="filter-year-to-range"]');
        setRange(to, 2016);
        await flush();
        expect(to.value).toBe('2019');
        expect(shown()).toEqual([2]);
        setRange(to, 2024);
        setRange(from, 2015);
        await flush();
        expect(filterPillActive('year')).toBe(false);
        expect(q('[data-testid="filter-reset"]')).toBeNull();
        expect(meta()).toMatch(/^4 items/);
        q('.lb-page-title').dispatchEvent(new Event('pointerdown', { bubbles: true }));
        await flush();

        // --- Category rail: most frequent first, toggles, row tags join in --
        const chips = () => [...document.querySelectorAll('[data-testid="filter-categories"] .lb-fbar-chip')];
        expect(chips().map((c) => c.firstChild.textContent.trim())).toEqual(['Theory', 'Methods']);
        chips()[1].click();
        await flush();
        expect(shown()).toEqual([1, 2]);
        expect(chips()[1].classList.contains('is-on')).toBe(true);
        [...q('li[data-paper-id="2"]').querySelectorAll('.lb-tag-cat')].find((el) => el.textContent.trim() === 'Theory').click();
        await flush();
        expect(shown()).toEqual([2, 3, 4]);
        expect(chips()[0].classList.contains('is-on')).toBe(true);
        expect(chips()[1].classList.contains('is-on')).toBe(false);
        chips()[0].click();
        await flush();
        expect(shown()).toEqual([1, 2, 3, 4]);

        // --- Sort: every key, a pick closes ---------------------------------
        const sortPop = await openFilterPill('sort');
        expect(sortPop.querySelectorAll('[role="radio"]').length).toBe(5);
        sortPop.querySelector('[role="radio"][data-value="title"]').click();
        await flush();
        expect(pop('sort')).toBeNull();
        expect(filterPill('sort').querySelector('.lb-fbar-pv').textContent.trim()).toBe('Title');

        // --- Semantic search ignores the filters: the pills say so ----------
        (await openFilterPill('pdf')).querySelector('[role="radio"][data-value="no"]').click();
        await flush();
        expect(filterPillActive('pdf')).toBe(true);
        q('[data-testid="semantic-toggle"]').click();
        await flush();
        expect(q('[data-testid="semantic-toggle"]').getAttribute('aria-pressed')).toBe('true');
        expect(filterPill('pdf').disabled).toBe(true);
        expect(filterPillActive('pdf')).toBe(false);
        expect(wrap('pdf').querySelector('.lb-fbar-clr')).toBeNull();
        expect(wrap('pdf').getAttribute('title')).toBe('Not available with semantic search');
        expect(chips().every((c) => c.disabled)).toBe(true);
        q('[data-testid="semantic-toggle"]').click();
        await flush();
        expect(filterPill('pdf').disabled).toBe(false);
        expect(filterPillActive('pdf')).toBe(true);
    });
});
