// Side panel with tabs, default layout (#230): abstract and annotations in the
// main column, tabs Metadata / References / Notes, a number on References and a
// dot on Notes. Tab clicks fold and unfold the panel between 1100 and 1440 px,
// and the tab picked last is remembered. jsdom has no layout, so the widths are
// answered by a stubbed matchMedia. One boot per file (helpers.js).
import { describe, it, expect, beforeAll } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const PAPER = {
    id: 91, title: 'Side Panel', authors: 'Kern, D.', year: 2026, doi: '10.1/sp', journal: '',
    filename: 'sp.pdf', abstract: 'An abstract.', notes: 'hallo', categories: [], custom_fields: [],
};
const NO_NOTES = { ...PAPER, id: 92, title: 'Without Notes', notes: '' };
const REFS = [1, 2, 3].map((n) => ({ id: n, ref_index: n, title: `Ref ${n}`, authors: '', year: 2020 }));

let wide = false;   // what the "(min-width: 1440px)" query answers
const tabs = () => [...document.querySelectorAll('[role="tab"]')];
const tab = (label) => tabs().find((el) => el.querySelector('.lb-tab-label').textContent === label);
const panel = (key) => document.querySelector(`[data-zone="${key}"]`);
const modal = () => document.querySelector('.lb-modal');

async function open(id) {
    window.location.hash = '#/paper/' + id;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await flush(); await flush(); await flush();
}

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    window.matchMedia = (query) => ({
        matches: query.includes('min-width: 1440px') ? wide : false,
        media: query, addEventListener() {}, removeEventListener() {},
    });
    installFetchMock([
        { method: 'GET', url: /^\/api\/papers\/\d+$/, reply: ({ url }) => ({ ...(url.endsWith('/92') ? NO_NOTES : PAPER) }) },
        { method: 'GET', url: /^\/api\/papers\/\d+\/references$/, reply: { count: 3, references: REFS } },
        { method: 'GET', url: /^\/api\/papers\/\d+\/bibtex$/, reply: { key: 'k2026' } },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    await flush();
});

describe('detail side panel, default layout', () => {
    it('puts abstract and annotations in the main column and the rest into three tabs', async () => {
        await open(91);

        expect(tabs().map((el) => el.querySelector('.lb-tab-label').textContent))
            .toEqual(['Metadata', 'References', 'Notes']);
        const main = panel('main');
        expect(main.textContent).toContain('An abstract.');
        expect(main.querySelector('[data-testid="annotations"]')).toBeTruthy();
        expect(main.querySelector('[data-testid="paper-notes"]')).toBeNull();
        expect(panel('tab0').querySelector('[data-testid="block-metadata"]')).toBeTruthy();
        expect(panel('tab0').querySelector('[data-testid="block-categories"]')).toBeTruthy();
        expect(panel('tab0').querySelector('[data-testid="detail-collections"]')).toBeTruthy();
        expect(panel('tab2').querySelector('[data-testid="paper-notes"]')).toBeTruthy();

        // A number on References (three extracted), a dot on Notes (not empty);
        // Metadata carries neither.
        expect(tab('References').querySelector('.lb-tab-count').textContent).toBe('3');
        expect(tab('Notes').querySelector('.lb-tab-dot')).toBeTruthy();
        expect(tab('Notes').querySelector('.lb-tab-count')).toBeNull();
        expect(tab('Metadata').querySelector('.lb-tab-count')).toBeNull();
        expect(tab('Metadata').querySelector('.lb-tab-dot')).toBeNull();

        // First time: the tab with Notes is shown, the others stay out of sight.
        expect(tab('Notes').getAttribute('aria-selected')).toBe('true');
        expect(panel('tab2').style.display).not.toBe('none');
        expect(panel('tab0').style.display).toBe('none');
        expect(panel('tab1').style.display).toBe('none');
        // The old Metadata button in the head is gone.
        expect([...document.querySelectorAll('.lb-modal-head button')]
            .some((b) => b.dataset.testid === 'meta-toggle')).toBe(false);
    });

    it('shows no dot for empty notes', async () => {
        await open(92);
        expect(tab('Notes').querySelector('.lb-tab-dot')).toBeNull();
    });

    it('folds and unfolds on tab clicks between 1100 and 1440 px, and remembers the pick', async () => {
        await open(91);
        expect(modal().classList.contains('is-side-open')).toBe(false);

        tab('References').click();
        await flush();
        expect(modal().classList.contains('is-side-open')).toBe(true);
        expect(tab('References').getAttribute('aria-selected')).toBe('true');
        expect(panel('tab1').style.display).not.toBe('none');
        expect(panel('tab2').style.display).toBe('none');
        expect(localStorage.getItem('lbDetailTab')).toBe('references');

        // The active tab again: folded.
        tab('References').click();
        await flush();
        expect(modal().classList.contains('is-side-open')).toBe(false);

        // Another tab opens it again.
        tab('Metadata').click();
        await flush();
        expect(modal().classList.contains('is-side-open')).toBe(true);
        expect(panel('tab0').style.display).not.toBe('none');

        // Back to the list and open an Item again: the last pick is shown.
        await open(91);
        expect(tab('Metadata').getAttribute('aria-selected')).toBe('true');
    });

    it('does not fold from 1440 px up', async () => {
        wide = true;
        await open(91);
        tab('References').click();
        await flush();
        const openNow = modal().classList.contains('is-side-open');
        tab('References').click();
        await flush();
        expect(modal().classList.contains('is-side-open')).toBe(openNow);
        expect(tab('References').getAttribute('aria-selected')).toBe('true');
        wide = false;
    });
});
