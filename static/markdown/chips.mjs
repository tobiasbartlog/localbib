// Shared bits of the two inline nodes: the atom node schema, its live view
// (refreshed in place when a lookup arrives, never through a transaction -
// a lookup must not count as an edit) and the one popover per editor.
import { $nodeSchema, $view } from '../vendor/milkdown-7.22.3/milkdown.mjs';

export function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

// An inline atom whose only attribute is its source text.
export function atomSchema(name, wrapMd) {
    return $nodeSchema(name, () => ({
        group: 'inline',
        inline: true,
        atom: true,
        selectable: true,
        attrs: { raw: { default: '' } },
        parseDOM: [{ tag: `span[data-type="${name}"]`, getAttrs: (dom) => ({ raw: dom.dataset.raw || '' }) }],
        // Clipboard and drag: the source text, so pasting into a plain field
        // keeps the citation readable.
        toDOM: (node) => ['span', { 'data-type': name, 'data-raw': node.attrs.raw }, wrapMd(node.attrs.raw)],
        leafText: (node) => wrapMd(node.attrs.raw),
        parseMarkdown: {
            match: (n) => n.type === name,
            runner: (state, n, type) => { state.addNode(type, { raw: n.raw }); },
        },
        toMarkdown: {
            match: (n) => n.type.name === name,
            runner: (state, n) => { state.addNode(name, undefined, undefined, { raw: n.attrs.raw }); },
        },
    }));
}

// The live view: `fill(dom, raw)` draws the chip; `host.views` lets the
// editor redraw every chip when a lookup resolves.
export function atomView(schema, host, name, fill) {
    return $view(schema.node, () => (node, view, getPos) => {
        const dom = el('span', `lb-md-${name}`);
        dom.dataset.type = name;
        dom.contentEditable = 'false';
        const nv = {
            dom,
            node,
            draw() {
                dom.dataset.raw = nv.node.attrs.raw;
                dom.replaceChildren();
                fill(dom, nv.node.attrs.raw);
            },
            update(next) {
                if (next.type !== nv.node.type) return false;
                nv.node = next;
                nv.draw();
                return true;
            },
            selectNode() { dom.classList.add('is-selected'); },
            deselectNode() { dom.classList.remove('is-selected'); },
            stopEvent: (e) => e.type === 'mousedown' || e.type === 'click',
            ignoreMutation: () => true,
            destroy() { host.views.delete(nv); },
        };
        dom.addEventListener('mousedown', (e) => e.preventDefault());
        dom.addEventListener('click', (e) => {
            e.preventDefault();
            const pos = typeof getPos === 'function' ? getPos() : null;
            if (pos != null) host.popover.show(view, pos, e.target);
        });
        host.views.add(nv);
        nv.draw();
        return nv;
    });
}

// One popover per editor. `build(pop, ctx)` fills it for the node at `pos`.
export function createPopover(host) {
    let pop = null;
    let onKey = null;
    const builders = {};
    const close = () => {
        if (onKey) { document.removeEventListener('keydown', onKey, true); onKey = null; }
        if (pop) { pop.remove(); pop = null; }
    };
    const outside = (e) => {
        if (pop && !pop.contains(e.target) && !(e.target.closest && e.target.closest('.lb-md-citation, .lb-md-wikilink'))) close();
    };
    document.addEventListener('mousedown', outside);
    return {
        register(name, build) { builders[name] = build; },
        get open() { return !!pop; },
        close,
        show(view, pos, target, { focus = false } = {}) {
            close();
            const node = view.state.doc.nodeAt(pos);
            if (!node || !builders[node.type.name]) return;
            pop = el('div', 'lb-md-pop');
            pop.setAttribute('role', 'dialog');
            // The editor closes the popover on every doc change, so `pos`
            // still holds the chip; the check keeps a stale one from ever
            // writing to whatever moved there.
            const still = () => view.state.doc.nodeAt(pos) === node;
            const replaceRaw = (raw) => { if (still()) view.dispatch(view.state.tr.setNodeMarkup(pos, null, { raw })); };
            const remove = () => { if (still()) view.dispatch(view.state.tr.delete(pos, pos + node.nodeSize)); };
            const done = () => { close(); view.focus(); };
            builders[node.type.name](pop, { node, target, replaceRaw, remove, done, readonly: !view.editable });
            // Escape closes the popover only, wherever the focus is - never
            // the page or dialog around the editor (captured before them).
            onKey = (e) => {
                if (e.key !== 'Escape') return;
                e.preventDefault();
                e.stopPropagation();
                done();
            };
            document.addEventListener('keydown', onKey, true);
            document.body.append(pop);
            const r = target.getBoundingClientRect();
            // Inside the window: shifted left at the right edge, above the
            // chip when there is no room below.
            pop.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8))}px`;
            const below = r.bottom + 6;
            pop.style.top = `${below + pop.offsetHeight > window.innerHeight - 8 && r.top > pop.offsetHeight + 14 ? r.top - pop.offsetHeight - 6 : below}px`;
            if (focus) {
                const first = pop.querySelector('input, button');
                if (first) first.focus();
            }
        },
        destroy() { close(); document.removeEventListener('mousedown', outside); },
    };
}

// A labelled one-line field that commits on Enter.
export function popField(label, value, placeholder, commit) {
    const row = el('label', 'lb-md-pop-field');
    row.append(el('span', null, label));
    const input = el('input');
    input.value = value || '';
    input.placeholder = placeholder || '';
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit(input.value.trim()); }
    });
    row.append(input);
    return row;
}

export function popButton(text, primary, onClick) {
    const b = el('button', 'lb-md-btn' + (primary ? ' is-primary' : ''), text);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
}
