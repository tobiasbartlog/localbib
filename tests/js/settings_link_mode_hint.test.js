// Library → Advanced → Link mode (#213): the field shows the mode in effect
// (the server sends it even when the .env holds none), and a saved change
// offers "Rebuild category folders?" with "Later" and "Rebuild now". The
// rebuild is the recompute of Refresh block ② with its category folders step;
// afterwards a short success line replaces the hint.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, goTo } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const status = (key) => {
    const el = q(`[data-field-status="${key}"]`);
    return el ? el.getAttribute('data-status') : null;
};

describe('settings: link mode context hint', () => {
    it('offers the rebuild after a change and reports it', async () => {
        const calls = await bootSettings([
            // Nothing in the .env: the server answers with the mode in effect.
            { method: 'GET', url: '/api/settings', reply: { LINK_MODE: 'hardlink' } },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            {
                method: 'POST',
                url: '/api/maintenance/recompute',
                stream: [
                    { type: 'progress', step: 'category_folders', index: 1, steps: ['category_folders'],
                      current: 200, total: 412, percent: 48, stats: {} },
                    { type: 'done', steps: ['category_folders'], errors: 2, total: 412,
                      stats: { category_folders: { updated: 410, errors: 2, fallbacks: 0 } } },
                ],
            },
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: {} },
        ], 'library');
        const runs = () => calls.filter((c) => c.url === '/api/maintenance/recompute');

        q('[data-testid="settings-advanced-toggle"]').click();
        await flush();
        expect(q('[data-testid="settings-link-hardlink"]').getAttribute('aria-checked')).toBe('true');
        expect(q('[data-testid="link-mode-hint"]')).toBeNull();

        // A saved change offers the rebuild; "Later" puts it away.
        q('[data-testid="settings-link-copy"]').click();
        await vi.waitFor(() => {
            if (status('LINK_MODE') !== 'saved') throw new Error('not saved');
        });
        const hint = q('[data-testid="link-mode-hint"]');
        expect(hint.textContent).toContain('Rebuild category folders?');
        expect(hint.textContent).toContain('your items stay unchanged');
        q('[data-testid="link-mode-later"]').click();
        await flush();
        expect(q('[data-testid="link-mode-hint"]')).toBeNull();
        expect(runs()).toEqual([]);

        // The next change offers it again; "Rebuild now" runs the recompute.
        q('[data-testid="settings-link-symlink"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="link-mode-hint"]')) throw new Error('hint not offered again');
        });
        q('[data-testid="link-mode-rebuild"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="link-mode-done"]')) throw new Error('no success line');
        });
        expect(runs().length).toBe(1);
        expect(runs()[0].method).toBe('POST');
        expect(runs()[0].body).toEqual({ steps: ['category_folders'] });
        expect(q('[data-testid="link-mode-done"]').textContent).toContain('Category folders rebuilt – 410 items, 2 errors');
        expect(q('[data-testid="link-mode-rebuild"]')).toBeNull();

        // Refresh block ② shows its own runs' results, not this one's.
        goTo('#/settings/refresh');
        await vi.waitFor(() => {
            if (!q('[data-testid="recompute"]')) throw new Error('refresh not open');
        });
        expect(q('[data-testid="recompute"]').getAttribute('data-state')).toBe('ready');
        expect(document.body.textContent).not.toContain('⟦');
    });
});
