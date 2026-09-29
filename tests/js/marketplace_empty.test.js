// Marketplace with no cache and no network: a clear empty message, not a
// dead page (#189, AC2). Own file: app.js self-mounts once per test file.
import { describe, expect, it } from 'vitest';
import { bootMarketplace } from './helpers.js';

describe('marketplace without a cached index', () => {
    it('shows a clear empty state', async () => {
        await bootMarketplace([
            {
                method: 'GET', url: '/api/marketplace',
                reply: { addons: [], offline: true, has_index: false, index_date: null, core: {} },
            },
        ]);

        expect(document.querySelector('[data-testid="marketplace-no-index"]')).toBeTruthy();
    });
});
