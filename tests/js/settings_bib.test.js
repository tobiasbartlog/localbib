// Settings for the bibliographic fields (Ticket 3): Maintenance → Refresh has
// the scope "Bibliographic details (DOI / ISBN)" with its own gaps rule, the
// items it skips for want of a DOI or ISBN and the gap fills its run wrote;
// Library has the bibliography format (BIB_DIALECT) with a live example. One
// boot, the tests run in order.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, flush, goTo, holdRequests, liveStream } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const tid = (id) => q(`[data-testid="${id}"]`);
let calls;

async function text(id, wanted) {
    return vi.waitFor(() => {
        const el = tid(id);
        if (!el || !el.textContent.includes(wanted)) throw new Error(`${id}: ${el && el.textContent}`);
        return el;
    });
}

describe('settings: bibliographic details', () => {
    it('offers the bibliographic scope with its help, rule and skipped items', async () => {
        calls = await bootSettings([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true', BIB_DIALECT: 'biblatex' } },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: {} },
            {
                method: 'POST', url: '/api/maintenance/refresh-review/count',
                reply: ({ body }) => (body.scopes.includes('bibliographic')
                    ? (body.only_gaps ? { count: 312, no_identifier: 0 } : { count: 1071, no_identifier: 213 })
                    : { count: 1284 }),
            },
            {
                method: 'POST', url: '/api/maintenance/refresh-review/propose',
                stream: [
                    { type: 'progress', current: 1, total: 1, percent: 100, title: 'X' },
                    { type: 'done', rows: [], stats: { items: 4, errors: 0, no_text: 0, filled: 9, filled_items: 4 }, warnings: [] },
                ],
            },
        ], 'refresh');

        const row = q('[data-scope-row="bibliographic"]');
        expect(row.textContent).toContain('Bibliographic details (DOI / ISBN)');
        expect(tid('refresh-review-help-bibliographic').textContent).toContain('Needs no PDF, only a DOI or an ISBN');
        expect(tid('refresh-review-help-categories').textContent).toContain('You see every proposal first');

        q('input[data-scope="categories"]').click();
        q('input[data-scope="bibliographic"]').click();
        await text('refresh-review-affected', 'Affects 312 items · all with a DOI or ISBN');
        expect(tid('refresh-review-gaps-help-bib').textContent).toContain('only items that lack something for the bibliography');
        // Only this scope ticked: the general gaps rule says nothing about it.
        expect(tid('refresh-review-gaps-help')).toBeNull();

        tid('refresh-review-only-gaps').click();
        await text('refresh-review-affected', 'Affects 1,071 items · 213 without DOI and ISBN skipped');
        expect(tid('refresh-review-gaps-help-bib').textContent).toContain('every item with a DOI or ISBN is looked up');

        tid('refresh-review-start').click();
        await text('refresh-review-filled', '9 empty fields filled right away (in 4 items).');
        const propose = calls.find((c) => c.url === '/api/maintenance/refresh-review/propose');
        expect(propose.body).toEqual({ scopes: ['bibliographic'], only_gaps: false });
        expect(document.body.textContent).not.toContain('No changes found');
        expect(document.body.textContent).not.toContain('⟦');
    });

    // Gap fills are written during the run: a cancelled run still says what
    // it wrote (every progress event carries the count so far).
    it('reports the gap fills a cancelled run already wrote', async () => {
        const runs = holdRequests((method, url) => method === 'POST' && url === '/api/maintenance/refresh-review/propose');
        tid('refresh-review-start').click();
        await vi.waitFor(() => { if (runs.length !== 1) throw new Error('run not started'); });
        expect(tid('refresh-review-filled')).toBeNull();   // a new run starts without the old count
        const stream = liveStream(runs[0].signal);
        runs[0].respond(stream.response);
        stream.push({ type: 'progress', current: 1, total: 3, percent: 33, title: 'A', rows: 0, filled: 5, filled_items: 2 });
        await vi.waitFor(() => { if (!document.body.textContent.includes('33%')) throw new Error('no progress'); });
        [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Cancel').click();
        await text('refresh-review-filled', '5 empty fields filled right away (in 2 items).');
        expect(stream.aborted).toBe(true);
        expect(document.body.textContent).not.toContain('⟦');
    });

    it('sets the bibliography format and shows a web page as it would be written', async () => {
        goTo('#/settings/library');
        const card = await vi.waitFor(() => {
            const el = tid('settings-bib-format');
            if (!el) throw new Error('format card not shown');
            return el;
        });
        expect(card.textContent).toContain('Bibliography format');
        const biblatex = tid('settings-bib-dialect-biblatex');
        expect(biblatex.getAttribute('aria-checked')).toBe('true');
        expect(biblatex.textContent).toContain('recommended');
        expect(tid('settings-bib-format-help').textContent).toContain('Own types for web pages');
        expect(tid('settings-bib-example').textContent).toBe([
            '@online{DEGES.2023,',
            '  author = {{DEGES GmbH}},',
            '  title = {Modernising bridges in service},',
            '  date = {2023-05},',
            '  url = {https://example.org/bruecken},',
            '  urldate = {2026-10-22},',
            '}',
        ].join('\n'));

        tid('settings-bib-dialect-bibtex').click();
        await vi.waitFor(() => {
            if (q('[data-field-status="BIB_DIALECT"]').getAttribute('data-status') !== 'saved') throw new Error('not saved');
        });
        const put = calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings').at(-1);
        expect(put.body).toEqual({ BIB_DIALECT: 'bibtex' });
        expect(tid('settings-bib-dialect-bibtex').getAttribute('aria-checked')).toBe('true');
        expect(tid('settings-bib-format-help').textContent).toContain('\\bibliographystyle');
        const example = tid('settings-bib-example').textContent;
        expect(example).toContain('@misc{DEGES.2023,');
        expect(example).toContain('  month = may,');
        expect(example).toContain('  howpublished = {\\url{https://example.org/bruecken}},');
        expect(example).toContain('  note = {Accessed 2026-10-22},');
        await flush();
        expect(document.body.textContent).not.toContain('⟦');
    });
});
