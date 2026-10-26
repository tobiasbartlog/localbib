// Annotations block and PDF.js preview in the detail view (#229).
//
// The block lists what GET /api/papers/{id}/annotations answers (mocked here,
// modelled on Niu.2026, the spec's acceptance file); the preview is the fake
// viewer adapter from setup.js, whose log shows what the SPA asked of PDF.js.
// One boot per file, so every case is an Item the test navigates to.
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText } from './helpers.js';

const viewerLog = () => window.LB_PDF_VIEWER.log;

function paper(id, over = {}) {
    return {
        id, title: 'Item ' + id, authors: 'Niu, Z.', year: 2026, doi: '', journal: '',
        filename: `item-${id}.pdf`, abstract: 'An abstract.', notes: 'n',
        categories: [], custom_fields: [], ...over,
    };
}

function entry(over) {
    return {
        kind: 'highlight', subtype: 'Highlight', page: 8, rect: { x: 0.1, y: 0.1, w: 0.4, h: 0.02 },
        color: '#FFEE58', quote: null, comment: null, replies: [], context: null, caption: null,
        author: 'tbartlog', created: '2026-10-09T20:39:18+02:00', modified: null, merged: 1,
        has_image: false, section: null, ...over,
    };
}

const NIU = [
    entry({ id: 'a1', quote: '3.3 Analysis of High-Risk Functions', comment: 'kommentar zu 3.3', section: '3.3 Analysis of High-Risk Functions' }),
    entry({ id: 'a2', quote: '…High-Risk Functions to LLM for Vulnerability…', merged: 2, section: '3.3 Analysis of High-Risk Functions' }),
    entry({ id: 'a3', kind: 'comment', subtype: 'Text', comment: 'Einfach so ein kommentar auf s8',
            context: '…statistical mechanism allows researchers to clearly understand the model’s token usage…',
            section: '3.4 Token Efficiency and Logging Mechanism' }),
    entry({ id: 'a4', subtype: 'Underline', color: '#00FF00', quote: '3.5 Results Storage', section: '3.5 Results Storage' }),
    entry({ id: 'a5', kind: 'shape', subtype: 'Square', color: '#FF0000', page: 9, has_image: true,
            rect: { x: 0.102, y: 0.3088, w: 0.8599, h: 0.2196 },
            caption: '4 Experiments — All experiments were conducted on a workstation.', section: '4 Experiments' }),
    entry({ id: 'a6', color: '#5C6BC0', page: 10, rect: { x: 0.0897, y: 0.1209, w: 0.7933, h: 0.0675 },
            quote: '…the TDE of our proposed method is 0.107, and overall, there…', section: '4 Experiments' }),
    entry({ id: 'a7', page: 10, quote: '…required more tokens for thorough analysis, reflecting the…', section: '4 Experiments' }),
];

let stand = '2026-10-09T18:40:00+00:00';
let release = null;   // resolves the pending read of Item 86
let releaseItem = null;   // resolves the pending GET of Item 89
const PAPERS = {
    81: paper(81), 82: paper(82), 83: paper(83), 84: paper(84, { filename: '' }),
    85: paper(85), 86: paper(86), 87: paper(87), 90: paper(90), 91: paper(91),
};
// What GET /api/papers/{id}/locate answers for a found passage (contract 2.6).
const SPOT = { x: 0.119, y: 0.3117, w: 0.6464, h: 0.0717 };
const locateCalls = (id) => calls.filter((c) => c.url.startsWith(`/api/papers/${id}/locate?`));
const LISTS = {
    82: [],
    85: [entry({ id: 'p3', page: 3, quote: 'A line on page three.' }), entry({ id: 'p5', page: 5, quote: 'A line on page five.' })],
    87: [],
};

let calls = [];
const annotationCalls = (id) => calls.filter((c) => c.method === 'GET' && c.url === `/api/papers/${id}/annotations`);
const rows = () => [...document.querySelectorAll('[data-testid="annotations"] .lb-ann-row')];
const rowOf = (id) => document.querySelector(`.lb-ann-row[data-annotation-id="${id}"]`);
const chip = (key) => document.querySelector(`.lb-ann-chip[data-filter="${key}"]`);
const dot = (hex) => document.querySelector(`.lb-ann-dot[data-color="${hex}"]`);

async function open(hash) {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await flush(); await flush(); await flush();
}

async function openNiu() {
    await open('#/paper/81');
    await vi.waitFor(() => {
        if (rows().length !== NIU.length) throw new Error('list not rendered');
    });
}

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    calls = installFetchMock([
        { method: 'GET', url: '/api/papers/89', reply: () => new Promise((resolve) => { releaseItem = resolve; }) },
        { method: 'GET', url: /^\/api\/papers\/\d+$/, reply: ({ url }) => ({ ...PAPERS[url.split('/').pop()] }) },
        { method: 'GET', url: /^\/api\/papers\/90\/locate\?/, reply: { page: 4, rect: SPOT } },
        { method: 'GET', url: /^\/api\/papers\/91\/locate\?/, reply: { page: 4, rect: null } },
        { method: 'GET', url: '/api/papers/83/annotations', status: 409, reply: { detail: 'error.annotations.unreadable' } },
        { method: 'GET', url: '/api/papers/86/annotations', reply: () => new Promise((resolve) => { release = resolve; }) },
        {
            method: 'GET',
            url: /^\/api\/papers\/\d+\/annotations$/,
            reply: ({ url }) => {
                const id = url.split('/')[3];
                return {
                    file_modified: stand, pages: 13, multiple_authors: false,
                    has_sections: id === '81', annotations: id === '81' ? NIU : (LISTS[id] || []),
                };
            },
        },
        { method: 'POST', url: /^\/api\/papers\/\d+\/ocr\?pages=10$/, reply: () => { stand = '2026-10-10T08:00:00+00:00'; return { status: 'ok' }; } },
        { method: 'GET', url: /^\/api\/papers\/\d+\/references$/, reply: { references: [] } },
        { method: 'GET', url: /^\/api\/papers\/\d+\/bibtex$/, reply: { key: 'Niu.2026' } },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    await flush();
});

describe('annotations from the PDF', () => {
    it('lists them under the abstract, grouped by section, styled per type', async () => {
        await openNiu();
        const block = document.querySelector('[data-testid="annotations"]');
        expect(block.querySelector('.lb-detail-h').textContent.trim()).toBe('Annotations');
        expect(block.querySelector('[data-testid="annotations-count"]').textContent.trim()).toBe('7');
        expect(block.querySelector('[data-testid="annotations-stand"]').textContent).toMatch(/^read from the PDF · /);

        const groups = [...block.querySelectorAll('[data-testid="annotations-group"]')];
        expect(groups.map((g) => g.querySelector('.lb-ann-group-title').textContent.trim())).toEqual([
            '3.3 Analysis of High-Risk Functions', '3.4 Token Efficiency and Logging Mechanism',
            '3.5 Results Storage', '4 Experiments',
        ]);
        expect(groups.map((g) => g.querySelector('.lb-ann-group-pages').textContent.trim()))
            .toEqual(['p. 8', 'p. 8', 'p. 8', 'pp. 9–10']);

        // Row anatomy: quote in the mark's colour, the comment in its box,
        // a sticky note with its context line, a shape as a picture + caption.
        expect(rowOf('a1').querySelector('.lb-ann-mark').style.background).toContain('rgba(255, 238, 88');
        expect(rowOf('a1').querySelector('[data-testid="annotation-comment"]').textContent).toContain('kommentar zu 3.3');
        expect(rowOf('a2').textContent).toContain('marked twice, merged');
        expect(rowOf('a3').querySelector('.lb-ann-note').textContent).toBe('Einfach so ein kommentar auf s8');
        expect(rowOf('a3').querySelector('.lb-ann-context').textContent).toContain('next to “…statistical mechanism');
        const img = rowOf('a5').querySelector('img.lb-ann-img');
        expect(img.getAttribute('src')).toBe(`/api/papers/81/annotations/a5/image?v=${encodeURIComponent(stand)}`);
        expect(rowOf('a5').querySelector('.lb-ann-caption').textContent).toContain('4 Experiments —');
        // One author only: the meta line does not name them.
        expect(rowOf('a1').querySelector('.lb-ann-meta').textContent).not.toContain('tbartlog');
        expect(rowOf('a1').querySelector('.lb-ann-meta').textContent).toContain('Highlight');
        expect(rowOf('a4').querySelector('.lb-ann-meta').textContent).toContain('Underline');
        // Reachable by keyboard: every row is a button.
        expect(rows().every((r) => r.tagName === 'BUTTON' && r.type === 'button')).toBe(true);
    });

    it('filters by kind with counts and by colour, the second click releasing it', async () => {
        await openNiu();
        const counts = ['all', 'highlights', 'comments', 'shapes']
            .map((k) => chip(k).querySelector('.lb-ann-chip-n').textContent.trim());
        expect(counts).toEqual(['7', '5', '2', '1']);
        expect([...document.querySelectorAll('.lb-ann-dot')].map((d) => d.getAttribute('data-color')))
            .toEqual(['#FFEE58', '#00FF00', '#FF0000', '#5C6BC0']);
        expect(dot('#FFEE58').textContent.trim()).toBe('4');

        chip('highlights').click(); await flush();
        expect(rows().length).toBe(5);
        chip('comments').click(); await flush();
        expect(rows().map((r) => r.getAttribute('data-annotation-id'))).toEqual(['a1', 'a3']);
        chip('shapes').click(); await flush();
        expect(rows().map((r) => r.getAttribute('data-annotation-id'))).toEqual(['a5']);

        chip('all').click(); await flush();
        dot('#FFEE58').click(); await flush();
        expect(rows().length).toBe(4);
        expect(dot('#FFEE58').getAttribute('aria-pressed')).toBe('true');
        dot('#FFEE58').click(); await flush();
        expect(rows().length).toBe(7);

        // Nothing left: say so and offer the way back.
        chip('shapes').click(); await flush();
        dot('#5C6BC0').click(); await flush();
        const none = document.querySelector('[data-testid="annotations-no-match"]');
        expect(none.textContent).toContain('No annotation matches this filter.');
        findByText('[data-testid="annotations-no-match"] button', 'Show all').click();
        await flush();
        expect(rows().length).toBe(7);
        expect(chip('all').getAttribute('aria-pressed')).toBe('true');
    });

    it('jumps the preview to the page and area of a clicked row', async () => {
        await openNiu();
        rowOf('a6').click();
        await flush();
        const jump = viewerLog().jumps.at(-1);
        expect(jump).toEqual({ page: 10, rect: NIU[5].rect });
        expect(rowOf('a6').classList.contains('is-active')).toBe(true);
        expect(rowOf('a6').textContent).toContain('in preview');
        expect(rowOf('a1').textContent).not.toContain('in preview');
        expect(document.querySelector('[data-testid="pdf-page"]').textContent.trim()).toBe('p. 10 / 12');
    });

    it('reads the list again on return to the window and reloads a changed file', async () => {
        await openNiu();
        const before = annotationCalls(81).length;
        const opened = viewerLog().opened.length;

        window.dispatchEvent(new Event('focus'));
        await vi.waitFor(() => {
            if (annotationCalls(81).length !== before + 1) throw new Error('no reload on focus');
        });
        await flush();
        expect(viewerLog().opened.length).toBe(opened);   // same Stand: the preview stays

        stand = '2026-10-09T19:15:00+00:00';               // the reader saved
        document.dispatchEvent(new Event('visibilitychange'));
        await vi.waitFor(() => {
            const last = viewerLog().opened.at(-1);
            if (!last || !last.url.includes('?v=')) throw new Error('preview not reloaded');
        });
        expect(viewerLog().opened.at(-1).url).toBe(`/api/papers/81/pdf?v=${encodeURIComponent(stand)}`);
        expect(rows().length).toBe(7);
    });

    it('reads the annotations again and reloads the preview after OCR rewrote the PDF', async () => {
        await openNiu();
        const before = annotationCalls(81).length;
        const opened = viewerLog().opened.length;
        document.querySelector('button[aria-label="More"]').click();
        await flush();
        findByText('button', 'OCR starten').click();
        await vi.waitFor(() => {
            if (annotationCalls(81).length !== before + 1) throw new Error('annotations not read again');
        });
        await vi.waitFor(() => {
            if (viewerLog().opened.length <= opened) throw new Error('preview not reloaded');
        });
        expect(viewerLog().opened.at(-1).url).toBe(`/api/papers/81/pdf?v=${encodeURIComponent(stand)}`);
    });

    it('shows the empty, unreadable, loading and no-PDF states', async () => {
        await open('#/paper/82');
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="annotations-empty"]')) throw new Error('no empty state');
        });
        const empty = document.querySelector('[data-testid="annotations-empty"]').textContent;
        expect(empty).toContain('Nothing marked in this PDF yet.');
        expect(empty).toContain('Zotero, Mendeley and Citavi');

        await open('#/paper/83');
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="annotations-unreadable"]')) throw new Error('no unreadable state');
        });
        expect(document.querySelector('[data-testid="annotations-unreadable"]').textContent)
            .toContain('password-protected');
        expect(viewerLog().opened.some((o) => o.url === '/api/papers/83/pdf')).toBe(true);   // preview still works

        await open('#/paper/86');
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="annotations-loading"]')) throw new Error('no placeholders');
        });
        expect(findByText('h1', 'Item 86')).toBeTruthy();   // the view did not wait
        expect(document.querySelector('[data-testid="annotations"]').textContent).toContain('reading the PDF…');
        release({ file_modified: stand, pages: 2, multiple_authors: false, has_sections: false,
                  annotations: [entry({ id: 'late', page: 1, quote: 'Read at last.' })] });
        await vi.waitFor(() => {
            if (!rowOf('late')) throw new Error('list not rendered after the read');
        });

        await open('#/paper/84');
        expect(findByText('h1', 'Item 84')).toBeTruthy();
        expect(document.querySelector('[data-testid="annotations"]')).toBeNull();
        expect(annotationCalls(84)).toEqual([]);
    });

    it('groups by page when the PDF has no sections', async () => {
        await open('#/paper/85');
        await vi.waitFor(() => {
            if (rows().length !== 2) throw new Error('list not rendered');
        });
        const titles = [...document.querySelectorAll('.lb-ann-group-title')].map((t) => t.textContent.trim());
        expect(titles).toEqual(['Page 3', 'Page 5']);
    });

    it('opens the preview on the file\'s Stand, never on the bare URL an older file may be cached under', async () => {
        PAPERS[88] = paper(88, { pdf_modified: stand });
        await open('#/paper/88');
        await vi.waitFor(() => {
            if (annotationCalls(88).length !== 1) throw new Error('list not read');
            if (!viewerLog().opened.some((o) => o.url.startsWith('/api/papers/88/pdf'))) throw new Error('preview not opened');
        });
        await flush();
        // The reading reports the same Stand: nothing is opened a second time.
        expect(viewerLog().opened.filter((o) => o.url.startsWith('/api/papers/88/pdf')).map((o) => o.url))
            .toEqual([`/api/papers/88/pdf?v=${encodeURIComponent(stand)}`]);
    });

    it('keeps the outgoing preview on its Stand while the next Item is still loading', async () => {
        PAPERS[88] = paper(88, { pdf_modified: stand });
        await open('#/paper/88');
        await vi.waitFor(() => {
            if (!viewerLog().opened.some((o) => o.url.startsWith('/api/papers/88/pdf'))) throw new Error('preview not opened');
        });
        const before = viewerLog().opened.length;

        await open('#/paper/89');   // its GET stays pending
        await vi.waitFor(() => {
            if (!releaseItem) throw new Error('Item 89 not requested');
        });
        await flush();
        expect(viewerLog().opened.slice(before).map((o) => o.url)).toEqual([]);

        releaseItem(paper(89, { pdf_modified: stand }));
        await vi.waitFor(() => {
            if (viewerLog().opened.length === before) throw new Error('preview not opened');
        });
        await flush();
        expect(viewerLog().opened.slice(before).map((o) => o.url))
            .toEqual([`/api/papers/89/pdf?v=${encodeURIComponent(stand)}`]);
    });

    it('opens the preview on the page a link names', async () => {
        await open('#/paper/87?page=5');
        await vi.waitFor(() => {
            if (!viewerLog().opened.some((o) => o.url === '/api/papers/87/pdf')) throw new Error('preview not opened');
        });
        expect(viewerLog().opened.filter((o) => o.url === '/api/papers/87/pdf').at(-1).page).toBe(5);
        expect(locateCalls(87)).toEqual([]);   // no ?find=: nothing to locate
    });

    it('frames the passage a ?find= link names on its page', async () => {
        const jumps = viewerLog().jumps.length;
        const find = 'Lifecycle costs of bridges are dominated by maintenance';
        await open('#/paper/90?page=4&find=' + encodeURIComponent(find));
        await vi.waitFor(() => {
            if (viewerLog().jumps.length === jumps) throw new Error('no jump to the passage');
        });
        expect(viewerLog().jumps.at(-1)).toEqual({ page: 4, rect: SPOT });
        expect(locateCalls(90).map((c) => c.url))
            .toEqual([`/api/papers/90/locate?page=4&text=${encodeURIComponent(find)}`]);
        expect(viewerLog().opened.filter((o) => o.url === '/api/papers/90/pdf').at(-1).page).toBe(4);
    });

    it('only goes to the page when the passage is not found there', async () => {
        const jumps = viewerLog().jumps.length;
        await open('#/paper/91?page=4&find=' + encodeURIComponent('Not on this page at all'));
        await vi.waitFor(() => {
            if (!locateCalls(91).length) throw new Error('not located');
        });
        await flush(); await flush();
        expect(viewerLog().jumps.length).toBe(jumps);   // no frame, never a guess
        expect(viewerLog().opened.filter((o) => o.url === '/api/papers/91/pdf').at(-1).page).toBe(4);
    });
});
