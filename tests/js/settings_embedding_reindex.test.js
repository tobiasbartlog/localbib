// Wartung → Semantischer Index (#153): Status-Zeile + die beiden Reindex-Buttons.
// Bis dahin waren `/api/maintenance/embeddings/reindex[-chunks]` nur per HTTP
// erreichbar — es gab keinen Weg, den Index aus der App heraus nachzuziehen.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, findByText, flush } from './helpers.js';

const STATUS = {
    indexed: 36,
    total: 36,
    model: 'qwen3-embedding-8b',
    dim: 4096,
    chunks_indexed: 13585,
    chunks_total: 15160,
};

describe('settings: semantischer Index', () => {
    it('zeigt den Indexstand und stoesst den Chunk-Reindex an', async () => {
        const calls = await bootSettings([
            { method: 'GET', url: '/api/maintenance/embeddings/status', reply: STATUS },
            {
                method: 'POST',
                url: '/api/maintenance/embeddings/reindex-chunks',
                reply: {
                    indexed: 1575, skipped: 13585, errors: 0, total: 15160,
                    model: 'qwen3-embedding-8b', dim: 4096,
                },
            },
        ]);

        const status = findByText('p', 'qwen3-embedding-8b');
        expect(status).toBeTruthy();
        expect(status.textContent).toContain('13585/15160');

        const button = findByText('button', 'Textstellen indexieren');
        expect(button).toBeTruthy();
        button.click();

        await vi.waitFor(() => {
            if (!findByText('span', '1575 Textstellen indexiert')) {
                throw new Error('Ergebnis nicht gerendert');
            }
        });
        await flush();

        expect(
            calls.some(
                (c) => c.method === 'POST' && c.url === '/api/maintenance/embeddings/reindex-chunks',
            ),
        ).toBe(true);
        // Der Lauf aktualisiert den angezeigten Stand danach selbst.
        const statusCalls = calls.filter((c) => c.url === '/api/maintenance/embeddings/status');
        expect(statusCalls.length).toBeGreaterThanOrEqual(2);

        // Die Paper-Ebene hat ihren eigenen Lauf daneben.
        expect(findByText('button', 'Paper indexieren')).toBeTruthy();
    });
});
