// Settings -> Library (#208): switches and choices save at once, number fields
// on blur or Enter with the server's reason on a refusal (and the display back
// at the saved value), "Advanced" starts closed and remembers its state in the
// browser, and the base folder only changes through the confirmation dialog,
// which restarts the app.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const puts = (calls) => calls.filter((c) => c.method === 'PUT' && c.url === '/api/settings');
const status = (key) => {
    const el = q(`[data-field-status="${key}"]`);
    return el ? el.getAttribute('data-status') : null;
};

describe('settings: Library autosave', () => {
    it('autosaves per field, keeps Advanced closed and changes the base folder only via the dialog', async () => {
        let refused = false;
        const calls = await bootSettings([
            {
                method: 'GET', url: '/api/settings',
                reply: { LITERATUR_BASE_DIR: 'D:\\Lib', WATCH_INTERVAL: '5', LINK_MODE: 'copy' },
            },
            {
                method: 'PUT', url: '/api/settings',
                // The mock reads `status` after `reply`, so the reply decides it.
                reply: ({ body }) => {
                    refused = body.WATCH_INTERVAL === 'abc';
                    return refused
                        ? { detail: { code: 'error.settings.invalidNumber', params: { key: 'WATCH_INTERVAL' } } }
                        : { status: 'ok' };
                },
                get status() { return refused ? 422 : 200; },
            },
            { method: 'POST', url: '/api/app/restart', reply: { status: 'restarting' } },
        ], 'library');

        // Basisordner + PDF-Schutz on top; Erweitert closed, no .env wording.
        expect(q('[data-testid="settings-base-dir"]').textContent).toBe('D:\\Lib');
        expect(q('[data-testid="settings-unlock-pdfs"]')).toBeTruthy();
        expect(q('[data-testid="settings-advanced"]')).toBeNull();
        expect(q('[data-testid="settings-advanced-toggle"]').getAttribute('aria-expanded')).toBe('false');
        expect(document.body.textContent).not.toContain('.env');

        // --- the PDF-protection switch saves by itself ------------------------
        q('[data-testid="settings-unlock-pdfs"]').click();
        await vi.waitFor(() => {
            if (status('UNLOCK_PDFS') !== 'saved') throw new Error('not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ UNLOCK_PDFS: 'false' });
        expect(q('[data-field-status="UNLOCK_PDFS"]').textContent).toContain('Saved ✓');

        // --- Erweitert opens, is remembered, and holds the three settings -----
        q('[data-testid="settings-advanced-toggle"]').click();
        await flush();
        expect(q('[data-testid="settings-advanced"]')).toBeTruthy();
        expect(localStorage.getItem('lbSettingsLibraryAdvanced')).toBe('1');

        q('[data-testid="settings-link-hardlink"]').click();
        await vi.waitFor(() => {
            if (status('LINK_MODE') !== 'saved') throw new Error('link mode not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ LINK_MODE: 'hardlink' });

        const interval = q('#env-WATCH_INTERVAL');
        typeInto(interval, '12');
        interval.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (status('WATCH_INTERVAL') !== 'saved') throw new Error('interval not saved');
        });
        expect(puts(calls).at(-1).body).toEqual({ WATCH_INTERVAL: '12' });

        // Enter saves too, and an untouched field sends nothing.
        const sent = puts(calls).length;
        interval.dispatchEvent(new Event('blur'));
        await flush();
        expect(puts(calls).length).toBe(sent);

        const ocr = q('#env-MAX_OCR_PAGES');
        typeInto(ocr, '40');
        ocr.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        await vi.waitFor(() => {
            if (puts(calls).at(-1).body.MAX_OCR_PAGES !== '40') throw new Error('Enter did not save');
        });

        // --- a refused number: the reason at the field, the saved value back --
        typeInto(interval, 'abc');
        interval.dispatchEvent(new Event('blur'));
        await vi.waitFor(() => {
            if (status('WATCH_INTERVAL') !== 'error') throw new Error('no error shown');
        });
        expect(q('[data-field-status="WATCH_INTERVAL"]').textContent).toContain('whole number');
        expect(interval.value).toBe('12');

        // --- the base folder: only through the dialog -------------------------
        expect(puts(calls).some((c) => 'LITERATUR_BASE_DIR' in c.body)).toBe(false);
        q('[data-testid="settings-base-dir-change"]').click();
        await flush();
        expect(q('[data-testid="settings-base-dir-dialog"]').textContent).toContain('Change base folder?');
        q('[data-testid="settings-base-dir-cancel"]').click();
        await flush();
        expect(q('[data-testid="settings-base-dir-dialog"]')).toBeNull();
        expect(puts(calls).some((c) => 'LITERATUR_BASE_DIR' in c.body)).toBe(false);

        q('[data-testid="settings-base-dir-change"]').click();
        await flush();
        typeInto(q('[data-testid="settings-base-dir-input"]'), 'E:\\NewLib');
        await flush();
        q('[data-testid="settings-base-dir-confirm"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="settings-base-dir-restarting"]')) throw new Error('not restarting');
        });
        expect(puts(calls).at(-1).body).toEqual({ LITERATUR_BASE_DIR: 'E:\\NewLib' });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/app/restart')).toBe(true);
        expect(document.body.textContent).not.toContain('⟦');
    });
});
