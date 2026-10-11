// Item detail (architecture review 2026-10-09): a refused request says what
// went wrong in the UI language. The open-access fetch used to show the raw
// error code (SPA-4), and a refused reference extraction - JSON instead of a
// stream - stopped the spinner without a word (SPA-5).
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { defaultRoutes, findByText, flush, goTo, installFetchMock } from './helpers.js';

const PAPER = {
    id: 99, title: 'Some Chapter', authors: 'X', year: 2025,
    doi: '10.1/x', filename: '', categories: [], custom_fields: [],
};

describe('item detail: refusals are translated', () => {
    it('shows the translated reason for the OA fetch and the reference extraction', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        const alerts = [];
        vi.spyOn(window, 'alert').mockImplementation((msg) => { alerts.push(msg); });
        installFetchMock([
            { method: 'GET', url: '/api/papers/99', reply: PAPER },
            { method: 'GET', url: '/api/papers/99/references', reply: { references: [] } },
            { method: 'GET', url: '/api/papers/99/bibtex', reply: { key: 'x2025' } },
            { method: 'POST', url: '/api/papers/99/fetch-oa-pdf', reply: { detail: 'error.no_oa_pdf_url' }, status: 404 },
            { method: 'POST', url: /\/api\/papers\/99\/extract-references/, reply: { detail: 'error.no_pdf_for_item' }, status: 404 },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        goTo('#/paper/99');
        const oa = await vi.waitFor(() => {
            const el = findByText('button', 'PDF from Open Access');
            if (!el) throw new Error('detail not rendered');
            return el;
        });

        // --- SPA-4: the OA fetch names the reason, not its code -----------------
        oa.click();
        await vi.waitFor(() => { if (!alerts.length) throw new Error('no alert'); });
        expect(alerts[0]).toContain('No open-access PDF URL found');
        expect(alerts[0]).not.toContain('error.no_oa_pdf_url');

        // --- SPA-5: a refused extraction shows its reason ------------------------
        document.querySelector('button[aria-label="More"]').click();
        await flush();
        findByText('button', 'Extract references').click();
        await vi.waitFor(() => { if (!findByText('p', 'No PDF for this item')) throw new Error('no error shown'); });
    });
});
