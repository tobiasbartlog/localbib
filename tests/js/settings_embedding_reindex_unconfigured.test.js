// Gegenstueck zu settings_embedding_reindex: ohne konfiguriertes
// Embedding-Modell nennt der Wartungsabschnitt die fehlende Konfiguration,
// statt einen leeren Indexstand (0/0) als Ergebnis auszugeben.
// Eigene Datei, weil die SPA sich pro Testdatei genau einmal selbst mountet.
import { describe, it, expect } from 'vitest';
import { bootSettings, findByText } from './helpers.js';

describe('settings: semantischer Index ohne Modell', () => {
    it('nennt die fehlende Konfiguration statt eines leeren Indexstands', async () => {
        await bootSettings([
            {
                method: 'GET',
                url: '/api/maintenance/embeddings/status',
                reply: { indexed: 0, total: 12, model: '', dim: 0, chunks_indexed: 0, chunks_total: 0 },
            },
        ]);

        expect(findByText('p', 'Kein Embedding-Modell konfiguriert')).toBeTruthy();
        // Die Buttons bleiben sichtbar — der Lauf degradiert serverseitig und
        // meldet das zurueck, statt hier weggeblendet zu werden.
        expect(findByText('button', 'Textstellen indexieren')).toBeTruthy();
    });
});
