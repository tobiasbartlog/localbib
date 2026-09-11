// Notes block in the paper detail view (#160): a paper with notes opens them
// rendered, a paper without notes opens straight into the editor, and the
// block sits between the abstract and the user-defined fields.
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

describe('paper notes block', () => {
    it('renders notes, opens an editor without them, and sits after the abstract', async () => {
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

        // A paper with notes: rendered, Markdown applied, no editor.
        await open(7);
        const rendered = document.querySelector('[data-testid="paper-notes-rendered"]');
        expect(rendered).toBeTruthy();
        expect(rendered.textContent).toContain('Erste Erkenntnis');
        expect(rendered.innerHTML).toContain('<strong>Erkenntnis</strong>');
        expect(document.querySelector('[data-testid="paper-notes-editor"]')).toBeFalsy();

        // A paper without notes: the editor is already open.
        await open(8);
        expect(document.querySelector('[data-testid="paper-notes-editor"]')).toBeTruthy();
        expect(document.querySelector('[data-testid="paper-notes-rendered"]')).toBeFalsy();

        // Position: abstract -> notes -> user-defined fields.
        await open(9);
        const headings = [...document.querySelectorAll('.lb-modal-main .lb-detail-section')]
            .map((s) => ((s.querySelector('.lb-detail-h') || {}).textContent || '').trim());
        const at = (label) => headings.findIndex((h) => h.startsWith(label));
        expect(at('Abstract')).toBeGreaterThanOrEqual(0);
        expect(at('Notizen')).toBe(at('Abstract') + 1);
        expect(at('Benutzerdefinierte Felder')).toBe(at('Notizen') + 1);
    });

    it('switches to editing when the rendered notes are clicked', async () => {
        await open(7);
        document.querySelector('[data-testid="paper-notes-rendered"]').click();
        await flush();

        expect(document.querySelector('[data-testid="paper-notes-editor"]')).toBeTruthy();
        expect(document.querySelector('[data-testid="paper-notes-rendered"]')).toBeFalsy();
    });

    it('renders Markdown structure but never live markup', async () => {
        await open(10);
        const rendered = document.querySelector('[data-testid="paper-notes-rendered"]');

        expect(rendered.querySelector('h1').textContent).toBe('Kapitel');
        expect([...rendered.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['eins', 'zwei']);
        expect(rendered.querySelector('script')).toBeFalsy();
        expect(rendered.innerHTML).not.toContain('onerror');
        expect(rendered.innerHTML).not.toContain('alert(2)');
    });

    it('saves after a pause in typing and says so', async () => {
        await open(8);
        typeInto(document.querySelector('[data-testid="paper-notes-editor"] textarea'), 'Frisch getippt');

        const put = await vi.waitFor(() => {
            const hit = calls.find((c) => c.method === 'PUT' && /\/notes$/.test(c.url));
            if (!hit) throw new Error('Kein Autosave-Request');
            return hit;
        }, { timeout: 3000 });
        expect(put.body).toEqual({ notes: 'Frisch getippt' });

        await vi.waitFor(() => {
            if (!findByText('.lb-notes-status', 'gespeichert')) throw new Error('Keine Bestaetigung');
        });
    });
});
