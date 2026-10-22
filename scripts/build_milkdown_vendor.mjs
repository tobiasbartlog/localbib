// Builds the vendored Milkdown bundle (ADR-0026): one ESM file plus one
// stylesheet under static/vendor/milkdown-<version>/, committed with the
// licenses of every package that went into it. The app itself stays
// buildless; this script runs only when the pinned version changes.
//
//   node scripts/build_milkdown_vendor.mjs
//
// The bundle re-exports every Milkdown/ProseMirror API static/markdown/*.mjs
// needs, from this one instance: a second copy of ProseMirror in the page
// would break `instanceof` checks between plugins.
//
// Only the Crepe features the notes use are bundled (PLAN "Bündelgröße"):
// the Crepe class imports all of them statically, CodeMirror 6, KaTeX and
// fonts included - 2.7 MB of JS. The builder takes them one by one.
import { build } from 'esbuild';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pkg = (name) => JSON.parse(readFileSync(join(ROOT, 'node_modules', name, 'package.json'), 'utf8'));
const VERSION = pkg('@milkdown/crepe').version;
const OUT = join(ROOT, 'static', 'vendor', `milkdown-${VERSION}`);

const ENTRY = `
export { CrepeBuilder } from '@milkdown/crepe/builder';
export { listItem } from '@milkdown/crepe/feature/list-item';
export { linkTooltip } from '@milkdown/crepe/feature/link-tooltip';
export { cursor } from '@milkdown/crepe/feature/cursor';
export { blockEdit } from '@milkdown/crepe/feature/block-edit';
export { toolbar } from '@milkdown/crepe/feature/toolbar';
export { placeholder } from '@milkdown/crepe/feature/placeholder';
export { table } from '@milkdown/crepe/feature/table';
export { commandsCtx, editorViewCtx, remarkStringifyOptionsCtx, serializerCtx, parserCtx } from '@milkdown/kit/core';
export { $nodeSchema, $remark, $prose, $view, getMarkdown, replaceAll } from '@milkdown/kit/utils';
export { Plugin, PluginKey, TextSelection, NodeSelection } from '@milkdown/kit/prose/state';
export { remarkGFMPlugin } from '@milkdown/kit/preset/gfm';
export { clearTextInCurrentBlockCommand } from '@milkdown/kit/preset/commonmark';
export { defaultHandlers } from 'mdast-util-to-markdown';
export const MILKDOWN_VERSION = ${JSON.stringify(VERSION)};
`;

const CSS = ['prosemirror', 'reset', 'block-edit', 'cursor', 'link-tooltip', 'list-item', 'placeholder', 'toolbar', 'table']
    .map((name) => `@import '@milkdown/crepe/theme/common/${name}.css';`).join('\n');

const common = {
    bundle: true,
    minify: true,
    legalComments: 'none',
    logLevel: 'warning',
    absWorkingDir: ROOT,
    metafile: true,
};

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

const js = await build({
    ...common,
    stdin: { contents: ENTRY, resolveDir: ROOT, sourcefile: 'milkdown-entry.mjs', loader: 'js' },
    format: 'esm',
    target: 'es2020',
    platform: 'browser',
    outfile: join(OUT, 'milkdown.mjs'),
    define: {
        'process.env.NODE_ENV': '"production"',
        __VUE_OPTIONS_API__: 'true',
        __VUE_PROD_DEVTOOLS__: 'false',
        __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false',
    },
});
const css = await build({
    ...common,
    stdin: { contents: CSS, resolveDir: ROOT, sourcefile: 'milkdown-entry.css', loader: 'css' },
    outfile: join(OUT, 'crepe.css'),
    loader: { '.svg': 'dataurl', '.woff': 'dataurl', '.woff2': 'dataurl', '.ttf': 'dataurl' },
});

// Every package an input file came from, with its license text.
function packageOf(file) {
    const parts = file.split('/');
    const at = parts.lastIndexOf('node_modules');
    if (at < 0) return null;
    return parts[at + 1].startsWith('@') ? `${parts[at + 1]}/${parts[at + 2]}` : parts[at + 1];
}
const packages = new Set();
for (const meta of [js.metafile, css.metafile]) {
    for (const file of Object.keys(meta.inputs)) {
        const name = packageOf(file);
        if (name) packages.add(name);
    }
}
const sections = [];
for (const name of [...packages].sort()) {
    const dir = join(ROOT, 'node_modules', name);
    const info = pkg(name);
    const file = readdirSync(dir).find((f) => /^(licen[cs]e|copying)/i.test(f));
    const text = file ? readFileSync(join(dir, file), 'utf8').trim() : `License: ${info.license || 'unknown'} (no license file shipped)`;
    sections.push(`${name}@${info.version} (${info.license || 'unknown'})\n${'-'.repeat(60)}\n${text}\n`);
}
writeFileSync(join(OUT, 'LICENSE'), [
    `Third-party licenses of milkdown.mjs and crepe.css (Milkdown ${VERSION}).`,
    'Built by scripts/build_milkdown_vendor.mjs from the exactly pinned devDependencies in package.json.',
    '',
    ...sections,
].join('\n\n'));

const size = (f) => `${(readFileSync(join(OUT, f)).length / 1024).toFixed(0)} KB`;
for (const f of ['milkdown.mjs', 'crepe.css']) console.log(`${relative(ROOT, join(OUT, f))}: ${size(f)}`);
console.log(`${packages.size} packages, licenses in ${relative(ROOT, join(OUT, 'LICENSE'))}`);
if (!existsSync(join(OUT, 'milkdown.mjs'))) process.exit(1);
