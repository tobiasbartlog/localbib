// Dark-Mode-Deckung der Tailwind-Klassen.
//
// Die SPA mischt zwei Farbquellen: die LocalBib-Tokens (`--lb-*`, per
// `data-theme` umschaltbar) und Tailwinds *statische* Palette (`bg-white`,
// `text-gray-500`, …), die von `data-theme` nichts weiss. Genau daher kamen
// die weissen Fenster in den Einstellungen.
//
// Dieser Test haelt beide Haelften der Reparatur fest:
//   1. die Tailwind-Config bindet die LocalBib-Namen an `var(--lb-*)`,
//      statt Hexwerte einzufrieren, und
//   2. jede benutzte Klasse aus Tailwinds Standardpalette hat im Stylesheet
//      eine Dark-Mode-Entsprechung — oder steht bewusst auf der Ausnahmeliste.
//
// Neues Markup soll bevorzugt die Token-Utilities verwenden
// (`bg-bg-elev`, `text-ink-3`, `border-hairline`); dann ist hier nichts
// nachzutragen. Wer doch einen Grauwert braucht, wird hier daran erinnert,
// ihn zu ueberbruecken.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
const APP = read('../../static/app.js');
const CSS = read('../../static/style.css');
const INDEX = read('../../templates/index.html');

// Farben, die in beiden Themes stehen bleiben duerfen:
//   * Weisse Schrift und schwarze Overlays sitzen nie auf dem Karton.
//   * Die 400er-Toene sind Statuspunkte, keine Flaechen — sie tragen in
//     beiden Themes.
const ALLOWED_WITHOUT_DARK_RULE = new Set([
    'text-white', 'hover:text-white',
    'bg-black',
    'bg-gray-400', 'bg-red-400', 'bg-amber-400', 'bg-emerald-400', 'bg-blue-400',
]);

const UTILITY = /(?:(hover|focus|group-hover|disabled|focus-within|active):)?(bg|text|border|divide)-(white|black|gray|slate|zinc|neutral|stone|red|green|blue|amber|yellow|orange|indigo|purple|pink|emerald|teal|cyan|sky|violet|fuchsia|rose|lime)(?:-(\d{2,3}))?(?![\w-])/g;

function usedUtilities() {
    const found = new Set();
    for (const m of APP.matchAll(UTILITY)) {
        const [, variant, prop, hue, shade] = m;
        found.add(`${variant ? variant + ':' : ''}${prop}-${hue}${shade ? '-' + shade : ''}`);
    }
    return [...found].sort();
}

// Im Stylesheet steht der Variantenpraefix escaped (`.hover\:bg-gray-50:hover`).
// Eine Klasse kann mehrfach vorkommen (ein Plugin-Scope bringt eigene
// Regeln mit); gesucht ist die Fundstelle, deren Selektorzeile unter
// `[data-theme="dark"]` haengt — eine Light-Mode-Definition derselben Klasse
// waere gerade keine Abdeckung.
function hasDarkRule(cls) {
    const [variant, base] = cls.includes(':') ? cls.split(':') : [null, cls];
    const selector = variant ? `.${variant}\\:${base}` : `.${base}`;
    for (let idx = CSS.indexOf(selector); idx !== -1; idx = CSS.indexOf(selector, idx + 1)) {
        const after = CSS[idx + selector.length];
        if (/[\w-]/.test(after)) continue;   // bg-gray-5 darf nicht bg-gray-50 treffen
        const lineStart = CSS.lastIndexOf('\n', idx) + 1;
        if (CSS.slice(lineStart, idx).includes('[data-theme="dark"]')) return true;
    }
    return false;
}

describe('Dark Mode: Tailwind-Palette', () => {
    it('bindet die LocalBib-Farbnamen an die CSS-Tokens statt an Hexwerte', () => {
        const config = INDEX.slice(INDEX.indexOf('colors: {'), INDEX.indexOf('boxShadow'));
        expect(config).toContain("var(--lb-bg-elev)");
        expect(config).toContain("var(--lb-ink)");
        expect(config).toContain("var(--lb-accent-ink)");
        expect(config).not.toMatch(/#[0-9a-fA-F]{6}/);
    });

    it('laesst keine Standardpalette-Klasse ohne Dark-Mode-Entsprechung', () => {
        const missing = usedUtilities()
            .filter((cls) => !ALLOWED_WITHOUT_DARK_RULE.has(cls))
            .filter((cls) => !hasDarkRule(cls));
        expect(missing).toEqual([]);
    });

    it('faerbt Fehlertext ueber ein Token, nicht ueber ein festes Rot', () => {
        expect(CSS).toMatch(/--lb-danger:\s*#dc2626/);
        expect(CSS).toMatch(/\[data-theme="dark"\][\s\S]{0,600}--lb-danger:/);
        expect(APP).not.toContain('#dc2626');
    });

    it('zeichnet das Wissensnetz aus Tokens statt aus festen Hexwerten', () => {
        // Der Trennring der Fremdknoten ist die Seitenfarbe; fest verdrahtet
        // war er ein weisser Leuchtring im Dark Mode.
        expect(APP).toContain("lbToken('--lb-bg', '#fafaf7')");
        const literals = [...APP.matchAll(/['"]#[0-9a-fA-F]{3,8}['"]/g)]
            .filter((m) => {
                const line = APP.slice(APP.lastIndexOf('\n', m.index) + 1, APP.indexOf('\n', m.index));
                return !/lbToken|cssVar/.test(line);
            })
            .map((m) => m[0])
            // Weiss als *Schrift auf einer Serienfarbe* ist kein Theme-Wert:
            // dort liegt die Flaeche schon fest, egal welches Theme laeuft.
            .filter((hex) => hex !== "'#ffffff'");
        expect(literals).toEqual([]);
    });
});
