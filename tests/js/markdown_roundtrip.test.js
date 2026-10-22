// Round-trip fidelity of the Markdown editor (ADR-0026): the vendored Milkdown
// bundle and static/markdown/ load a note and write it back untouched. The
// corpus is synthetic (the real notes are the user's, never in the repo) and
// covers every case the prototype met in a real archive of notes: both table styles,
// bare URLs, "tags: [a, b]" in body text, a heading directly before a task
// list, a blank line after the frontmatter, a lone "~", citations with dotted
// keys and multi-cites, aliased wikilinks, "[[x]]"/"[@x]" inside code.
import { describe, it, expect, afterEach, vi } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { createMarkdownEditor } from '../../static/markdown/editor.mjs';
import { NodeSelection } from '../../static/vendor/milkdown-7.22.3/milkdown.mjs';

// jsdom has no layout: ProseMirror asks for rects when it scrolls a
// selection into view.
const NO_RECT = { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
for (const proto of [Range.prototype, Element.prototype]) {
    if (!proto.getClientRects) proto.getClientRects = () => [];
    if (!proto.getBoundingClientRect) proto.getBoundingClientRect = () => NO_RECT;
}
if (!document.elementFromPoint) document.elementFromPoint = () => null;

const DIR = resolve(__dirname, 'fixtures/notes');
const CORPUS = readdirSync(DIR).filter((f) => f.endsWith('.md')).sort()
    .map((file) => [file, readFileSync(resolve(DIR, file), 'utf8')]);

const LIBRARY = [
    { id: 1, cite_key: 'Peck2005', title: 'Lehm im Bestand', authors: 'Peck, Anna', year: 2005, has_pdf: true },
    { id: 2, cite_key: 'Niu.2026', title: 'Dotted keys', authors: 'Niu, Li; Chen, Bo', year: 2026, has_pdf: false },
];
const resolvers = {
    citation: {
        resolve: async (keys) => LIBRARY.filter((p) => keys.includes(p.cite_key)),
        search: async (q) => LIBRARY.filter((p) => p.cite_key.toLowerCase().startsWith(q.toLowerCase())),
    },
};

let editors = [];
async function open(value, extra = {}) {
    const host = document.createElement('div');
    document.body.append(host);
    const ed = await createMarkdownEditor(host, { value, resolvers, ...extra });
    editors.push(ed);
    return ed;
}
afterEach(async () => {
    for (const ed of editors) await ed.destroy();
    editors = [];
    document.body.innerHTML = '';
});
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

describe('markdown round trip', () => {
    it.each(CORPUS)('%s comes back byte-identical', async (_file, text) => {
        const ed = await open(text);
        expect(ed.serialize()).toBe(text);
        expect(ed.getValue()).toBe(text);
    });

    it('renders citations and wikilinks as chips, code stays text', async () => {
        const ed = await open(CORPUS.find(([f]) => f.startsWith('08'))[1]);
        const dom = ed.view.dom;
        expect(dom.querySelector('pre, code').textContent).toContain('[[kein-verweis]]');
        expect([...dom.querySelectorAll('.lb-md-wikilink')].map((n) => n.dataset.raw)).toEqual(['gibt-es-nicht']);
        expect([...dom.querySelectorAll('.lb-md-citation')].map((n) => n.dataset.raw)).toEqual(['@Mustermann1999']);
        expect(dom.textContent).toContain('[@y]');
    });

    it('keeps a dotted key whole, splits multi-cites, labels from the library', async () => {
        const ed = await open(CORPUS.find(([f]) => f.startsWith('01'))[1]);
        await tick();
        await tick();
        const raws = [...ed.view.dom.querySelectorAll('.lb-md-citation')].map((n) => n.dataset.raw);
        expect(raws).toEqual(['@Peck2005, S. 12', '@Schneider2026', '@Grieshaber2026; @Schneider2026, S. 4', '@Niu.2026']);
        const chips = [...ed.view.dom.querySelectorAll('.lb-md-cite-chip')];
        expect(chips.map((c) => c.querySelector('.lb-md-cite-label').textContent))
            .toEqual(['Peck 2005', '@Schneider2026', '@Grieshaber2026', '@Schneider2026', 'Niu & Chen 2026']);
        expect(chips[0].querySelector('.lb-md-cite-loc').textContent).toBe('S. 12');
        expect(chips[1].classList.contains('is-missing')).toBe(true);
        const alias = [...ed.view.dom.querySelectorAll('.lb-md-wikilink')].map((n) => n.textContent);
        expect(alias).toEqual(['↗zim-lehm', '↗Dissertationskonzept']);
    });

    it('fires onChange only after an edit, and writes our nodes verbatim', async () => {
        const changes = [];
        const text = 'Vorher [@Niu.2026] und [[a|b]].\n';
        const ed = await open(text, { onChange: (v) => changes.push(v) });
        await tick(300);
        expect(changes).toEqual([]);
        const view = ed.view;
        view.dispatch(view.state.tr.insertText(' Neu.', view.state.doc.content.size - 1));
        await tick(300);
        expect(changes).toEqual(['Vorher [@Niu.2026] und [[a|b]]. Neu.\n']);
        // Undone back to the start: the original bytes, not a re-serialisation.
        view.dispatch(view.state.tr.delete(view.state.doc.content.size - 6, view.state.doc.content.size - 1));
        await tick(300);
        expect(changes[changes.length - 1]).toBe(text);
    });

    it('a file in a foreign style is normalised by the serialiser, never on load', async () => {
        const text = '* eins\n* zwei\n\n__fett__\n';
        const ed = await open(text);
        expect(ed.serialize()).toBe('- eins\n- zwei\n\n__fett__\n');
        expect(ed.getValue()).toBe(text);
    });

    it('setValue is not an edit', async () => {
        const changes = [];
        const ed = await open('Eins\n', { onChange: (v) => changes.push(v) });
        ed.setValue('Zwei [@Peck2005]\n');
        await tick(300);
        expect(changes).toEqual([]);
        expect(ed.getValue()).toBe('Zwei [@Peck2005]\n');
    });

    it('turns a hand-typed citation into a chip on the closing bracket', async () => {
        const ed = await open('');
        const view = ed.view;
        const at = 1;
        view.dispatch(view.state.tr.insertText('Siehe [@Peck2005, S. 3', at));
        const end = view.state.selection.from;
        const handled = view.someProp('handleTextInput', (f) => f(view, end, end, ']'));
        expect(handled).toBe(true);
        expect(view.dom.querySelector('.lb-md-citation').dataset.raw).toBe('@Peck2005, S. 3');
        expect(ed.getValue()).toBe('Siehe [@Peck2005, S. 3]');
    });

    it('keyboard: "@" searches the library, arrows move, Enter inserts the citation', async () => {
        const ed = await open('');
        const view = ed.view;
        view.focus();
        view.dispatch(view.state.tr.insertText('Laut @', 1));
        await vi.waitFor(() => {
            if (document.querySelectorAll('.lb-md-suggest-row').length !== 2) throw new Error('no list');
        });
        const key = (k) => view.dom.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
        key('ArrowDown');
        expect(document.querySelector('.lb-md-suggest-row.is-active').textContent).toContain('Niu.2026');
        key('Enter');
        expect(view.dom.querySelector('.lb-md-citation').dataset.raw).toBe('@Niu.2026');
        expect(document.querySelector('.lb-md-suggest').style.display).toBe('none');
        expect(ed.getValue()).toBe('Laut [@Niu.2026]');
    });

    it('keyboard: a bare "@" in prose keeps Enter a line break until a hit is chosen', async () => {
        const ed = await open('');
        const view = ed.view;
        view.focus();
        const key = (k) => view.dom.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
        const listed = (n) => vi.waitFor(() => {
            if (document.querySelectorAll('.lb-md-suggest-row').length !== n) throw new Error('no list');
        });
        view.dispatch(view.state.tr.insertText('Danke @', 1));
        await listed(2);
        key('Enter');                              // the editor's own Enter: a new paragraph
        expect(view.dom.querySelector('.lb-md-citation')).toBeFalsy();
        expect(view.state.doc.childCount).toBe(2);
        // A typed key that begins the first hit's does pick, as does "[@".
        view.dispatch(view.state.tr.insertText('@pe'));
        await listed(1);
        key('Enter');
        expect(view.dom.querySelector('.lb-md-citation').dataset.raw).toBe('@Peck2005');
        expect(view.state.doc.childCount).toBe(2);
    });

    it('keyboard: Escape holds for its spot only while the trigger is there', async () => {
        const ed = await open('');
        const view = ed.view;
        view.focus();
        const box = () => document.querySelector('.lb-md-suggest');
        view.dispatch(view.state.tr.insertText('Laut @', 1));
        await vi.waitFor(() => { if (!box() || box().style.display !== 'block') throw new Error('closed'); });
        view.dom.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
        expect(box().style.display).toBe('none');
        // Gone and typed again at the same offset: the list opens again.
        const end = view.state.selection.from;
        view.dispatch(view.state.tr.delete(end - 1, end));
        view.dispatch(view.state.tr.insertText('@'));
        await vi.waitFor(() => { if (box().style.display !== 'block') throw new Error('still dismissed'); });
    });

    it('a chip popover closes on an edit and never writes to a moved position', async () => {
        const ed = await open('Siehe [@Peck2005, S. 3].\n');
        await tick();
        await tick();
        const view = ed.view;
        view.dom.querySelector('.lb-md-cite-chip').click();
        expect(document.querySelector('.lb-md-pop')).toBeTruthy();
        view.dispatch(view.state.tr.insertText('Vorher ', 1));
        expect(document.querySelector('.lb-md-pop')).toBeFalsy();
        expect(ed.getValue()).toBe('Vorher Siehe [@Peck2005, S. 3].\n');
    });

    it('keyboard: Enter on a selected chip opens its popover; Escape closes only the popover', async () => {
        const ed = await open('Siehe [@Peck2005, S. 3].\n');
        await tick();
        await tick();
        const view = ed.view;
        let pos = -1;
        view.state.doc.descendants((n, p) => { if (n.type.name === 'citation') pos = p; });
        view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, pos)));
        view.dom.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
        const pop = document.querySelector('.lb-md-pop');
        expect(pop.textContent).toContain('Lehm im Bestand');
        expect(document.activeElement).toBe(pop.querySelector('input'));
        expect(pop.querySelector('input').value).toBe('S. 3');
        // A new locator, committed with Enter, is the node's new source.
        const input = pop.querySelector('input');
        input.value = 'S. 7';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
        expect(ed.getValue()).toBe('Siehe [@Peck2005, S. 7].\n');

        const outer = vi.fn();
        window.addEventListener('keydown', outer);
        view.dom.querySelector('.lb-md-cite-chip').click();
        expect(document.querySelector('.lb-md-pop')).toBeTruthy();
        document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
        expect(document.querySelector('.lb-md-pop')).toBeFalsy();
        expect(outer).not.toHaveBeenCalled();
        window.removeEventListener('keydown', outer);
    });
});

describe('compact editor', () => {
    it('offers only the essentials in the slash menu', async () => {
        const ed = await open('', { compact: true, t: (k) => k });
        const frame = ed.view.dom.closest('.lb-md-editor');
        expect(frame.classList.contains('is-compact')).toBe(true);
        ed.view.dispatch(ed.view.state.tr.insertText('/'));
        const rows = () => frame.querySelectorAll('.milkdown-slash-menu .menu-group li');
        // the menu opens on Milkdown's own timer: wait for it, a slow runner needs longer
        await vi.waitFor(() => { if (!rows().length) throw new Error('no slash menu yet'); });
        const items = [...rows()].map((li) => li.textContent.trim());
        expect(items).toEqual([
            'editor.slash.paragraph', 'editor.slash.heading', 'editor.slash.quote',
            'editor.slash.bullet', 'editor.slash.ordered', 'editor.slash.tasks', 'editor.slash.cite',
        ]);
    });

    // Tables and code blocks are not offered, but a note that has them
    // still opens and comes back untouched.
    it.each(CORPUS)('%s comes back byte-identical', async (_file, text) => {
        const ed = await open(text, { compact: true });
        expect(ed.serialize()).toBe(text);
    });
});
