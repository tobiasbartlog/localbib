// The guard behind ADR-0018, Decision 6.
//
// A missing key renders visibly as ⟦key.path⟧ rather than silently falling
// back to the other language — but that fallback is a safety net, not the
// defence. The defence is this test: English is the source, German the
// translation, and neither may drift from the other. A key added on one side
// and forgotten on the other fails here, not in front of a buyer.
import { describe, expect, it } from 'vitest';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import '../../static/locales/core.en.js';
import '../../static/locales/core.de.js';

const en = window.LB_I18N.en;
const de = window.LB_I18N.de;

// `{n}`, `{count}`, `{message}` … — a placeholder dropped in translation
// silently swallows the number or the error it was meant to carry.
function placeholders(text) {
    return new Set((text.match(/\{(\w+)\}/g) || []).sort());
}

describe('i18n catalogs', () => {
    it('cover exactly the same keys in both languages', () => {
        const onlyEn = Object.keys(en).filter((k) => !(k in de));
        const onlyDe = Object.keys(de).filter((k) => !(k in en));
        expect({ onlyEn, onlyDe }).toEqual({ onlyEn: [], onlyDe: [] });
    });

    it('hold a non-empty string for every key', () => {
        const empty = [];
        for (const [lang, catalog] of [['en', en], ['de', de]]) {
            for (const [key, value] of Object.entries(catalog)) {
                if (typeof value !== 'string' || !value.trim()) empty.push(`${lang}:${key}`);
            }
        }
        expect(empty).toEqual([]);
    });

    it('carry the same placeholders in both languages', () => {
        const mismatched = Object.keys(en).filter(
            (k) => k in de
                && [...placeholders(en[k])].join() !== [...placeholders(de[k])].join(),
        );
        expect(mismatched).toEqual([]);
    });

    // Plural keys come in pairs: `tn()` appends `.one` or `.other`, so a
    // lonely half means one of the two counts renders as its key path.
    it('keep both halves of every plural pair', () => {
        const lonely = Object.keys(en).filter((k) => {
            if (k.endsWith('.one')) return !(`${k.slice(0, -4)}.other` in en);
            if (k.endsWith('.other')) return !(`${k.slice(0, -6)}.one` in en);
            return false;
        });
        expect(lonely).toEqual([]);
    });

    // The legacy-supporter note moved into the catalogs when the backend
    // started sending a code instead of a sentence (ADR-0018). Its promise did
    // not move with it by itself: a blocked legacy install must be told why
    // and given the free way forward, in both languages. ADR-0015 forbids the
    // silent lockout — so the sentence is pinned here, where it now lives.
    it('keeps the legacy-supporter note pointing at the free way forward', () => {
        for (const catalog of [en, de]) {
            expect(catalog['license.legacyNote']).toContain('support@localbib.com');
        }
        expect(en['license.legacyNote']).toMatch(/free/i);
        expect(de['license.legacyNote']).toMatch(/kostenlos/i);
    });

    // The tray icon of the Windows .exe is drawn by Python, which reads its
    // `tray.*` texts straight from these files, line by line (ADR-0018
    // addendum, #214) - so they are covered by the checks above, and each
    // must sit on one `"key": "value",` line for that reader to see it.
    it('carry the tray texts of the .exe, one readable line each', () => {
        const tray = Object.keys(en).filter((k) => k.startsWith('tray.'));
        expect(tray.length).toBeGreaterThan(0);
        for (const name of ['core.en.js', 'core.de.js']) {
            const text = readFileSync(resolve(__dirname, '../../static/locales', name), 'utf8');
            const lines = new Set(
                text.split('\n')
                    .map((l) => l.match(/^\s*"(tray\.[^"]+)"\s*:\s*"(?:[^"\\]|\\.)*",?\s*$/))
                    .filter(Boolean)
                    .map((m) => m[1]),
            );
            expect(tray.filter((k) => !lines.has(k))).toEqual([]);
        }
    });

    // Catalogs are split per namespace: an Add-on Bundle brings its own locale
    // files (ADR-0021), so no core key may live in a Bundle's namespace. The
    // ids come from the Bundles in plugins/ — none in a tree without them.
    it('keeps Add-on namespaces out of the core catalogs', () => {
        const dir = resolve(__dirname, '../../plugins');
        const ids = existsSync(dir)
            ? readdirSync(dir).filter((id) => existsSync(resolve(dir, id, 'plugin.json')))
            : [];
        const inAddon = (k) => ids.some((id) => k.startsWith(id + '.'));
        expect(Object.keys(en).filter(inAddon)).toEqual([]);
        expect(Object.keys(de).filter(inAddon)).toEqual([]);
    });
});
