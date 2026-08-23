// Settings-UI: LLM_EMBED_MODEL als Dropdown (vom Anbieter abgerufen, wie beim
// LLM-Modell) + Provider-Presets als datalist fuer die Embedding-URL.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, findByText, flush, pluginNavRendered } from './helpers.js';

describe('settings: embedding model dropdown', () => {
    it('renders the embed-model dropdown from the provider list and the URL presets', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            {
                method: 'GET',
                url: '/api/llm/embed-models',
                reply: { models: ['qwen3-embedding-8b', 'text-embedding-3-small'], current: '' },
            },
            {
                method: 'GET',
                url: '/api/llm/models',
                reply: { models: ['gpt-5.5'], current: 'gpt-5.5', current_fast: '' },
            },
            {
                method: 'GET',
                url: '/api/llm/providers',
                reply: {
                    providers: [
                        { id: 'kiconnect', label: 'KI Connect NRW', base_url: 'https://chat.kiconnect.nrw/api/v1' },
                        { id: 'custom', label: 'Custom', base_url: '' },
                    ],
                    current: 'kiconnect',
                },
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        // Erst warten, bis der Plugin-Nav-Sync durch ist — syncPluginRoutes
        // feuert direkt nach der /api/plugins/nav-Antwort ein router.replace,
        // das eine fruehere Navigation zurueckziehen wuerde. Danach wie der
        // Nutzer ueber die Sidebar navigieren.
        const settingsLink = await vi.waitFor(() => {
            const a = [...document.querySelectorAll('a')].find(
                (el) => el.getAttribute('href') === '#/settings',
            );
            if (!a || !pluginNavRendered()) throw new Error('Sidebar noch nicht fertig gerendert');
            return a;
        });
        await flush();
        settingsLink.click();

        await vi.waitFor(() => {
            if (!findByText('h2', 'Einstellungen')) throw new Error('Einstellungen nicht gerendert');
        });
        await flush();

        // Embedding-Modell ist ein Dropdown mit den Anbieter-Modellen …
        const embedOption = [...document.querySelectorAll('select option')].find(
            (o) => o.value === 'qwen3-embedding-8b',
        );
        expect(embedOption).toBeTruthy();
        // … inklusive expliziter Aus-Option (leer = Feature deaktiviert)
        const offOption = [...document.querySelectorAll('select option')].find((o) =>
            o.textContent.includes('deaktiviert'),
        );
        expect(offOption).toBeTruthy();

        // Embedding-URL-Feld bietet die Provider-Presets als datalist an;
        // Custom ohne base_url erzeugt keinen Eintrag.
        const datalist = document.querySelector('datalist#embed-url-presets');
        expect(datalist).toBeTruthy();
        const presets = [...datalist.querySelectorAll('option')].map((o) => o.getAttribute('value'));
        expect(presets).toEqual(['https://chat.kiconnect.nrw/api/v1/embeddings']);
    });
});
