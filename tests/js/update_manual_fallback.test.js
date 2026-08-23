// Kein Installer im Release (oder Quellinstallation): kein Ein-Klick-Knopf,
// aber immer ein anklickbarer Weg zur Release-Seite (#148).
import { describe, it, expect, vi } from 'vitest';
import { bootApp } from './helpers.js';

const RELEASE_PAGE = 'https://github.com/tobiasbartlog/localbib/releases/tag/v99.0.0';

describe('Update-Hinweis ohne Ein-Klick-Weg', () => {
    it('zeigt nur den manuellen Link auf die Release-Seite', async () => {
        await bootApp([
            {
                method: 'GET',
                url: '/api/version-check',
                reply: {
                    current: '0.3.0',
                    latest: '99.0.0',
                    update_available: true,
                    release_url: RELEASE_PAGE,
                    installer_url: '',
                    download_url: RELEASE_PAGE,
                    is_frozen: true,
                    can_auto_update: false,
                },
            },
        ]);

        const link = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="update-manual-link"]');
            if (!el) throw new Error('Manueller Link nicht gerendert');
            return el;
        });
        expect(link.getAttribute('href')).toBe(RELEASE_PAGE);
        expect(link.textContent).toContain('Release-Seite');
        expect(document.querySelector('[data-testid="update-now"]')).toBeNull();
    });
});
