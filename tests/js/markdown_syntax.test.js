// The citation/wikilink syntax (ADR-0026): static/markdown/syntax.mjs is the
// source, app.js keeps a synchronous twin for read views. This pins the twin
// to the source and checks the read renderer LocalBib.renderMarkdown.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { KEY, WIKI_SOURCE, CITE_RE, parseCiteItems, citeRaw, pageOf, splitInline, citeLabel } from '../../static/markdown/syntax.mjs';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const APP = readFileSync(resolve(__dirname, '../../static/app.js'), 'utf8');
const twin = (name) => {
    const m = new RegExp('const ' + name + ' = String\\.raw`([^`]*)`;').exec(APP);
    return m && m[1];
};

describe('citation syntax', () => {
    it('app.js renders with exactly the syntax of syntax.mjs', () => {
        expect(twin('MD_KEY')).toBe(KEY);
        expect(twin('MD_WIKI_SOURCE')).toBe(WIKI_SOURCE);
        expect(twin('MD_CITE_ITEM')).toBe(String.raw`-?@${'${MD_KEY}'}(?:,\s*[^;\[\]\n]+?)?`);
    });

    it('reads Pandoc citations: locators, multi-cites, dotted keys, trailing punctuation', () => {
        const raws = (text) => [...text.matchAll(CITE_RE)].map((m) => m[1]);
        expect(raws('[@a] und [@a, S. 12] und [@a; @b, p. 4]')).toEqual(['@a', '@a, S. 12', '@a; @b, p. 4']);
        expect(raws('siehe [@Niu.2026].')).toEqual(['@Niu.2026']);
        expect(raws('[@Niu.]')).toEqual([]);
        expect(raws('[mail@example.org] und [@] und [ @a]')).toEqual([]);
        expect(parseCiteItems('@a, S. 12; -@Niu.2026')).toEqual([
            { suppress: false, key: 'a', locator: 'S. 12' },
            { suppress: true, key: 'Niu.2026', locator: '' },
        ]);
        expect(citeRaw(parseCiteItems('@a, S. 12;  @b'))).toBe('@a, S. 12; @b');
        expect([pageOf('S. 12'), pageOf('p. 4-6'), pageOf('12'), pageOf('Kap. 3'), pageOf('')]).toEqual([12, 4, 12, null, null]);
    });

    it('splits text into citation and wikilink parts', () => {
        expect(splitInline('a [[x|Y]] b [@k]')).toEqual([
            { type: 'text', value: 'a ' },
            { type: 'wikilink', raw: 'x|Y' },
            { type: 'text', value: ' b ' },
            { type: 'citation', raw: '@k' },
        ]);
        expect(splitInline('nichts hier')).toBe(null);
    });

    it('labels an item like the chip does', () => {
        expect(citeLabel({ cite_key: 'k', authors: 'Müller, Anna', year: 2021 })).toBe('Müller 2021');
        expect(citeLabel({ cite_key: 'k', authors: 'Anna Müller, Bernd Schmidt, Carla Weber', year: 2021 })).toBe('Müller et al. 2021');
        expect(citeLabel({ cite_key: 'k', authors: '', year: null })).toBe('k');
    });
});

describe('LocalBib.renderMarkdown', () => {
    it('renders chips, keeps code as text and never live markup', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: /^\/api\/papers\/lookup\?/, reply: { papers: [
                { id: 4, cite_key: 'Niu.2026', title: 'Dotted', authors: 'Niu, Li; Chen, Bo', year: 2026, doi: '', has_pdf: true },
            ] } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        const html = window.LocalBib.renderMarkdown(
            'Siehe [@Niu.2026, S. 3] und [@Gone1999], [[ziel|Ziel]].\n\n`[@code]`\n\n<img src=x onerror="alert(1)"><script>alert(2)</script>',
            { wikilink: (id) => (id === 'ziel' ? { title: 'Das Ziel', href: '#/x/ziel' } : null) });
        const el = document.createElement('div');
        el.innerHTML = html;
        expect([...el.querySelectorAll('.lb-md-cite-chip')].map((c) => c.dataset.key)).toEqual(['Niu.2026', 'Gone1999']);
        expect(el.querySelector('a.lb-md-wikilink').getAttribute('href')).toBe('#/x/ziel');
        expect(el.querySelector('a.lb-md-wikilink').textContent).toBe('↗Ziel');
        expect(el.querySelector('code').textContent).toBe('[@code]');
        expect(el.querySelector('script')).toBeFalsy();
        expect(html).not.toContain('onerror');

        document.body.append(el);
        await window.LocalBib.hydrateMarkdown(el);
        const [found, missing] = el.querySelectorAll('.lb-md-cite-chip');
        expect(found.querySelector('.lb-md-cite-label').textContent).toBe('Niu & Chen 2026');
        expect(found.dataset.href).toBe('#/paper/4?page=3');
        expect(missing.classList.contains('is-missing')).toBe(true);
        expect(calls.some((c) => c.url === '/api/papers/lookup?keys=Niu.2026&keys=Gone1999')).toBe(true);
    });
});
