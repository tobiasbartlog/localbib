// Settings -> Library (#211): a .env without LITERATUR_BASE_DIR shows the
// effective folder from /api/system/info, and "Open" asks for the named folder.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);

describe('settings: Library base folder', () => {
    it('shows the effective path and opens it by name', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/settings', reply: {} },
            { method: 'GET', url: '/api/system/info', reply: { version: '0.8.0', is_frozen: false, folders: { library: 'C:\\Users\\me\\Literatur', settings: 'x', logs: 'y' }, autostart_available: false } },
            { method: 'POST', url: '/api/system/open-folder', reply: { status: 'opened', folder: 'library' } },
        ], 'library');
        await vi.waitFor(() => {
            if (q('[data-testid="settings-base-dir"]').textContent !== 'C:\\Users\\me\\Literatur') throw new Error('no effective path');
        });
        q('[data-testid="settings-base-dir-open"]').click();
        await flush();
        expect(calls.filter((c) => c.url === '/api/system/open-folder').map((c) => c.body)).toEqual([{ folder: 'library' }]);
    });
});
