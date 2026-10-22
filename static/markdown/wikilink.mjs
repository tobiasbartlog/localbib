// The wikilink node: [[id]], [[id|Alias]]. The core knows no targets: an
// editor without a `resolvers.wikilink` keeps the link as a neutral chip
// showing its id, an Add-on resolves id -> {title, href, missing}.
import { atomSchema, atomView, el, popButton, popField } from './chips.mjs';
import { parseWikiRaw } from './syntax.mjs';

export function wikilinkNode(host) {
    const schema = atomSchema('wikilink', (raw) => `[[${raw}]]`);
    const view = atomView(schema, host, 'wikilink', (dom, raw) => {
        const { id, alias } = parseWikiRaw(raw);
        const ent = host.entity(id);
        const dangling = host.hasWiki && (ent === null || (ent && ent.missing));
        dom.classList.toggle('is-dangling', !!dangling);
        dom.title = dangling ? host.t('editor.wiki.missing', { id }) : (ent && ent.title) || id;
        dom.append(el('span', 'lb-md-wiki-glyph', '↗'));
        dom.append(el('span', null, alias || (ent && ent.title) || id));
    });
    host.popover.register('wikilink', (pop, { node, replaceRaw, remove, done, readonly }) => {
        const { id, alias } = parseWikiRaw(node.attrs.raw);
        const ent = host.entity(id);
        const found = ent && !ent.missing;
        pop.append(el('div', 'lb-md-pop-title' + (found ? '' : ' is-missing'),
            found ? ent.title || id : host.t('editor.wiki.missing', { id })));
        pop.append(el('div', 'lb-md-pop-meta is-mono', `${found && ent.kindLabel ? ent.kindLabel + ' · ' : ''}${id}`));
        if (!readonly) {
            pop.append(popField(host.t('editor.wiki.alias'), alias, found ? ent.title : id, (v) => {
                replaceRaw(v ? `${id}|${v}` : id);
                done();
            }));
        }
        const actions = el('div', 'lb-md-pop-actions');
        if (found && ent.href) {
            actions.append(popButton(host.t('editor.wiki.open'), true, () => { done(); host.open(ent.href); }));
        }
        if (!readonly) actions.append(popButton(host.t('editor.remove'), false, () => { remove(); done(); }));
        if (actions.childNodes.length) pop.append(actions);
    });
    return [schema, view];
}
