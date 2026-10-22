// Round-trip fidelity (ADR-0026): load a note, save it untouched, get the same
// bytes back. Ported from the approved prototype, which brought 17 of 18
// notes of a real archive back byte-identical (docs/markdown-editor/PLAN.md):
//
//   1. the file's own edges are kept (a blank line after the frontmatter)
//   2. "[" is escaped only where it could open a link, a lone "~" never
//   3. a bare URL (GFM autolink) stays bare instead of becoming <url>
//   4. table delimiter rows are written in the file's own style
//   5. blocks that sat on consecutive lines stay on consecutive lines
//
// Our own nodes are written verbatim: their `raw` attribute is the source.
import { $remark, remarkStringifyOptionsCtx, remarkGFMPlugin, defaultHandlers } from '../vendor/milkdown-7.22.3/milkdown.mjs';
import { splitInline } from './syntax.mjs';

// A block's signature: what it is and how it starts. Remembers which
// neighbouring blocks had no blank line between them.
const textOf = (n) => (n.value != null ? n.value : n.raw != null ? n.raw : (n.children || []).map(textOf).join(''));
const sig = (n) => `${n.type}${n.depth || ''}:${textOf(n).slice(0, 80)}`;

function remarkLbNodes(tight) {
    return () => (tree) => {
        const walk = (node) => {
            if (!node.children) return;
            const next = [];
            for (const child of node.children) {
                if (child.type === 'text') {
                    const parts = splitInline(child.value);
                    if (parts) { next.push(...parts); continue; }
                }
                walk(child);
                next.push(child);
            }
            node.children = next;
            // After the split, so the signature sees citation/wikilink nodes
            // the way the serialiser will.
            if (node.type === 'root' || node.type === 'blockquote' || node.type === 'listItem') {
                for (let i = 0; i + 1 < next.length; i++) {
                    const a = next[i], b = next[i + 1];
                    if (a.position && b.position && b.position.start.line === a.position.end.line + 1) {
                        tight.add(`${sig(a)}→${b.type}`);
                    }
                }
            }
        };
        walk(tree);
    };
}

function stringifyOptions(ctx, tight) {
    ctx.update(remarkStringifyOptionsCtx, (o) => ({
        ...o,
        bullet: '-',
        emphasis: '*',
        strong: '*',
        rule: '-',
        join: [...(o.join || []), (left, right) => (tight.has(`${sig(left)}→${right.type}`) ? 0 : undefined)],
        handlers: {
            ...o.handlers,
            citation: (n) => `[${n.raw}]`,
            wikilink: (n) => `[[${n.raw}]]`,
            // "[" only needs its escape when it could open a link: a lone
            // "[lehm, putz]" stays a literal on reload, so write it as it was.
            text: (n, p, state, info) => {
                let out = o.handlers.text(n, p, state, info);
                const plain = out.replace(/\\\[/g, '[');
                if (!/\]\s*[(\[:]/.test(plain)) out = plain;
                // a lone "~" cannot pair into strikethrough
                if ((n.value.match(/~/g) || []).length === 1) out = out.replace('\\~', '~');
                return out;
            },
            // A bare URL (GFM autolink literal) is written bare, not as <url>.
            link: (n, p, state, info) => {
                const only = n.children.length === 1 && n.children[0].type === 'text' ? n.children[0].value : null;
                if (!n.title && only === n.url && /^https?:\/\/\S+$/.test(n.url)) return n.url;
                return defaultHandlers.link(n, p, state, info);
            },
        },
    }));
}

// One per editor: the parse records which blocks were tight, the serialiser
// honours it.
let seq = 0;
export function lbMarkdown() {
    const tight = new Set();
    const remark = $remark(`lbNodes${++seq}`, () => remarkLbNodes(tight));
    const config = (ctx) => {
        ctx.set(remarkGFMPlugin.options.key, { tablePipeAlign: false });
        stringifyOptions(ctx, tight);
    };
    return { remark, config };
}

// Delimiter rows ("| - | :- |") are written as "|---|:--|", or spaced as
// "| --- | :-- |" when the file used that style.
export function tidyTables(md, spaced = false) {
    const j = spaced ? ' | ' : '|';
    const edge = spaced ? ['| ', ' |'] : ['|', '|'];
    let fence = false;
    return md.split('\n').map((line) => {
        if (/^\s*(```|~~~)/.test(line)) fence = !fence;
        if (fence || !/^\|(\s*:?-+:?\s*\|)+\s*$/.test(line)) return line;
        const cells = line.trim().slice(1, -1).split('|').map((c) => c.trim());
        return edge[0] + cells.map((c) => (c.startsWith(':') ? ':' : '') + '---' + (c.length > 1 && c.endsWith(':') ? ':' : '')).join(j) + edge[1];
    }).join('\n');
}

// The host keeps the file's own edges; the editor owns what is between them.
export function withEdges(original, md) {
    const lead = (original.match(/^\s*\n/) || [''])[0];
    const trail = (original.match(/\n\s*$/) || [''])[0];
    const spaced = /^\|\s+:?-{3,}:?\s+\|/m.test(original);
    const core = tidyTables(md.replace(/^\s*\n/, '').replace(/\s+$/, ''), spaced);
    return core ? lead + core + trail : original.trim() ? '' : original;
}
