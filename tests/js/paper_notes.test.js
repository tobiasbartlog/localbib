// Notes block in the paper detail view (#160): every note opens in the
// Markdown editor (ADR-0026, the seam's textarea fake here), which saves only
// what was typed, and the block sits in the side panel's Notes tab (#230).
//
// One boot per file (vitest isolates module graphs per file, and app.js mounts
// itself on import), so the three cases are three papers the test navigates
// between -- which is also how a reader meets them.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, findByText, typeInto } from './helpers.js';

function paper(id, over = {}) {
    return {
        id, title: 'Paper ' + id, authors: 'Doe, J.', year: 2024, doi: '',
        journal: '', filename: 'x.pdf', abstract: '', notes: '',
        categories: [], custom_fields: [], ...over,
    };
}

const PAPERS = {
    7: paper(7, { notes: 'Erste **Erkenntnis**' }),
    8: paper(8, { notes: '' }),
    // Markdown structure next to markup a note must never be allowed to run.
    10: paper(10, {
        notes: '# Kapitel\n\n- eins\n- zwei\n\n<img src=x onerror="alert(1)">\n\n<script>alert(2)</script>',
    }),
    9: paper(9, {
        abstract: 'Ein Abstract.',
        notes: 'Notiz',
        custom_fields: [
            { field_id: 3, name: 'Lesefortschritt', field_type: 'text', options: '', value: '' },
        ],
    }),
};

let calls = [];

async function open(id) {
    window.location.hash = '#/paper/' + id;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await flush(); await flush(); await flush();
}

const editorOf = () => document.querySelector('[data-testid="paper-notes-editor"] textarea.lb-md-fake');
const notePuts = () => calls.filter((c) => c.method === 'PUT' && /\/notes$/.test(c.url));

describe('paper notes block', () => {
    it('opens every note in the Markdown editor, which sits in the Notes tab', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        calls = installFetchMock([
            { method: 'PUT', url: /^\/api\/papers\/\d+\/notes$/, reply: ({ body }) => ({ notes: body.notes }) },
            // Eine Kopie je Abruf: die Detailansicht schreibt in ihr Paper-Objekt
            // zurueck, und das darf die Vorlage der naechsten Navigation nicht aendern.
            { method: 'GET', url: /^\/api\/papers\/\d+$/, reply: ({ url }) => ({ ...PAPERS[url.split('/').pop()] }) },
            { method: 'GET', url: /^\/api\/papers\/\d+\/references$/, reply: { count: 0, references: [] } },
            { method: 'GET', url: /^\/api\/papers\/\d+\/bibtex$/, reply: { key: 'Doe2024' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();

        // A paper with notes: the editor holds them, no read/write switch.
        await open(7);
        expect(editorOf().value).toBe('Erste **Erkenntnis**');
        expect(document.querySelector('[data-testid="paper-notes-rendered"]')).toBeFalsy();

        // A paper without notes: the same editor, empty.
        await open(8);
        expect(editorOf().value).toBe('');

        // Position (#230), default layout: abstract -> annotations (#229) in
        // the main column; Notes in its tab, shown first; the user-defined
        // fields in the Metadata tab.
        await open(9);
        const headings = (zone) => [...document.querySelectorAll(`[data-zone="${zone}"] .lb-detail-section`)]
            .map((s) => ((s.querySelector('.lb-detail-h') || {}).textContent || '').trim());
        const main = headings('main');
        expect(main.findIndex((h) => h.startsWith('Abstract'))).toBeGreaterThanOrEqual(0);
        expect(main.findIndex((h) => h.startsWith('Annotations'))).toBe(
            main.findIndex((h) => h.startsWith('Abstract')) + 1);
        expect(main.some((h) => h.startsWith('Notes'))).toBe(false);
        const notesPanel = document.querySelector('[data-zone="tab2"]');
        expect(notesPanel.querySelector('[data-testid="paper-notes"]')).toBeTruthy();
        expect(notesPanel.style.display).not.toBe('none');
        expect(headings('tab0').some((h) => h.startsWith('Custom fields'))).toBe(true);
    });

    it('never saves a note that was only opened, not even on leaving', async () => {
        await open(10);
        expect(editorOf().value).toBe(PAPERS[10].notes);
        await open(7);
        await new Promise((r) => setTimeout(r, 900));
        expect(notePuts()).toEqual([]);
    });

    it('saves after a pause in typing and says so', async () => {
        await open(8);
        typeInto(editorOf(), 'Frisch getippt');

        const put = await vi.waitFor(() => {
            const hit = notePuts()[0];
            if (!hit) throw new Error('Kein Autosave-Request');
            return hit;
        }, { timeout: 3000 });
        expect(put.url).toBe('/api/papers/8/notes');
        expect(put.body).toEqual({ notes: 'Frisch getippt' });

        await vi.waitFor(() => {
            if (!findByText('.lb-notes-status', 'saved')) throw new Error('no save confirmation');
        });
    });

    it('saves what was typed before switching to another item, for that item', async () => {
        await open(9);
        const before = notePuts().length;
        typeInto(editorOf(), 'Notiz ergänzt');
        await open(7);
        await vi.waitFor(() => {
            if (notePuts().length === before) throw new Error('nothing saved');
        });
        const put = notePuts()[before];
        expect(put.url).toBe('/api/papers/9/notes');
        expect(put.body).toEqual({ notes: 'Notiz ergänzt' });
        // The next item's note arrives as a new value, not as an edit.
        expect(editorOf().value).toBe('Erste **Erkenntnis**');
        await new Promise((r) => setTimeout(r, 900));
        expect(notePuts().length).toBe(before + 1);
    });
});
