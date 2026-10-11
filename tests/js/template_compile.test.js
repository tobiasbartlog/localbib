// Every component template must compile under the PRODUCTION Vue build.
//
// templates/index.html loads vue.global.prod.js, whose runtime compiler has no
// error hook: a malformed template (say, a missing `</div>`) throws and the
// page never mounts. The vitest smokes run the dev build, where the same defect
// is only a `[Vue warn]` and the page still renders — so a broken template
// passed every smoke while the real analysis page failed to mount. This test
// compiles each `template: \`...\`` literal with the prod build's compiler and
// fails on any compile error, naming the file and line.
import { describe, expect, it } from 'vitest';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const { compile } = require('vue/dist/vue.cjs.prod.js');

const ROOT = resolve(__dirname, '../..');

// The core script plus any Bundle frontend scripts present in this tree.
function scripts() {
    const files = [join(ROOT, 'static', 'app.js')];
    const pluginsDir = join(ROOT, 'plugins');
    if (existsSync(pluginsDir)) {
        for (const id of readdirSync(pluginsDir)) {
            const fe = join(pluginsDir, id, 'frontend');
            if (!existsSync(fe)) continue;
            for (const f of readdirSync(fe)) {
                if (f.endsWith('.js')) files.push(join(fe, f));
            }
        }
    }
    return files;
}

// `template: \`...\`` literals; they hold no unescaped backticks.
function templates(src) {
    const out = [];
    const re = /template:\s*`/g;
    let m;
    while ((m = re.exec(src))) {
        const start = m.index + m[0].length;
        let end = start;
        for (;;) {
            end = src.indexOf('`', end);
            if (end === -1 || src[end - 1] !== '\\') break;
            end += 1;
        }
        if (end === -1) throw new Error(`unterminated template literal at offset ${start}`);
        out.push({ start, body: src.slice(start, end) });
        re.lastIndex = end + 1;
    }
    return out;
}

function lineOf(src, offset) {
    return src.slice(0, offset).split('\n').length;
}

describe('component templates compile with the production Vue build', () => {
    for (const file of scripts()) {
        const rel = file.slice(ROOT.length + 1).replace(/\\/g, '/');
        it(rel, () => {
            const src = readFileSync(file, 'utf8');
            const found = templates(src);
            expect(found.length).toBeGreaterThan(0);
            const problems = [];
            for (const { start, body } of found) {
                const errors = [];
                try {
                    compile(body, { onError: (e) => errors.push(e) });
                } catch (e) {
                    errors.push(e);
                }
                const base = lineOf(src, start);
                for (const e of errors) {
                    const line = e.loc ? base + e.loc.start.line - 1 : base;
                    problems.push(`${rel}:${line} (template at line ${base}): ${e.message}`);
                }
            }
            expect(problems).toEqual([]);
        });
    }
});
