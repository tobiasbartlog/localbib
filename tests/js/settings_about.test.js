// Settings -> License & About (#211): version with the update check (a hit
// offers the existing one-click update), the three folders with copy and open,
// the AGPL note and "Restart setup" reopening the first-run dialog.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => (q(sel) ? q(sel).textContent.trim() : null);

const FOLDERS = { library: 'D:\\Lib', settings: 'C:\\Users\\me\\Literatur', logs: 'C:\\Users\\me\\Literatur\\logs' };

describe('settings: License & About', () => {
    it('shows paths, copies and opens them, checks for updates and restarts the setup', async () => {
        const written = [];
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText: vi.fn(async (v) => { written.push(v); }) },
        });
        let versionCalls = 0;
        let refusedFolder = false;
        const calls = await bootSettings([
            { method: 'GET', url: '/api/system/info', reply: { version: '0.8.0', is_frozen: true, folders: FOLDERS, autostart_available: false } },
            { method: 'GET', url: '/api/license/status', reply: { activated: false, trial: { expired: false, days_remaining: 9 } } },
            {
                method: 'GET', url: '/api/version-check',
                reply: () => {
                    versionCalls += 1;
                    return versionCalls < 3
                        ? { latest: '0.8.0', update_available: false }
                        : { latest: '0.8.1', update_available: true, can_auto_update: true, release_url: 'https://github.com/tobiasbartlog/localbib/releases/tag/v0.8.1' };
                },
            },
            { method: 'POST', url: '/api/update/install', reply: { status: 'installing', message: 'Installing now.' } },
            {
                method: 'POST', url: '/api/system/open-folder',
                reply: ({ body }) => {
                    refusedFolder = body.folder === 'logs';
                    return refusedFolder ? { detail: 'error.folder_missing' } : { status: 'opened', folder: body.folder };
                },
                get status() { return refusedFolder ? 404 : 200; },
            },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/llm/providers', reply: { providers: [{ id: 'openrouter', label: 'OpenRouter' }] } },
        ], 'about');

        // License/trial, version, three folders, AGPL note, restart-setup button.
        expect(q('[data-testid="license-trial"]')).toBeTruthy();
        await vi.waitFor(() => { if (!text('[data-testid="about-version-label"]').includes('0.8.0')) throw new Error('no version'); });
        for (const name of Object.keys(FOLDERS)) expect(text(`[data-testid="about-folder-${name}"]`)).toBe(FOLDERS[name]);
        expect(q('[data-testid="about-source-link"]').getAttribute('href')).toBe('https://github.com/tobiasbartlog/localbib');
        expect(q('[data-testid="about-source"]').textContent).toContain('AGPL-3.0');
        expect(q('[data-testid="about-restart-setup"]')).toBeTruthy();

        // --- copy: accessible icon button, short confirmation -----------------
        const copy = q('[data-testid="about-copy-settings"]');
        expect(copy.getAttribute('aria-label')).toContain('Settings folder');
        copy.click();
        await vi.waitFor(() => { if (!q('[data-testid="about-copied-settings"]')) throw new Error('no confirmation'); });
        expect(written).toEqual([FOLDERS.settings]);

        // --- open: only the name goes out; a refusal shows its message ----------
        q('[data-testid="about-open-library"]').click();
        await flush();
        const open = calls.filter((c) => c.url === '/api/system/open-folder');
        expect(open.map((c) => c.body)).toEqual([{ folder: 'library' }]);
        expect(q('[data-testid="about-folder-error"]')).toBeNull();
        q('[data-testid="about-open-logs"]').click();
        await vi.waitFor(() => { if (!q('[data-testid="about-folder-error"]')) throw new Error('no error shown'); });
        expect(text('[data-testid="about-folder-error"]')).toBe('This folder does not exist (yet).');

        // --- update check: nothing, then a hit with the existing one-click flow --
        expect(versionCalls).toBeGreaterThanOrEqual(1);
        expect(q('[data-testid="about-update-found"]')).toBeNull();
        q('[data-testid="about-check-update"]').click();
        await vi.waitFor(() => { if (!text('[data-testid="about-update-status"]').includes('up to date')) throw new Error('not checked'); });
        q('[data-testid="about-check-update"]').click();
        await vi.waitFor(() => { if (!q('[data-testid="about-update-found"]')) throw new Error('no update shown'); });
        expect(text('[data-testid="about-update-found"]')).toContain('Version 0.8.1 is available');
        expect(q('[data-testid="about-update-notes"]').getAttribute('href')).toContain('v0.8.1');
        q('[data-testid="about-update-now"]').click();
        await vi.waitFor(() => { if (!text('[data-testid="about-update-message"]')) throw new Error('no message'); });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/update/install')).toBe(true);

        // --- restart setup: key reset, dialog open now -------------------------
        expect(q('[data-testid="onboarding-dialog"]')).toBeNull();
        q('[data-testid="about-restart-setup"]').click();
        await vi.waitFor(() => { if (!q('[data-testid="onboarding-dialog"]')) throw new Error('dialog not open'); });
        const puts = calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings');
        expect(puts[puts.length - 1].body).toEqual({ ONBOARDING_COMPLETED: 'false' });
    });
});
