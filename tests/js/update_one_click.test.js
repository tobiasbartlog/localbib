// Ein-Klick-Update (#148): sagt der Server, dass diese Installation sich selbst
// aktualisieren darf, zeigt der Hinweisstreifen "Jetzt aktualisieren" — und ein
// Klick stoesst genau einen POST an, mehr nicht.
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush } from './helpers.js';

const INSTALLER = 'https://github.com/tobiasbartlog/localbib/releases/download/v99.0.0/LocalBib-Setup-99.0.0.exe';

describe('Update-Hinweis mit Ein-Klick-Weg', () => {
    it('bietet "Jetzt aktualisieren" an und meldet den Start der Installation', async () => {
        const calls = await bootApp([
            {
                method: 'GET',
                url: '/api/version-check',
                reply: {
                    current: '0.3.0',
                    latest: '99.0.0',
                    update_available: true,
                    release_url: 'https://github.com/tobiasbartlog/localbib/releases/tag/v99.0.0',
                    installer_url: INSTALLER,
                    download_url: INSTALLER,
                    is_frozen: true,
                    can_auto_update: true,
                },
            },
            {
                method: 'POST',
                url: '/api/update/install',
                reply: { status: 'installing', version: '99.0.0', message: 'Update wird installiert — LocalBib startet gleich neu.' },
            },
        ]);

        const button = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="update-now"]');
            if (!el) throw new Error('Update-Knopf nicht gerendert');
            return el;
        });
        expect(button.textContent).toContain('Jetzt aktualisieren');
        // Der manuelle Weg bleibt daneben stehen, auch wenn alles gut geht.
        expect(document.querySelector('[data-testid="update-manual-link"]').getAttribute('href'))
            .toBe(INSTALLER);

        button.click();
        await flush();
        await flush();

        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/update/install')).toBe(true);
        const message = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="update-message"]');
            if (!el || !el.textContent.includes('installiert')) throw new Error('Keine Rueckmeldung');
            return el;
        });
        expect(message.textContent).toContain('LocalBib startet gleich neu');
    });
});
