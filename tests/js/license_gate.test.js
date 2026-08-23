// Smoke test for the blocking activation dialog (#144, ADR-0015): an expired
// trial in the frozen build puts a gate in front of the app. A valid key no
// longer makes the dialog vanish silently (#156) — it shows a confirmation
// the customer dismisses themselves, and only then lands in the app.
import { describe, it, expect, vi } from 'vitest';
import { bootApp, typeInto, flush } from './helpers.js';

const BLOCKED_STATUS = {
    activated: false,
    key: '',
    activated_at: '',
    checkout_url: 'https://buy.example/localbib',
    price_display: '29,50 €',
    is_frozen: true,
    trial: { days_total: 14, days_remaining: 0, expired: true },
    blocked: true,
    legacy_supporter: false,
    legacy_note: '',
};

describe('Blockierender Aktivierungsdialog nach abgelaufener Testphase', () => {
    it('sperrt, nennt den Kaufweg und zeigt nach der Aktivierung eine Bestätigung', async () => {
        const calls = await bootApp([
            { method: 'GET', url: '/api/license/status', reply: BLOCKED_STATUS },
            { method: 'GET', url: '/api/banner/state', reply: { show: false, trial: BLOCKED_STATUS.trial, blocked: true } },
            {
                method: 'POST',
                url: '/api/license/activate',
                reply: { activated: true, key: '••••1234', blocked: false, trial: null },
            },
        ]);

        const gate = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="license-gate"]');
            if (!el) throw new Error('Aktivierungsdialog nicht gerendert');
            return el;
        });
        // Der Kaufweg steht im Dialog, sonst waere die Sperre eine Sackgasse.
        expect(
            document.querySelector('[data-testid="license-gate-buy"]').getAttribute('href'),
        ).toBe('https://buy.example/localbib');
        // Kein Kaufhinweis-Streifen daneben - eine Bitte reicht.
        expect(document.querySelector('[data-testid="banner-message"]')).toBeNull();
        expect(gate.textContent).toContain('Testphase beendet');
        // Der Preis kommt aus der API-Antwort, nicht aus einem Literal in der SPA.
        expect(gate.textContent).toContain('29,50 €');

        typeInto(document.querySelector('[data-testid="license-gate-input"]'), 'LB--ABCD-1234');
        await flush();
        document.querySelector('[data-testid="license-gate-activate"]').click();
        await flush();

        const activate = calls.find((c) => c.url === '/api/license/activate');
        expect(activate.body).toEqual({ key: 'LB--ABCD-1234' });

        // Der Dialog bleibt stehen, zeigt aber die Bestaetigung — nicht das
        // Eingabeformular, und er verschwindet nicht von selbst.
        const confirm = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="license-gate-confirm"]');
            if (!el) throw new Error('Bestätigung nicht gerendert');
            return el;
        });
        expect(confirm.textContent).toContain('Freigeschaltet');
        expect(confirm.textContent).toContain('dauerhaft und ohne weitere Prüfung');
        // Der maskierte Schlüssel kommt von der API — nichts in der SPA maskiert ihn erneut.
        expect(document.querySelector('[data-testid="license-gate-confirm-key"]').textContent).toBe('••••1234');
        expect(document.querySelector('[data-testid="license-gate-input"]')).toBeNull();
        // Die Antwort trug keine Restplaetze-Zahl -> die Zeile fehlt, statt geraten zu werden.
        expect(document.querySelector('[data-testid="license-gate-confirm-remaining"]')).toBeNull();

        document.querySelector('[data-testid="license-gate-confirm-dismiss"]').click();
        await flush();

        await vi.waitFor(() => {
            if (document.querySelector('[data-testid="license-gate"]')) {
                throw new Error('Dialog steht nach dem Wegklicken der Bestätigung noch');
            }
        });
    });
});
