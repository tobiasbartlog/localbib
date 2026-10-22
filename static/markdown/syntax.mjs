// The Markdown syntax of LocalBib's own inline nodes (ADR-0026), and nothing
// else: no Milkdown, no DOM. The editor, its round-trip tests and the read
// view renderer in app.js (a pinned twin, tests/js/markdown_syntax.test.js)
// all read it from here.
//
//   citation   [@key]  [@key, S. 12]  [@a; @b, p. 4]  [-@key]   (Pandoc)
//   wikilink   [[id]]  [[id|Alias]]

// Pandoc citekey: starts with a letter, digit or _, internal punctuation is
// allowed, it never ends on punctuation - "[@Niu.2026]." keeps its period
// outside, and "Niu.2026" stays one key.
export const KEY = String.raw`[\p{L}\p{N}_](?:[\p{L}\p{N}_:.#$%&+?<>~\/-]*[\p{L}\p{N}_])?`;
const ITEM = String.raw`-?@${KEY}(?:,\s*[^;\[\]\n]+?)?`;
export const CITE_SOURCE = String.raw`\[(${ITEM}(?:;\s*${ITEM})*)\]`;
export const CITE_RE = new RegExp(CITE_SOURCE, 'gu');
// Same shape as the wikilink indexer's WIKILINK_RE on the Python side.
export const WIKI_SOURCE = String.raw`\[\[([^\[\]|\n]+?)(?:\|([^\[\]\n]*))?\]\]`;
export const WIKI_RE = new RegExp(WIKI_SOURCE, 'g');

const ITEM_RE = new RegExp(String.raw`^(-?)@(${KEY})(?:,\s*(.+))?$`, 'u');

// "@a, S. 12; -@b" -> [{suppress, key, locator}]
export function parseCiteItems(raw) {
    return String(raw || '').split(';').map((part) => {
        const m = part.trim().match(ITEM_RE);
        return m ? { suppress: m[1] === '-', key: m[2], locator: (m[3] || '').trim() } : null;
    }).filter(Boolean);
}

export function citeRaw(items) {
    return items.map((i) => `${i.suppress ? '-' : ''}@${i.key}${i.locator ? ', ' + i.locator : ''}`).join('; ');
}

// The page a locator names ("S. 12", "p. 4-6", "12") or null.
export function pageOf(locator) {
    const m = String(locator || '').match(/^(?:S\.|p\.|pp\.|Seite|page)?\s*(\d+)/i);
    return m ? Number(m[1]) : null;
}

// "id|Alias" -> {id, alias}
export function parseWikiRaw(raw) {
    const at = String(raw || '').indexOf('|');
    const id = (at < 0 ? raw : raw.slice(0, at)).trim();
    const alias = at < 0 ? '' : raw.slice(at + 1).trim();
    return { id, alias };
}

// A text run split into text / citation / wikilink parts; null when the text
// holds neither. `raw` is the source between the brackets, the node's only
// attribute, so an untouched node is written back byte for byte.
export function splitInline(value) {
    const hits = [];
    for (const re of [CITE_RE, WIKI_RE]) {
        re.lastIndex = 0;
        let m;
        while ((m = re.exec(value))) {
            hits.push(re === CITE_RE
                ? { start: m.index, end: m.index + m[0].length, node: { type: 'citation', raw: m[1] } }
                : { start: m.index, end: m.index + m[0].length, node: { type: 'wikilink', raw: m[0].slice(2, -2) } });
        }
    }
    if (!hits.length) return null;
    hits.sort((a, b) => a.start - b.start);
    const out = [];
    let pos = 0;
    for (const h of hits) {
        if (h.start < pos) continue;
        if (h.start > pos) out.push({ type: 'text', value: value.slice(pos, h.start) });
        out.push(h.node);
        pos = h.end;
    }
    if (pos < value.length) out.push({ type: 'text', value: value.slice(pos) });
    return out;
}

// "Müller, A.; Schmidt, B." / "Müller, Anna, Schmidt, Bernd" / "Anna Müller,
// Bernd Schmidt" -> "Müller & Schmidt"; three or more -> "Müller et al.".
export function shortAuthors(authors) {
    const a = String(authors || '').trim();
    if (!a) return '';
    let names;
    if (a.includes(';')) names = a.split(';').map((x) => x.split(',')[0].trim()).filter(Boolean);
    else {
        const segs = a.split(',').map((x) => x.trim()).filter(Boolean);
        if (segs.length && segs[0].includes(' ')) names = segs.map((s) => s.split(/\s+/).pop());
        else names = segs.filter((_, i) => i % 2 === 0);
    }
    if (!names.length) return '';
    if (names.length === 1) return names[0];
    if (names.length === 2) return `${names[0]} & ${names[1]}`;
    return `${names[0]} et al.`;
}

// The chip text of a library entry: "Müller 2021", else its citekey.
export function citeLabel(paper) {
    if (!paper) return '';
    return [shortAuthors(paper.authors) || paper.cite_key, paper.year].filter(Boolean).join(' ');
}
