// The knowledge network in German (#241, ADR-0018): every new text comes
// from the catalog (no ⟦key⟧ anywhere on the page), numbers follow the UI
// locale, and the page no longer says "Quelle", "Primärquelle", "geteilt" or
// "LLM" - Quellen are the Sources (CrossRef, OpenAlex, arXiv), the terms here
// are Zitiertes Werk, Gemeinsame Referenz and KI-Modell. The English run is
// covered by the other analysis smokes.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, preferReducedMotion } from './helpers.js';
import { analysisText, clickNode, network, nodesOfKind } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.replace(/\s+/g, ' ').trim();
const FORBIDDEN = /Quelle|Primärquelle|geteilt|LLM/;

function expectCleanPage() {
    const all = analysisText();
    expect(all).not.toContain('⟦');
    expect(all).not.toMatch(FORBIDDEN);
}

describe('analysis: German interface', () => {
    it('renders every state from the catalog, in German terms', async () => {
        window.LB_LANG = 'de';
        preferReducedMotion();
        await bootAnalysis([
            { method: 'GET', url: '/api/stats', reply: { paper_count: 34, with_doi: 30 } },
            { method: 'POST', url: '/api/analysis/build?depth=1', reply: network() },
        ]);
        await vi.waitFor(() => { if (!q('[data-testid="analysis-empty"]')) throw new Error('empty state missing'); });
        await vi.waitFor(() => {
            if (!q('[data-testid="analysis-meta-items"]')) throw new Error('count missing');
        });

        // Before the first run.
        expect(text('.lb-an-head h1')).toBe('Wissensnetz');
        expect(text('.lb-an-head .lb-eyebrow')).toBe('Analyse');
        expect(text('[data-testid="analysis-pdf-refs"]')).toBe('PDF-Referenzen');
        expect(text('[data-testid="analysis-meta-items"]')).toBe('34 eigene Einträge, 30 davon mit DOI');
        expect(text('[data-testid="analysis-empty"] h2')).toBe('Noch kein Wissensnetz');
        expect(text('[data-testid="analysis-empty"] .lb-an-label')).toBe('Suchtiefe');
        expect(text('[data-testid="analysis-empty"] .lb-an-depth-name')).toBe('Direkte Referenzen');
        expect(text('[data-testid="analysis-start"]')).toBe('Analyse starten');
        expect(analysisText()).toContain('An OpenAlex gehen DOIs und Titel deiner Einträge, sonst nichts.');
        expectCleanPage();

        // The dialog without an AI model.
        q('[data-testid="analysis-pdf-refs"]').click();
        await vi.waitFor(() => {
            const el = q('[data-testid="bulk-dialog"]');
            if (!el || el.getAttribute('data-state') !== 'nomodel') throw new Error('dialog not open');
        });
        expect(text('[data-testid="bulk-dialog"] h2')).toBe('PDF-Referenzen auslesen');
        expect(text('[data-testid="bulk-setup-models"]')).toBe('KI-Modelle einrichten');
        expect(text('[data-testid="bulk-no-model"]')).toBe('Zum Auslesen der Literaturverzeichnisse braucht LocalBib ein KI-Modell. Es ist noch keins verbunden.');
        expectCleanPage();
        q('[data-testid="bulk-close"]').click();
        await flush();

        // After a build: German numbers, views, tables, legend, chat hint.
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!nodesOfKind('own').length) throw new Error('network not drawn'); });
        expect(text('[data-testid="analysis-meta-items"]')).toBe('34 eigene Einträge, 21 bei OpenAlex gefunden');
        expect(text('[data-testid="analysis-meta-references"]')).toBe('3.749 Referenzen, 2.969 aus PDF-Verzeichnissen');
        expect(text('[data-testid="analysis-meta-depth"]')).toBe('Netz mit Tiefe 3');
        expect(text('[data-testid="analysis-run"]')).toBe('Mit Tiefe 1 analysieren');
        const tabs = [...document.querySelectorAll('[data-testid="analysis-tabs"] [role="tab"]')].map((tab) => tab.textContent.replace(/\s+/g, ' ').trim());
        expect(tabs).toEqual(['Netzwerk', 'Eigene Einträge2', 'Zitierte Werke500+', 'Fehlend13']);
        expect(text('#lb-an-panel-references thead')).toContain('Herkunft');
        expect(analysisText()).toContain('Gemeinsame Referenz');
        expectCleanPage();

        // The net (#242): tools, legend bar and the card in German.
        expect(q('[data-testid="analysis-search"]').getAttribute('placeholder')).toBe('Im Netz suchen und hinspringen…');
        expect(text('[data-testid="analysis-lens"]')).toBe('Gemeinsame Referenzen 1');
        expect(text('[data-testid="analysis-own-items"]')).toBe('Eigene Einträge 2');
        expect([...document.querySelectorAll('[data-testid="analysis-legend"] li')].map((li) => li.textContent.replace(/\s+/g, ' ').trim()))
            .toEqual(['Eigener Eintrag', 'Gemeinsame Referenz', 'Referenz', 'Weitere Ebene', 'Größe = Zitationen', 'Zitiert', 'Aus PDF-Verzeichnis']);
        expect(text('[data-testid="analysis-hint"]')).toBe('Scrollen zoomt · Ziehen verschiebt · / sucht');
        expect(text('[data-testid="analysis-view-key"]')).toBe('Ansicht');
        clickNode('w1');
        await vi.waitFor(() => { if (!q('[data-testid="analysis-card"]')) throw new Error('card not open'); });
        expect(text('[data-testid="analysis-card-kind"]')).toBe('Gemeinsame Referenz');
        expect(text('[data-testid="analysis-card-citations"]')).toBe('412 Zitationen laut OpenAlex');
        expect(text('[data-testid="analysis-card"] h3')).toBe('Zitiert von 2 eigenen Einträgen');
        expect(text('[data-testid="node-add-to-library"]')).toBe('In die Bibliothek übernehmen');
        expectCleanPage();
    });
});
