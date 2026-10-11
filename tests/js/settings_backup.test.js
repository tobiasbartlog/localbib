// Pflege → Backup: a folder first, then a backup now — and the question whether
// the PDFs go along is asked before every single run, never assumed. The
// automatic schedule needs the same answer once, explicitly, before an interval
// can be chosen; the section says that LocalBib never deletes a backup.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { bootSettings, flush, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => (q(sel) ? q(sel).textContent.trim() : null);

function initialState() {
    return {
        folder: '', auto: 'off', auto_effective: 'off', auto_pdfs: null,
        folder_state: { exists: false, free_bytes: null, same_drive: false },
        estimate: { data_bytes: 2 * 1024 * 1024, pdf_bytes: 300 * 1024 * 1024, pdf_count: 12 },
        backups: [], count: 0, total_bytes: 0, partial: [],
        run: null, last: null, auto_problem: null, next_auto_at: null,
    };
}

describe('settings: Backup', () => {
    it('asks before every run whether the PDFs go along, and the schedule needs that answer too', async () => {
        let state = initialState();
        let polls = 0;
        const calls = await bootSettings([
            {
                method: 'GET', url: '/api/backup',
                reply: () => {
                    // The run started below finishes on the first look after it.
                    if (state.run && ++polls >= 1) {
                        const entry = { name: 'LocalBib-Backup_2026-10-09_14-30-12_data-only.zip', bytes: 1536 * 1024,
                                        created_at: '2026-10-09T14:30:12', include_pdfs: false };
                        state = { ...state, run: null, backups: [entry], count: 1, total_bytes: entry.bytes,
                                  last: { ok: true, kind: 'manual', include_pdfs: false, bytes: entry.bytes, skipped: 0,
                                          name: entry.name, finished_at: '2026-10-09T14:30:14' } };
                    }
                    return state;
                },
            },
            {
                method: 'PUT', url: '/api/backup/settings',
                reply: ({ body }) => {
                    if (body.folder !== undefined) {
                        state = { ...state, folder: body.folder, folder_state: { exists: true, free_bytes: 1e12, same_drive: false } };
                    }
                    if (body.auto_pdfs !== undefined) state = { ...state, auto_pdfs: body.auto_pdfs };
                    if (body.auto !== undefined) {
                        state = { ...state, auto: body.auto, auto_effective: body.auto, next_auto_at: '2099-01-01T00:00:00' };
                    }
                    return state;
                },
            },
            {
                method: 'POST', url: '/api/backup/run',
                reply: ({ body }) => {
                    state = { ...state, run: { kind: 'manual', include_pdfs: body.include_pdfs, phase: 'pdfs', done: 3, total: 12, percent: 25 } };
                    return state;
                },
                status: 202,
            },
        ], 'backup');

        // --- nothing set up: no folder, no run, no schedule ----------------------
        expect(text('[data-testid="settings-head-title"]')).toBe('Backup');
        expect(text('[data-testid="settings-head-group"]')).toBe('Maintenance');
        expect(q('[data-testid="backup-folder-none"]')).toBeTruthy();
        expect(q('[data-testid="backup-start"]').disabled).toBe(true);
        expect(q('[data-testid="backup-interval-daily"]').disabled).toBe(true);
        expect(text('[data-testid="backup-auto-state"]')).toBe('Choose a backup folder first.');

        // --- choose the folder ------------------------------------------------------
        q('[data-testid="backup-folder-change"]').click();
        await flush();
        typeInto(q('[data-testid="backup-folder-input"]'), 'E:\\LocalBib-Backups');
        await flush();
        q('[data-testid="backup-folder-save"]').click();
        await vi.waitFor(() => {
            if (text('[data-testid="backup-folder"]') !== 'E:\\LocalBib-Backups') throw new Error('folder not shown');
        });
        expect(calls.find((c) => c.method === 'PUT').body).toEqual({ folder: 'E:\\LocalBib-Backups' });
        expect(text('[data-testid="backup-list-empty"]')).toBe('No backups here yet.');
        expect(text('[data-testid="backup-never-deleted"]')).toContain('never deletes or overwrites a backup');

        // --- back up now: the dialog asks, nothing runs before the answer ---------
        q('[data-testid="backup-start"]').click();
        await flush();
        expect(q('[data-testid="backup-ask"]')).toBeTruthy();
        expect(text('[data-testid="backup-ask-with-pdfs"]')).toContain('about 302 MB');
        expect(text('[data-testid="backup-ask-with-pdfs"]')).toContain('12 PDF files');
        expect(text('[data-testid="backup-ask-data-only"]')).toContain('about 2 MB');
        expect(calls.some((c) => c.url === '/api/backup/run')).toBe(false);
        q('[data-testid="backup-ask-data-only"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="backup-progress"]')) throw new Error('progress not shown');
        });
        expect(calls.find((c) => c.url === '/api/backup/run').body).toEqual({ include_pdfs: false });
        expect(text('[data-testid="backup-phase"]')).toBe('Backup · Copying the PDFs');
        // The section reads the state again while the run is under way.
        await vi.waitFor(() => {
            if (!q('[data-testid="backup-done"]')) throw new Error('run not finished');
        }, { timeout: 3000 });
        expect(text('[data-testid="backup-done"]')).toBe('Backup created and checked · 1.5 MB');
        expect(text('[data-testid="backup-count"]')).toBe('1 backup · 1.5 MB');
        expect(q('[data-backup="LocalBib-Backup_2026-10-09_14-30-12_data-only.zip"]').textContent).toContain('Data only');
        expect(text('[data-testid="backup-newest"]')).toContain('Newest backup:');

        // The next run asks again — and Cancel starts nothing.
        q('[data-testid="backup-start"]').click();
        await flush();
        expect(q('[data-testid="backup-ask"]')).toBeTruthy();
        q('[data-testid="backup-ask-cancel"]').click();
        await flush();
        expect(q('[data-testid="backup-ask"]')).toBeNull();
        expect(calls.filter((c) => c.url === '/api/backup/run').length).toBe(1);

        // --- the schedule needs the PDF answer first --------------------------------
        expect(q('[data-testid="backup-interval-daily"]').disabled).toBe(true);
        expect(text('[data-testid="backup-auto-state"]')).toBe('First answer whether the PDFs go along.');
        expect(q('[data-testid="backup-scope-with-pdfs"]').getAttribute('aria-checked')).toBe('false');
        expect(q('[data-testid="backup-scope-data-only"]').getAttribute('aria-checked')).toBe('false');
        q('[data-testid="backup-scope-with-pdfs"]').click();
        await vi.waitFor(() => {
            if (q('[data-testid="backup-interval-daily"]').disabled) throw new Error('interval still locked');
        });
        q('[data-testid="backup-interval-weekly"]').click();
        await vi.waitFor(() => {
            if (!q('[data-testid="backup-growth"]')) throw new Error('growth hint missing');
        });
        const puts = calls.filter((c) => c.method === 'PUT').map((c) => c.body);
        expect(puts.slice(1)).toEqual([{ auto_pdfs: true }, { auto: 'weekly' }]);
        expect(text('[data-testid="backup-auto-state"]')).toContain('Next automatic backup:');
        expect(text('[data-testid="backup-growth"]')).toContain('LocalBib never deletes old backups');
        expect(text('[data-testid="backup-growth"]')).toContain('about 302 MB');
        expect(document.body.textContent).not.toContain('⟦');
    });
});
