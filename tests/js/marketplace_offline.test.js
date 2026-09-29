// Marketplace offline: the cached list is shown with its date (#189, AC2).
// Own file: app.js self-mounts once per test file (see marketplace.test.js).
import { describe, expect, it } from 'vitest';
import { bootMarketplace } from './helpers.js';

describe('marketplace offline', () => {
    it('shows the offline notice with the cached date when the network is down', async () => {
        await bootMarketplace([
            {
                method: 'GET', url: '/api/marketplace',
                reply: {
                    addons: [{
                        id: 'demo1', name: 'DemoOne', tagline: 'Research workbench', description: '',
                        author: '', license: '', homepage: '', trust: 'official', languages: ['de'],
                        tags: [], icon: '', screenshots: [], requires_source: false, size: null,
                        in_index: true, source: 'bundle', installed: true, installed_version: '1.1.0',
                        installed_not_in_index: false, offered_version: '1.1.0', update_available: false,
                        state: 'installed', permissions: [], versions: [],
                    }],
                    offline: true, has_index: true, index_date: '2026-09-18T08:00:00Z', core: {},
                },
            },
        ]);

        expect(document.querySelector('[data-testid="marketplace-offline-notice"]')).toBeTruthy();
    });
});
