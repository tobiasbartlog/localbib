// A legacy Lemon Squeezy install (#144): the old Supporter Key counts as
// unactivated, but the Lizenz tab says so and names the free way forward —
// the one thing ADR-0015 rules out is a silent lockout.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings } from './helpers.js';

const NOTE =
    'Dieser Rechner trägt noch einen Supporter-Key aus dem alten Shop. ' +
    'Schreib kurz an support@localbib.com, du bekommst kostenlos einen Code.';

describe('Lizenz-Tab bei einem Altbestand aus dem früheren Shop', () => {
    it('zeigt den Migrationshinweis statt eines stillen "nicht aktiviert"', async () => {
        await bootSettings([
            {
                method: 'GET',
                url: '/api/license/status',
                reply: {
                    activated: false,
                    key: '',
                    checkout_url: 'https://buy.example/localbib',
                    is_frozen: true,
                    trial: { days_total: 14, days_remaining: 6, expired: false },
                    blocked: false,
                    legacy_supporter: true,
                    legacy_note: NOTE,
                },
            },
        ], 'license-tab');

        const note = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="license-legacy-note"]');
            if (!el) throw new Error('Migrationshinweis nicht gerendert');
            return el;
        });
        expect(note.textContent).toContain('support@localbib.com');
        // Die Testphase laeuft noch — der Hinweis ist eine Erklaerung, keine Sperre.
        expect(document.querySelector('[data-testid="license-trial"]').textContent)
            .toContain('noch 6 Tage');
        expect(document.querySelector('[data-testid="license-key-input"]')).not.toBeNull();
    });
});
