// Smoke (#178): Skip on page one still reaches the migration offer, and
// "Not now" is a real way out — it closes the dialog without routing anywhere.
// Own file — app.js self-mounts once per module graph (see helpers.js), and
// this run needs the skip path rather than the save path.
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('migration offer after skipping onboarding', () => {
    it('shows the offer after Skip and closes on "Not now"', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: {} },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush(); await flush(); await flush();

        document.querySelector('[data-testid="onboarding-skip"]').click();
        await flush(); await flush();

        // Skipping the configuration does not skip the offer.
        expect(calls.find((c) => c.method === 'PUT' && c.url === '/api/settings').body.ONBOARDING_COMPLETED).toBe('true');
        expect(document.querySelector('[data-testid="onboarding-migrate-zotero_rdf"]')).toBeTruthy();

        const hashBefore = window.location.hash;
        document.querySelector('[data-testid="onboarding-migrate-later"]').click();
        await flush(); await flush();

        expect(document.querySelector('[data-testid="onboarding-dialog"]')).toBeFalsy();
        // "Not now" goes nowhere — the user stays where they were.
        expect(window.location.hash).toBe(hashBefore);
    });
});
