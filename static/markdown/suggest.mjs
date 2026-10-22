// Typing "@" (or "[@") opens a search of the library, typing "[[" a search
// of the wikilink resolver. Arrow keys move, Enter/Tab picks, Escape closes
// for that spot. A completed "[@…]" or "[[…]]" typed by hand turns into its
// chip on the closing bracket.
import { $prose, Plugin, PluginKey } from '../vendor/milkdown-7.22.3/milkdown.mjs';
import { el } from './chips.mjs';
import { CITE_SOURCE, WIKI_SOURCE, citeLabel } from './syntax.mjs';

const CITE_END = new RegExp(CITE_SOURCE + '$', 'u');
const WIKI_END = new RegExp(WIKI_SOURCE + '$');
const MAX_ITEMS = 7;

export function suggestPlugin(host) {
    return $prose(() => {
        let box = null;
        let state = null; // {kind, from, to, query, items, index}
        let dismissedAt = -1;
        let seq = 0;

        const close = () => {
            state = null;
            seq++;
            if (box) box.style.display = 'none';
        };
        const render = (view) => {
            if (!state) return;
            if (!box) {
                box = el('div', 'lb-md-suggest');
                box.setAttribute('role', 'listbox');
                document.body.append(box);
            }
            box.replaceChildren(el('div', 'lb-md-suggest-head',
                state.kind === 'cite' ? host.t('editor.suggest.citeHead') : host.wikiTitle()));
            if (state.loading && !state.items.length) {
                box.append(el('div', 'lb-md-suggest-empty', host.t('editor.suggest.loading')));
            } else if (!state.items.length) {
                box.append(el('div', 'lb-md-suggest-empty',
                    host.t(state.kind === 'cite' ? 'editor.suggest.noCite' : 'editor.suggest.noWiki')));
            }
            state.items.forEach((it, i) => {
                const row = el('div', 'lb-md-suggest-row' + (i === state.index ? ' is-active' : ''));
                row.setAttribute('role', 'option');
                row.setAttribute('aria-selected', i === state.index ? 'true' : 'false');
                const top = el('div', 'lb-md-suggest-top');
                if (state.kind === 'cite') {
                    top.append(el('span', 'lb-md-suggest-main', citeLabel(it)), el('span', 'lb-md-suggest-key', it.cite_key));
                    row.append(top, el('div', 'lb-md-suggest-sub', it.title || ''));
                } else {
                    if (it.kindLabel) top.append(el('span', 'lb-md-suggest-kind', it.kindLabel));
                    top.append(el('span', 'lb-md-suggest-main', it.title || it.id));
                    row.append(top, el('div', 'lb-md-suggest-sub is-mono', it.id));
                }
                row.addEventListener('mousedown', (e) => { e.preventDefault(); state.index = i; pick(view); });
                box.append(row);
            });
            box.style.display = 'block';
            try {
                const c = view.coordsAtPos(state.from);
                box.style.left = `${Math.max(8, Math.min(c.left, window.innerWidth - box.offsetWidth - 8))}px`;
                const below = c.bottom + 6;
                box.style.top = `${below + box.offsetHeight > window.innerHeight - 8 && c.top > box.offsetHeight + 14 ? c.top - box.offsetHeight - 6 : below}px`;
            } catch (e) { /* no layout (tests): the list still works */ }
        };
        const pick = (view) => {
            const it = state && state.items[state.index];
            if (!it) return close();
            const schema = view.state.schema;
            const node = state.kind === 'cite'
                ? schema.nodes.citation.create({ raw: '@' + it.cite_key })
                : schema.nodes.wikilink.create({ raw: it.id });
            if (state.kind === 'cite') host.remember(it);
            else host.rememberEntity(it);
            view.dispatch(view.state.tr.replaceWith(state.from, state.to, [node, schema.text(' ')]));
            close();
            view.focus();
        };
        const search = (view, next) => {
            const mine = ++seq;
            const run = next.kind === 'cite' ? host.searchPapers(next.query) : host.searchEntities(next.query);
            Promise.resolve(run).then((items) => {
                if (mine !== seq || !state) return;
                state.items = (items || []).slice(0, MAX_ITEMS);
                state.index = Math.min(state.index, Math.max(0, state.items.length - 1));
                state.loading = false;
                render(view);
            }, () => {
                if (mine !== seq || !state) return;
                state.loading = false;
                render(view);
            });
        };
        const detect = (view) => {
            const { selection } = view.state;
            if (!view.editable || !selection.empty) return close();
            const $from = selection.$from;
            if ($from.parent.type.spec.code) return close();
            const before = $from.parent.textBetween(Math.max(0, $from.parentOffset - 60), $from.parentOffset, null, '￼');
            let m = before.match(/(?:^|[\s(])(\[?@)([\p{L}\p{N}_:.\-]*)$/u);
            let kind, len, query, explicit;
            if (m) { kind = 'cite'; query = m[2]; len = m[1].length + query.length; explicit = m[1] === '[@'; }
            else if (host.hasWiki && (m = before.match(/\[\[([^\[\]|\n]*)$/))) { kind = 'wiki'; query = m[1]; len = query.length + 2; explicit = true; }
            else {
                // The trigger is gone: an Escape no longer holds for its spot.
                dismissedAt = -1;
                return close();
            }
            const from = $from.pos - len;
            if (from === dismissedAt) return close();
            if (state && state.kind === kind && state.from === from && state.query === query) {
                state.to = $from.pos;
                return;
            }
            const same = state && state.kind === kind && state.from === from;
            state = { kind, from, to: $from.pos, query, explicit, moved: false, items: same ? state.items : [], index: 0, loading: true };
            render(view);
            search(view, state);
        };
        // A bare "@" may be prose ("@team", a line ending in " @"): Enter
        // takes a hit only once the user chose one with the arrows or the
        // typed key begins the first hit's. "[@", "[[" and Tab always pick.
        const enterPicks = () => {
            if (state.explicit || state.moved) return true;
            const key = String((state.items[0] && state.items[0].cite_key) || '').toLowerCase();
            return !!state.query && key.startsWith(state.query.toLowerCase());
        };
        // "]" that completes a citation or wikilink typed by hand.
        const closeBracket = (view, from, to, text) => {
            if (text !== ']') return false;
            const $from = view.state.doc.resolve(from);
            if ($from.parent.type.spec.code) return false;
            const before = $from.parent.textBetween(Math.max(0, $from.parentOffset - 200), $from.parentOffset, null, '￼') + ']';
            const schema = view.state.schema;
            let m = before.match(WIKI_END);
            let node = null;
            if (m) node = schema.nodes.wikilink.create({ raw: m[0].slice(2, -2) });
            else if ((m = before.match(CITE_END))) node = schema.nodes.citation.create({ raw: m[1] });
            if (!node) return false;
            const start = from - (m[0].length - 1);
            view.dispatch(view.state.tr.replaceWith(start, to, node));
            close();
            return true;
        };

        return new Plugin({
            key: new PluginKey('lbMdSuggest'),
            view: () => ({
                update: (view) => detect(view),
                destroy: () => { if (box) box.remove(); box = null; },
            }),
            props: {
                handleTextInput: closeBracket,
                // A DOM handler runs before every keymap plugin, so Enter
                // picks a suggestion instead of splitting the list item.
                handleDOMEvents: {
                    keydown: (view, e) => {
                        if (!state) return false;
                        const n = Math.max(1, state.items.length);
                        let handled = true;
                        if (e.key === 'ArrowDown') { state.index = (state.index + 1) % n; state.moved = true; render(view); }
                        else if (e.key === 'ArrowUp') { state.index = (state.index - 1 + n) % n; state.moved = true; render(view); }
                        else if (e.key === 'Tab' && state.items.length) pick(view);
                        else if (e.key === 'Enter' && state.items.length && enterPicks()) pick(view);
                        else if (e.key === 'Escape') { dismissedAt = state.from; close(); }
                        else handled = false;
                        // Not up to a dialog around the editor: Escape closes the list only.
                        if (handled) { e.preventDefault(); e.stopPropagation(); }
                        return handled;
                    },
                    blur: () => { close(); return false; },
                },
            },
        });
    });
}
