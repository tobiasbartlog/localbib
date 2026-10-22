// The citation node: [@key], [@key, S. 12], [@a; @b] (Pandoc). One chip per
// cited source, labelled "Müller 2021" from the library; an unknown key shows
// dashed as missing and stays in the text exactly as written (a renamed
// citekey is never rewritten - the app does not change a user's file unasked).
import { atomSchema, atomView, el, popButton, popField } from './chips.mjs';
import { citeLabel, citeRaw, pageOf, parseCiteItems } from './syntax.mjs';

export function citationNode(host) {
    const schema = atomSchema('citation', (raw) => `[${raw}]`);
    const view = atomView(schema, host, 'citation', (dom, raw) => {
        parseCiteItems(raw).forEach((item, i) => {
            const p = host.paper(item.key);
            const missing = p === null;
            const chip = el('span', 'lb-md-cite-chip' + (missing ? ' is-missing' : ''));
            chip.dataset.index = String(i);
            chip.dataset.key = item.key;
            chip.title = p ? `${p.title || ''}\n${p.authors || ''}${p.year ? ' · ' + p.year : ''}`
                : missing ? host.t('editor.cite.missing', { key: item.key }) : item.key;
            chip.append(el('span', 'lb-md-cite-label', p ? citeLabel(p) : '@' + item.key));
            if (item.locator) chip.append(el('span', 'lb-md-cite-loc', item.locator));
            dom.append(chip);
        });
    });
    host.popover.register('citation', (pop, { node, target, replaceRaw, remove, done, readonly }) => {
        const items = parseCiteItems(node.attrs.raw);
        const chip = target.closest && target.closest('.lb-md-cite-chip');
        const idx = Math.min(Number((chip && chip.dataset.index) || 0), items.length - 1);
        const item = items[idx];
        if (!item) return;
        const p = host.paper(item.key);
        if (p) {
            pop.append(el('div', 'lb-md-pop-title', p.title || item.key));
            pop.append(el('div', 'lb-md-pop-meta', `${p.authors || ''}${p.year ? ' · ' + p.year : ''}`));
        } else {
            pop.append(el('div', 'lb-md-pop-title is-missing', host.t('editor.cite.missing', { key: item.key })));
            pop.append(el('div', 'lb-md-pop-meta', host.t('editor.cite.missingHint')));
        }
        if (!readonly) {
            pop.append(popField(host.t('editor.cite.locator'), item.locator, host.t('editor.cite.locatorPlaceholder'), (v) => {
                items[idx] = { ...item, locator: v };
                replaceRaw(citeRaw(items));
                done();
            }));
        }
        const actions = el('div', 'lb-md-pop-actions');
        if (p) {
            const page = p.has_pdf ? pageOf(item.locator) : null;
            actions.append(popButton(page ? host.t('editor.cite.openPage', { page }) : host.t('editor.cite.open'), true, () => {
                done();
                host.open(`#/paper/${p.id}${page ? '?page=' + page : ''}`);
            }));
        }
        if (!readonly) {
            actions.append(popButton(host.t('editor.remove'), false, () => {
                items.splice(idx, 1);
                if (items.length) replaceRaw(citeRaw(items));
                else remove();
                done();
            }));
        }
        if (actions.childNodes.length) pop.append(actions);
    });
    return [schema, view];
}
