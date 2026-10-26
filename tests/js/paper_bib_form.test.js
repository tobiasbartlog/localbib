// Bibliographic fields in the Item detail (Ticket 3, bib_fields): the
// metadata block shows the type first, the filled fields of that type and
// what the bibliography still lacks; the edit form puts the type first, then
// exactly that type's fields from the server table, the rest behind "Show all
// fields", and saves only what changed. "Complete via DOI/ISBN" writes the
// empty fields at once and offers differing values unticked. The form reads
// people one per line with the twin of author_names.split_people, pinned by
// tests/fixtures/people_vectors.json (tests/test_author_names.py is the
// server's half). The tests share one booted app and run in order.
import { describe, it, expect, beforeAll, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { installFetchMock, defaultRoutes, flush, typeInto } from './helpers.js';

const { vectors: PEOPLE } = JSON.parse(readFileSync(resolve(__dirname, '../fixtures/people_vectors.json'), 'utf-8'));

const q = (sel) => document.querySelector(sel);
const qa = (sel) => [...document.querySelectorAll(sel)];
const tid = (id) => q(`[data-testid="${id}"]`);

const ARTICLE = {
    id: 101, title: 'Life-cycle cost analysis of highway bridges', authors: 'Chen, L., Müller, H.',
    year: 2011, pub_date: '2011-03', journal: 'Structure and Infrastructure Engineering', issue: '3',
    volume: '', pages: '', issn: '', publisher: '', url: '', abstract: '', editors: '',
    doi: '10.1000/sie.2011.0012', isbn: '', item_type: 'article', effective_type: 'article',
    missing_fields: ['volume', 'pages'], filename: '', notes: '', cite_key: 'Chen.2011', cited_by_count: 48,
    categories: [], custom_fields: [], collections: [],
};

// The server side of the mock: Items by id, PUT merges and answers the Item.
const store = new Map();
let calls;
const puts = () => calls.filter((c) => c.method === 'PUT' && /^\/api\/papers\/\d+$/.test(c.url));

function item(over) {
    const paper = { ...ARTICLE, ...over };
    store.set(paper.id, paper);
    return paper;
}

async function shows(sel) {
    return vi.waitFor(() => {
        const el = q(sel);
        if (!el) throw new Error('not shown: ' + sel);
        return el;
    });
}

async function openItem(id) {
    window.location.hash = `#/paper/${id}`;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await vi.waitFor(() => {
        const h1 = q('.lb-detail-title');
        if (!h1 || h1.textContent.trim() !== store.get(id).title) throw new Error('item not shown');
    });
    await flush();
}

async function openFormFromMenu() {
    q('button[aria-label="More"]').click();
    await flush();
    [...document.querySelectorAll('.lb-modal-actions button')].find((b) => b.textContent.trim() === 'Edit').click();
    return shows('[data-testid="bib-form"]');
}

const fieldIds = (testid) => qa(`[data-testid="${testid}"] [data-field]`).map((el) => el.getAttribute('data-field'));
const label = (field) => q(`[data-testid="bib-fields"] [data-field="${field}"] label`).textContent.trim();

function setSelect(el, value) {
    el.value = value;
    el.dispatchEvent(new Event('change'));
}

beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    item({});
    item({ id: 102, title: 'Undated report', year: null, pub_date: '', missing_fields: ['year'] });
    PEOPLE.forEach((v, i) => item({ id: 300 + i, title: 'People ' + i, authors: v.input, missing_fields: [] }));
    calls = installFetchMock([
        { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
        {
            method: 'POST', url: '/api/papers/101/metadata/lookup',
            reply: {
                paper_id: 101, found: true,
                fills: { pages: '211–226', issn: '1573-0000', publisher: 'Taylor & Francis' },
                conflicts: [
                    { field: 'title', current: ARTICLE.title, found: ARTICLE.title + ' under deterioration', source: 'crossref' },
                    { field: 'authors', current: 'Chen, L., Müller, H.', found: 'Chen, Li; Müller, Hans', source: 'crossref' },
                ],
                sources: { pages: 'crossref', issn: 'crossref', publisher: 'crossref', title: 'crossref', authors: 'crossref' },
                source_status: { crossref: 'found', datacite: 'not_asked', openlibrary: 'no_identifier' },
            },
        },
        {
            method: 'POST', url: '/api/papers/102/metadata/lookup',
            reply: {
                paper_id: 102, found: true, fills: { year: 2019 }, conflicts: [], sources: { year: 'crossref' },
                source_status: { crossref: 'found', datacite: 'not_asked', openlibrary: 'no_identifier' },
            },
        },
        {
            method: 'GET', url: /^\/api\/papers\/\d+$/,
            reply: ({ url }) => store.get(Number(url.split('/').pop())),
        },
        {
            method: 'PUT', url: /^\/api\/papers\/\d+$/,
            reply: ({ url, body }) => {
                const id = Number(url.split('/').pop());
                const next = { ...store.get(id), ...body };
                if (body.volume && body.volume.trim()) next.missing_fields = next.missing_fields.filter((f) => f !== 'volume');
                if (body.pages !== undefined) {
                    next.missing_fields = body.pages ? next.missing_fields.filter((f) => f !== 'pages')
                        : [...new Set([...next.missing_fields, 'pages'])];
                }
                store.set(id, next);
                return next;
            },
        },
        { method: 'GET', url: /^\/api\/papers\/\d+\/references$/, reply: { references: [] } },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    await flush();
});

describe('the metadata block', () => {
    it('shows the type first, the filled fields of the type and what is missing', async () => {
        await openItem(101);
        const block = await shows('[data-testid="block-metadata"]');
        const terms = [...block.querySelectorAll('dt')].map((dt) => dt.textContent.trim());
        expect(terms.slice(0, 5)).toEqual(['Item type', 'Journal', 'Date', 'Issue', 'DOI']);
        expect(tid('meta-type').textContent.trim()).toBe('Journal article');
        expect(block.querySelector('dd[data-field="pub_date"]').textContent.trim()).toBe('2011-03');
        // Empty fields of the type are no rows: the hint names them.
        expect(block.querySelector('dd[data-field="volume"]')).toBeNull();
        const hint = tid('bib-missing-hint');
        expect(hint.textContent).toContain('Missing for the bibliography:');
        expect(hint.textContent).toContain('Volume, Pages');
        expect(tid('bib-hint-lookup').textContent.trim()).toBe('Complete via DOI');
        expect(tid('bib-hint-edit').textContent.trim()).toBe('Enter by hand');
        expect(document.body.textContent).not.toContain('⟦');
    });
});

describe('the edit form', () => {
    it('puts the type first and shows exactly its fields, a missing one dashed', async () => {
        tid('bib-hint-edit').click();
        await shows('[data-testid="bib-form"]');
        expect(tid('bib-type').value).toBe('article');
        expect(fieldIds('bib-fields')).toEqual(['title', 'authors', 'journal', 'pub_date', 'volume', 'issue',
            'pages', 'doi', 'issn', 'url', 'accessed', 'language', 'abstract', 'note_bib']);
        expect(q('#bib-f-authors').value).toBe('Chen, L.\nMüller, H.');
        expect(q('#bib-f-pub_date').value).toBe('2011-03');
        const volume = q('[data-testid="bib-fields"] [data-field="volume"]');
        expect(volume.textContent).toContain('missing');
        expect(q('#bib-f-volume').classList.contains('is-missing')).toBe(true);
        expect(q('#bib-f-issue').classList.contains('is-missing')).toBe(false);
        expect(volume.closest('.lb-bib-form').textContent).toContain('organization in { }');
        expect(tid('bib-export-as').textContent).toBe('@article (biblatex)');
        // The side panel checks the type's requirements while editing.
        const check = tid('bib-checklist');
        const missing = [...check.querySelectorAll('li[data-ok="false"]')].map((li) => li.textContent.trim());
        expect(missing).toEqual(['Volume', 'Pages']);
        expect(tid('block-metadata')).toBeNull();
    });

    it('switches the fields with the type, labels per type from the table', async () => {
        setSelect(tid('bib-type'), 'conference');
        await flush();
        expect(fieldIds('bib-fields')).toEqual(['title', 'authors', 'booktitle', 'event', 'editors', 'place',
            'publisher', 'pub_date', 'pages', 'series', 'volume', 'isbn', 'doi', 'url', 'accessed', 'language',
            'abstract', 'note_bib']);
        expect(label('booktitle')).toBe('Proceedings');
        expect(label('event')).toBe('Conference');
        expect(tid('bib-export-as').textContent).toBe('@inproceedings (biblatex)');
        setSelect(tid('bib-type'), 'thesis');
        await flush();
        expect(label('institution')).toBe('University');
        setSelect(tid('bib-type'), 'article');
        await flush();
        expect(fieldIds('bib-fields')).toContain('journal');
    });

    it('shows every other field behind "Show all fields"', async () => {
        expect(tid('bib-more-fields')).toBeNull();
        const sw = tid('bib-all-fields');
        expect(sw.closest('.lb-bib-all').textContent).toContain('more');
        sw.click();
        await flush();
        expect(sw.getAttribute('aria-checked')).toBe('true');
        const more = fieldIds('bib-more-fields');
        expect(more).toEqual(expect.arrayContaining(['editors', 'booktitle', 'publisher', 'isbn', 'edition', 'eprint']));
        expect(more).not.toContain('journal');
        expect(more).not.toContain('year');
        sw.click();
        await flush();
        expect(tid('bib-more-fields')).toBeNull();
    });

    it('saves only the changed fields, people joined with "; "', async () => {
        typeInto(q('#bib-f-volume'), '7');
        typeInto(q('#bib-f-authors'), 'Chen, Li\nMüller, Hans\n\n{Autobahn Westfalen}\n');
        await flush();
        expect(q('#bib-f-volume').classList.contains('is-missing')).toBe(false);
        const before = puts().length;
        tid('bib-save').click();
        await vi.waitFor(() => {
            if (puts().length === before) throw new Error('not saved');
        });
        expect(puts().at(-1).body).toEqual({ volume: '7', authors: 'Chen, Li; Müller, Hans; {Autobahn Westfalen}' });
        await vi.waitFor(() => {
            if (tid('bib-form')) throw new Error('form still open');
        });
        // Every display drops an organization's braces.
        expect(q('.lb-detail-authors').textContent.trim()).toBe('Chen, Li; Müller, Hans; Autobahn Westfalen');
        expect(tid('bib-missing-hint').textContent).toContain('Pages');
        expect(tid('bib-missing-hint').textContent).not.toContain('Volume');
    });

    it('shows the check on opening, yet lets another tab be picked; Esc cancels the form', async () => {
        await openFormFromMenu();
        const active = () => q('.lb-tab[aria-selected="true"]');
        await shows('[data-testid="bib-check"]');
        const checkTab = active().getAttribute('data-tab');
        const other = qa('.lb-tab').find((b) => b.getAttribute('data-tab') !== checkTab);
        other.click();
        await flush();
        expect(active().getAttribute('data-tab')).toBe(other.getAttribute('data-tab'));
        expect(tid('bib-form')).not.toBeNull();
        const before = puts().length;
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        await flush();
        expect(tid('bib-form')).toBeNull();
        expect(q('.lb-detail-title')).not.toBeNull();   // the Item stays open
        expect(puts().length).toBe(before);
        // Back to the tab with the metadata block for the next steps.
        qa('.lb-tab').find((b) => b.getAttribute('data-tab') === checkTab).click();
        await flush();
    });

    it('closes without a request when nothing changed', async () => {
        await openFormFromMenu();
        const before = puts().length;
        tid('bib-save').click();
        await flush();
        expect(tid('bib-form')).toBeNull();
        expect(puts().length).toBe(before);
    });
});

describe('Complete via DOI/ISBN', () => {
    it('fills the empty fields at once and offers the differing ones unticked', async () => {
        q('button[aria-label="More"]').click();
        await flush();
        const before = puts().length;
        tid('detail-menu-lookup').click();
        const result = await shows('[data-testid="bib-lookup-filled"]');
        const lookup = calls.find((c) => c.url === '/api/papers/101/metadata/lookup');
        expect(lookup.body).toEqual({ doi: ARTICLE.doi, isbn: '' });
        // The fills are saved at once, exactly them.
        expect(puts().length).toBe(before + 1);
        expect(puts().at(-1).body).toEqual({ pages: '211–226', issn: '1573-0000', publisher: 'Taylor & Francis' });
        expect(result.textContent).toContain('3 empty fields filled');
        expect(result.textContent).toContain('CrossRef');
        expect(qa('.lb-bib-chip').map((c) => c.textContent.trim())).toEqual(['Pages 211–226', 'ISSN 1573-0000', 'Publisher']);
        expect(q('#bib-f-pages').value).toBe('211–226');
        expect(q('[data-testid="bib-fields"] [data-field="pages"] [data-testid="bib-provenance"]').textContent).toBe('CrossRef');
        // Sources of this lookup, in the side panel.
        const sources = tid('bib-lookup-sources');
        expect(sources.querySelector('[data-source="crossref"]').textContent).toBe('found via DOI');
        expect(sources.querySelector('[data-source="datacite"]').textContent).toBe('not asked');
        expect(sources.querySelector('[data-source="openlibrary"]').textContent).toBe('no ISBN');

        // Differing values: none ticked, nothing taken.
        const rows = qa('[data-testid="bib-conflicts"] label.lb-bib-cmp');
        expect(rows.map((r) => r.getAttribute('data-field'))).toEqual(['title', 'authors']);
        expect(rows.every((r) => !r.querySelector('input').checked)).toBe(true);
        expect(tid('bib-conflicts').textContent).toContain('2 details differ.');
        expect(tid('bib-conflicts-apply').disabled).toBe(true);
        expect(rows[1].querySelector('.lb-bib-new').textContent).toBe('Chen, Li; Müller, Hans');
    });

    it('takes only the ticked new values', async () => {
        const title = q('[data-testid="bib-conflicts"] label[data-field="title"] input');
        title.click();
        await flush();
        const apply = tid('bib-conflicts-apply');
        expect(apply.disabled).toBe(false);
        expect(apply.textContent.trim()).toBe('Apply 1 new value');
        const before = puts().length;
        apply.click();
        await vi.waitFor(() => {
            if (puts().length === before) throw new Error('not applied');
        });
        expect(puts().at(-1).body).toEqual({ title: ARTICLE.title + ' under deterioration' });
        await flush();
        expect(qa('[data-testid="bib-conflicts"] label.lb-bib-cmp').map((r) => r.getAttribute('data-field'))).toEqual(['authors']);
        expect(q('#bib-f-title').value).toBe(ARTICLE.title + ' under deterioration');
        tid('bib-conflicts-keep').click();
        await flush();
        expect(tid('bib-conflicts')).toBeNull();
    });

    it('undoes the fills', async () => {
        const before = puts().length;
        tid('bib-lookup-undo').click();
        await vi.waitFor(() => {
            if (puts().length === before) throw new Error('not undone');
        });
        expect(puts().at(-1).body).toEqual({ pages: '', issn: '', publisher: '' });
        await shows('[data-testid="bib-lookup-undone"]');
        expect(q('#bib-f-pages').value).toBe('');
        expect(q('[data-testid="bib-fields"] [data-field="pages"] [data-testid="bib-provenance"]')).toBeNull();
        expect(document.body.textContent).not.toContain('⟦');
    });

    // The form shows the year in the date field, and the server clears the
    // year with a cleared date: undoing a filled year clears that field.
    it('undoes a filled year by clearing the date field that shows it', async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        await flush();
        await openItem(102);
        q('button[aria-label="More"]').click();
        await flush();
        const before = puts().length;
        tid('detail-menu-lookup').click();
        await vi.waitFor(() => {
            if (puts().length === before) throw new Error('not filled');
        });
        expect(puts().at(-1).body).toEqual({ year: 2019 });
        await shows('[data-testid="bib-lookup-filled"]');
        tid('bib-lookup-undo').click();
        await vi.waitFor(() => {
            if (puts().length === before + 1) throw new Error('not undone');
        });
        expect(puts().at(-1).body).toEqual({ pub_date: '' });
        await shows('[data-testid="bib-lookup-undone"]');
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        await flush();
    });
});

describe('people in the form', () => {
    it('reads every shared vector into one person per line like the server', async () => {
        const seen = [];
        for (let i = 0; i < PEOPLE.length; i++) {
            await openItem(300 + i);
            await openFormFromMenu();
            seen.push([PEOPLE[i].input, q('#bib-f-authors').value]);
        }
        expect(seen).toEqual(PEOPLE.map((v) => [v.input, v.people.join('\n')]));
    });
});
