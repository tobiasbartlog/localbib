// Research Chat (architecture review 2026-10-09): the question and the answer
// are foreign text in a v-html, so markup in them must arrive as text (SPA-3)
// while the `[Quelle X]` protocol still turns into citation badges; and an
// item removed from the selection stays removed after leaving the page and
// coming back (SPA-2: the chips are the persistent chat store, not a copy).
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import { defaultRoutes, flush, goTo, installFetchMock, typeInto } from './helpers.js';

const ITEMS = [
    { id: 1, title: 'Alpha', authors: 'Doe', year: 2020 },
    { id: 2, title: 'Beta', authors: 'Roe', year: 2021 },
];

const ANSWER = 'Look: <img src=x onerror="window.__lbXss=1"> **bold** [Quelle 1]\nnext line';
const SOURCES = [
    { paper_id: 1, title: 'Alpha "<b>quoted</b>"', authors: 'Doe', pages_referenced: [3, 4], previews: [] },
];

const chips = () => [...document.querySelectorAll('[data-testid="chat-item-chip"]')]
    .map((el) => el.textContent.replace('×', '').trim());

describe('research chat', () => {
    it('renders markup as text, keeps citation badges and keeps a removed item removed', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        history.replaceState(null, '', '#/research-chat?paper_ids=1,2');
        const calls = installFetchMock([
            { method: 'GET', url: '/api/papers', reply: ITEMS },
            {
                method: 'GET', url: /^\/api\/research-chat\/chunk-status/,
                reply: { papers: [{ paper_id: 1, chunked: true, chunk_count: 2 }, { paper_id: 2, chunked: true, chunk_count: 1 }] },
            },
            { method: 'POST', url: '/api/research-chat/ask', reply: { answer: ANSWER, sources: SOURCES } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await vi.waitFor(() => { if (chips().length !== 2) throw new Error('selection not loaded'); });

        // --- SPA-3: nothing the user or the model wrote becomes live markup ---
        const input = document.querySelector('input[placeholder^="Ask a question about the selected"]');
        typeInto(input, 'Why <img src=y onerror="window.__lbXss=2">?');
        await flush();
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'POST' && c.url === '/api/research-chat/ask')) throw new Error('not asked');
            if (!document.querySelector('.source-badge')) throw new Error('answer not rendered');
        });

        const bubbles = [...document.querySelectorAll('[data-msg-idx]')];
        expect(bubbles).toHaveLength(2);
        expect(document.querySelectorAll('[onerror]')).toHaveLength(0);
        expect(document.querySelectorAll('[data-msg-idx] img')).toHaveLength(0);
        expect(bubbles[0].textContent).toContain('Why <img src=y onerror="window.__lbXss=2">?');
        expect(bubbles[1].textContent).toContain('<img src=x onerror="window.__lbXss=1">');
        expect(window.__lbXss).toBeUndefined();

        // The formatting the chat makes itself still works.
        expect(bubbles[1].querySelector('strong').textContent).toBe('bold');
        expect(bubbles[1].querySelectorAll('br').length).toBeGreaterThan(0);
        const badge = bubbles[1].querySelector('.source-badge');
        expect(badge.textContent).toBe('[Q1]');
        expect(badge.getAttribute('data-source-idx')).toBe('0');
        expect(badge.getAttribute('href')).toBe('#/paper/1?page=3');
        expect(badge.getAttribute('title')).toBe('Alpha "<b>quoted</b>" — Doe, pp. 3, 4');
        expect(badge.querySelector('b')).toBeNull();

        // --- SPA-2: a removed item stays removed across a page change ---------
        document.querySelector('[data-testid="chat-item-remove-2"]').click();
        await flush();
        expect(chips()).toEqual(['Alpha']);

        goTo('#/');
        await vi.waitFor(() => { if (document.querySelector('[data-testid="chat-item-chip"]')) throw new Error('still on the chat'); });
        goTo('#/research-chat');
        await vi.waitFor(() => { if (!chips().length) throw new Error('chat not back'); });
        expect(chips()).toEqual(['Alpha']);
        // The conversation itself survived the trip too.
        expect(document.querySelectorAll('[data-msg-idx]')).toHaveLength(2);
    });
});
