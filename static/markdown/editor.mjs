// The one seam to the WYSIWYG Markdown editor (ADR-0026), like
// createPdfJsViewer is the seam to PDF.js: app.js reaches it only through
// `window.LB_MD_EDITOR ?? import('/static/markdown/editor.mjs')`, tests
// replace it with a fake that renders a textarea.
//
//   const ed = await createMarkdownEditor(host, { value, onChange, readonly,
//                                                 placeholder, resolvers, t, compact })
//   ed.getValue() / ed.setValue(text) / ed.setReadonly(on) / ed.focus() / ed.destroy()
//
// Fidelity is the contract: `onChange` fires only after an edit, and an edit
// that ends where it started hands back the original text byte for byte.
import {
    CrepeBuilder, blockEdit, cursor, linkTooltip, listItem, placeholder, table, toolbar,
    $prose, Plugin, PluginKey, NodeSelection, commandsCtx, editorViewCtx,
    clearTextInCurrentBlockCommand, replaceAll,
} from '../vendor/milkdown-7.22.3/milkdown.mjs';
import { createPopover } from './chips.mjs';
import { citationNode } from './citation.mjs';
import { lbMarkdown, withEdges } from './fidelity.mjs';
import { suggestPlugin } from './suggest.mjs';
import { wikilinkNode } from './wikilink.mjs';

const ICON_CITE = '<svg viewBox="0 0 24 24" width="24" height="24"><path fill="currentColor" d="M7 17h3l2-4V7H6v6h3zm8 0h3l2-4V7h-6v6h3z"/></svg>';
const ICON_LINK = '<svg viewBox="0 0 24 24" width="24" height="24"><path fill="currentColor" d="M10.6 13.4a1 1 0 0 1 0-1.4l3-3a1 1 0 1 1 1.4 1.4l-3 3a1 1 0 0 1-1.4 0zM8.5 19a4.5 4.5 0 0 1-3.2-7.7l2-2 1.4 1.4-2 2a2.5 2.5 0 0 0 3.6 3.6l2-2 1.4 1.4-2 2A4.5 4.5 0 0 1 8.5 19zm7.6-4.2-1.4-1.4 2-2a2.5 2.5 0 0 0-3.6-3.6l-2 2-1.4-1.4 2-2a4.5 4.5 0 0 1 6.4 6.4z"/></svg>';

const CHANGE_DELAY = 150;

// What the nodes need from the page, built from the caller's resolvers.
// Lookups are cached per editor; a chip asks once and is redrawn in place
// when the answer arrives.
function makeHost(resolvers, t) {
    const cite = resolvers.citation || null;
    const wiki = resolvers.wikilink || null;
    const papers = new Map();
    const entities = new Map();
    let wanted = new Set();
    let redraw = false;
    const host = {
        t,
        views: new Set(),
        hasWiki: !!wiki,
        wikiTitle: () => (wiki && wiki.title) || t('editor.suggest.wikiHead'),
        refresh() {
            if (redraw) return;
            redraw = true;
            queueMicrotask(() => { redraw = false; host.views.forEach((v) => v.draw()); });
        },
        // undefined while unknown, null when the library has no such key
        paper(key) {
            if (papers.has(key)) return papers.get(key);
            if (!cite || !cite.resolve) return null;
            if (!wanted.size) queueMicrotask(flushPapers);
            wanted.add(key);
            return undefined;
        },
        remember(p) { if (p && p.cite_key) papers.set(p.cite_key, p); },
        entity(id) {
            if (!wiki || !wiki.resolve) return undefined;
            if (entities.has(id)) return entities.get(id);
            const got = wiki.resolve(id);
            if (got && typeof got.then === 'function') {
                entities.set(id, undefined);
                got.then((v) => { entities.set(id, v || null); host.refresh(); }, () => entities.delete(id));
                return undefined;
            }
            entities.set(id, got || null);
            return got || null;
        },
        rememberEntity(it) { if (it && it.id) entities.delete(it.id); },
        searchPapers: (q) => (cite && cite.search ? cite.search(q) : []),
        searchEntities: (q) => (wiki && wiki.search ? wiki.search(q) : []),
        open: (href) => (resolvers.open ? resolvers.open(href) : (window.location.hash = href)),
    };
    function flushPapers() {
        const keys = [...wanted];
        wanted = new Set();
        keys.forEach((k) => papers.set(k, undefined));
        Promise.resolve(cite.resolve(keys)).then((found) => {
            const byKey = new Map((found || []).map((p) => [p.cite_key, p]));
            keys.forEach((k) => papers.set(k, byKey.get(k) || null));
            host.refresh();
        }, () => keys.forEach((k) => papers.delete(k)));
    }
    return host;
}

// The slash menu's built-in groups. `compact` (a small field such as an
// Item's notes) keeps the essentials in one short list: the tabs and the
// block handle go by CSS, tables and code stay readable but are not offered.
function slashGroups(t, compact) {
    if (compact) {
        return {
            textGroup: {
                label: t('editor.slash.text'), text: { label: t('editor.slash.paragraph') },
                h1: null, h2: null, h3: { label: t('editor.slash.heading') },
                h4: null, h5: null, h6: null,
                quote: { label: t('editor.slash.quote') }, divider: null,
            },
            listGroup: {
                label: t('editor.slash.lists'), bulletList: { label: t('editor.slash.bullet') },
                orderedList: { label: t('editor.slash.ordered') }, taskList: { label: t('editor.slash.tasks') },
            },
            advancedGroup: null,
        };
    }
    return {
        textGroup: {
            label: t('editor.slash.text'), text: { label: t('editor.slash.paragraph') },
            h1: { label: t('editor.slash.h1') }, h2: { label: t('editor.slash.h2') }, h3: { label: t('editor.slash.h3') },
            h4: null, h5: null, h6: null,
            quote: { label: t('editor.slash.quote') }, divider: { label: t('editor.slash.divider') },
        },
        listGroup: {
            label: t('editor.slash.lists'), bulletList: { label: t('editor.slash.bullet') },
            orderedList: { label: t('editor.slash.ordered') }, taskList: { label: t('editor.slash.tasks') },
        },
        advancedGroup: {
            label: t('editor.slash.more'), image: null, math: null,
            codeBlock: { label: t('editor.slash.code') }, table: { label: t('editor.slash.table') },
        },
    };
}

// Enter on a selected chip opens its popover - the keyboard way to a
// citation's page or a wikilink's alias.
function chipKeys(host) {
    return $prose(() => new Plugin({
        key: new PluginKey('lbMdChipKeys'),
        props: {
            handleKeyDown(view, e) {
                const sel = view.state.selection;
                if (e.key !== 'Enter' || !(sel instanceof NodeSelection)) return false;
                const name = sel.node.type.name;
                if (name !== 'citation' && name !== 'wikilink') return false;
                const dom = view.nodeDOM(sel.from);
                if (!dom) return false;
                host.popover.show(view, sel.from, dom.querySelector('.lb-md-cite-chip') || dom, { focus: true });
                return true;
            },
        },
    }));
}

// Every doc change reaches `onDoc`; a lookup redraw never does (it is not a
// transaction).
function changePlugin(onDoc) {
    return $prose(() => new Plugin({
        key: new PluginKey('lbMdChange'),
        view: () => ({
            update(view, prev) { if (!view.state.doc.eq(prev.doc)) onDoc(); },
        }),
    }));
}

export async function createMarkdownEditor(root, options = {}) {
    const t = options.t || ((key) => key);
    const resolvers = options.resolvers || {};
    const host = makeHost(resolvers, t);
    host.popover = createPopover(host);

    let original = String(options.value || '');
    let last = original;
    let baseline = null;
    let timer = null;

    const frame = document.createElement('div');
    frame.className = options.compact ? 'lb-md-editor is-compact' : 'lb-md-editor';
    root.append(frame);
    // Escape that closes one of Crepe's menus (slash menu, toolbar, link
    // tooltip) ends there; the page or dialog around the editor never sees
    // it. Seen on the way down, because Crepe hides the menu on its own.
    let menuEscape = false;
    frame.addEventListener('keydown', (e) => {
        menuEscape = e.key === 'Escape' && !!frame.querySelector('[data-show="true"]');
    }, true);
    frame.addEventListener('keydown', (e) => {
        if (menuEscape) { e.stopPropagation(); menuEscape = false; }
    });

    const insert = (text) => (ctx) => {
        ctx.get(commandsCtx).call(clearTextInCurrentBlockCommand.key);
        const view = ctx.get(editorViewCtx);
        view.dispatch(view.state.tr.insertText(text));
        view.focus();
    };
    const crepe = new CrepeBuilder({ root: frame, defaultValue: original });
    crepe
        .addFeature(listItem)
        .addFeature(cursor)
        .addFeature(linkTooltip, { inputPlaceholder: t('editor.link.placeholder') })
        .addFeature(toolbar, {
            boldLabel: t('editor.toolbar.bold'), italicLabel: t('editor.toolbar.italic'),
            codeLabel: t('editor.toolbar.code'), linkLabel: t('editor.toolbar.link'),
            strikethroughLabel: t('editor.toolbar.strike'),
        })
        .addFeature(placeholder, { text: options.placeholder || t('editor.placeholder'), mode: 'block' })
        .addFeature(table)
        .addFeature(blockEdit, {
            ...slashGroups(t, !!options.compact),
            buildMenu: (builder) => {
                const group = builder.addGroup('localbib', t('editor.slash.references'));
                group.addItem('cite', { label: t('editor.slash.cite'), icon: ICON_CITE, onRun: insert('@') });
                if (host.hasWiki) group.addItem('wiki', { label: host.wikiTitle(), icon: ICON_LINK, onRun: insert('[[') });
            },
        });
    if (options.readonly) crepe.setReadonly(true);

    const read = () => {
        const md = crepe.getMarkdown();
        return md === baseline ? original : withEdges(original, md);
    };
    const emit = () => {
        clearTimeout(timer);
        timer = null;
        if (baseline === null) return;
        const value = read();
        if (value === last) return;
        last = value;
        if (options.onChange) options.onChange(value);
    };

    const md = lbMarkdown();
    crepe.editor
        .config(md.config)
        .use(md.remark)
        .use(citationNode(host))
        .use(wikilinkNode(host))
        .use(suggestPlugin(host))
        .use(chipKeys(host))
        .use(changePlugin(() => {
            // A chip popover holds its chip's position; any edit may move it.
            host.popover.close();
            clearTimeout(timer);
            timer = setTimeout(emit, CHANGE_DELAY);
        }));
    await crepe.create();
    baseline = crepe.getMarkdown();

    let destroyed = false;
    return {
        getValue: () => (timer ? (emit(), last) : read()),
        // What the serialiser itself writes, without the untouched-text
        // shortcut of getValue() - the round-trip tests measure this.
        serialize: () => withEdges(original, crepe.getMarkdown()),
        get view() { return crepe.editor.action((ctx) => ctx.get(editorViewCtx)); },
        // A value from outside (a reload) becomes the new original; it is
        // not an edit and fires no onChange.
        setValue(text) {
            const next = String(text || '');
            if (next === last) return;
            clearTimeout(timer);
            timer = null;
            original = next;
            last = next;
            baseline = null;
            crepe.editor.action(replaceAll(next, true));
            baseline = crepe.getMarkdown();
        },
        setReadonly(on) { crepe.setReadonly(!!on); },
        focus() { crepe.editor.action((ctx) => ctx.get(editorViewCtx).focus()); },
        // Pending typing is handed out before the editor goes.
        async destroy() {
            if (destroyed) return;
            destroyed = true;
            if (timer) emit();
            host.popover.destroy();
            await crepe.destroy();
            frame.remove();
        },
    };
}
