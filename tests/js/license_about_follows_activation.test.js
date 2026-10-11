// License & About (SPA-7): an activation through the trial gate reaches the
// About section, which loaded its status once and stays alive meanwhile.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootSettings, flush } from './helpers.js';

describe('About follows an activation made elsewhere', () => {
    it('shows "activated" after the license-activated event', async () => {
        let activated = false;
        const calls = await bootSettings([
            {
                method: 'GET', url: '/api/license/status',
                reply: () => (activated
                    ? { activated: true, key: '••••C570', checkout_url: '' }
                    : { activated: false, key: '', checkout_url: '' }),
            },
        ], 'about');
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="license-key-input"]')) throw new Error('not rendered');
        });
        expect(document.querySelector('[data-testid="license-activated"]')).toBeNull();
        // The section lives under <keep-alive>, so created() and activated()
        // both run on the first mount: one read, not two (the second could
        // overwrite the first). The other read is the App root's boot check.
        const statusReads = () => calls.filter((c) => c.method === 'GET' && c.url === '/api/license/status').length;
        expect(statusReads()).toBe(2);

        // The trial gate activates and announces it.
        activated = true;
        window.dispatchEvent(new CustomEvent('license-activated'));
        await flush();
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="license-activated"]')) throw new Error('still not activated');
        });
        expect(document.querySelector('[data-testid="license-key-input"]')).toBeNull();
    });
});
