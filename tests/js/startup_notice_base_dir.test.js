// Startup guard (cloud handoff task 3): an unusable base folder in .env
// (a control character from an older version's raw quoting, or a relative
// path) is ignored at start; the popup after the boot tells the user which
// value was ignored, why, and which folder is used instead — even when every
// AI role works.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootApp, settingsSection } from './helpers.js';
import { OK_REPORT, setUpRoutes } from './startup_check_fixtures.js';

const q = (sel) => document.querySelector(sel);

const REPORT = {
    ...OK_REPORT,
    notices: [{
        code: 'baseDirIgnored',
        params: { value: 'C:\\Bib\\x09x', reason: 'control', used: 'C:\\Users\\tim\\Literatur' },
    }],
};

describe('startup notice: ignored base folder', () => {
    it('says which folder was ignored and leads to the library settings', async () => {
        await bootApp(setUpRoutes(REPORT));
        const dialog = await vi.waitFor(() => {
            const el = q('[data-testid="startup-check-dialog"]');
            if (!el) throw new Error('popup not shown');
            return el;
        });
        expect(dialog.textContent).toContain('LocalBib started with a correction');
        const notice = q('[data-testid="startup-notice-baseDirIgnored"]').textContent;
        expect(notice).toContain('C:\\Bib\\x09x');
        expect(notice).toContain('control character');
        expect(notice).toContain('C:\\Users\\tim\\Literatur');
        // No AI problem: no role list, no "features are off".
        expect(q('[data-testid="startup-check-impact"]')).toBeNull();
        expect(q('[data-testid^="startup-check-role-"]')).toBeNull();
        expect(document.body.textContent).not.toContain('⟦');

        q('[data-testid="startup-check-open-library"]').click();
        await vi.waitFor(() => {
            if (settingsSection() !== 'library') throw new Error('library settings not open');
        });
        expect(q('[data-testid="startup-check-dialog"]')).toBeNull();
    });
});
