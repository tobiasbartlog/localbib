// Knowledge network, the card's chat (#242): it says what it answers from,
// offers three quick questions, sends the selected work as context to
// /api/analysis/chat and renders the streamed answer as safe Markdown. The
// selected work, the card's tab and the conversation survive a detail view
// and back; another work starts a new chat. In a Collection "Ask about …"
// opens the card straight in the chat, with at most 20 items and the hint
// that says so; "Details" is unavailable there.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootAnalysis, flush, goTo, preferReducedMotion, typeInto } from './helpers.js';
import { ABSTRACT_ROUTE, card, clickNode, netNode, network } from './analysis_fixtures.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => q(sel).textContent.replace(/\s+/g, ' ').trim();
const chatCalls = (calls) => calls.filter((c) => c.method === 'POST' && c.url === '/api/analysis/chat');
const messages = () => [...document.querySelectorAll('[data-testid="analysis-chat-messages"] .lb-an-msg')];

describe('analysis: the chat in the card', () => {
    it('answers about the work, keeps the talk across a detail view and serves a Collection', async () => {
        preferReducedMotion();
        const coll = { id: 4, name: 'Paper X', description: '', archived: false, item_count: 25, pdf_count: 1 };
        let answer = 0;
        const calls = await bootAnalysis([
            { method: 'GET', url: '/api/llm/status', reply: { reasoning: true, fast: true, embedding: false, connections: 1 } },
            { method: 'POST', url: /^\/api\/analysis\/build\?depth=\d/, reply: network() },
            ABSTRACT_ROUTE,
            {
                method: 'POST', url: '/api/analysis/chat',
                stream: () => (++answer === 1
                    ? [{ content: 'Earth is **built**' }, { content: ' by hand.' }]
                    : [{ content: 'Mostly <img src="x" onerror="alert(1)"> clay.' }]),
            },
            { method: 'GET', url: '/api/collections/4', reply: coll },
            {
                method: 'GET', url: '/api/papers?collection_id=4',
                reply: Array.from({ length: 25 }, (_, i) => ({ id: 100 + i, title: 'Member ' + i, categories: [] })),
            },
        ]);
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });

        clickNode('w1');
        await vi.waitFor(() => { if (!q('[data-testid="analysis-card-abstract"]')) throw new Error('abstract not loaded'); });
        q('[data-testid="analysis-card-tab-chat"]').click();
        await vi.waitFor(() => { if (!q('[data-testid="analysis-chat-quick"]')) throw new Error('chat not shown'); });

        // What it answers from; three quick questions; a labelled input.
        expect(text('[data-testid="analysis-chat-basis"]')).toBe('Answers from the title and abstract of this work. LocalBib does not know the full text.');
        const chips = [...q('[data-testid="analysis-chat-quick"]').querySelectorAll('button')];
        expect(chips.map((b) => b.textContent.trim())).toEqual(['What is it about?', 'What are the key findings?', 'Which methods are mentioned?']);
        const input = q('[data-testid="analysis-chat-input"]');
        const label = document.querySelector(`label[for="${input.id}"]`);
        expect(label.textContent.trim()).toBe('Your question');
        expect(label.classList.contains('lb-an-sr')).toBe(false);
        expect(input.getAttribute('placeholder')).toBe('Question about this work…');
        expect(q('[data-testid="analysis-chat-send"]').getAttribute('aria-label')).toBe('Send question');
        const tabs = [...card().querySelectorAll('[role="tab"]')];
        expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['false', 'true']);

        // A quick question: the work goes along as context, the answer streams in.
        chips[0].click();
        await vi.waitFor(() => { if (messages().length !== 2) throw new Error('no answer yet'); });
        const first = chatCalls(calls)[0];
        expect(first.body.question).toBe('What is it about?');
        expect(first.body.paper_ids).toEqual([]);
        expect(first.body.external_papers).toEqual([{
            title: 'Building with earth', authors: '', year: 2006, doi: '10.1000/earth', abstract: 'How earth is built.',
        }]);
        expect(messages()[0].textContent).toContain('What is it about?');
        const reply = messages()[1].querySelector('.lb-an-msg-text');
        expect(reply.querySelector('strong').textContent).toBe('built');
        expect(reply.textContent.trim()).toBe('Earth is built by hand.');

        // A typed follow-up carries the history; the answer is sanitised.
        typeInto(input, 'And the walls?');
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        await vi.waitFor(() => { if (messages().length !== 4) throw new Error('no second answer'); });
        expect(chatCalls(calls)[1].body.history.map((m) => m.role)).toEqual(['user', 'assistant']);
        const unsafe = messages()[3].querySelector('.lb-an-msg-text');
        expect(unsafe.querySelector('[onerror]')).toBeNull();
        expect(q('[data-testid="analysis-chat-clear"]').textContent.trim()).toBe('Clear chat');

        // To a detail view and back: the work, the tab and the talk remain.
        goTo('#/');
        await vi.waitFor(() => { if (q('.lb-analyse')) throw new Error('still on the analysis page'); });
        goTo('#/analyse');
        await vi.waitFor(() => {
            if (!card() || messages().length !== 4) throw new Error('card not restored');
        });
        expect(card().querySelector('h2').textContent).toBe('Building with earth');
        expect(card().getAttribute('data-card-tab')).toBe('chat');
        expect(netNode('w1').getAttribute('data-dimmed')).toBe('false');
        expect(netNode('w3').getAttribute('data-dimmed')).toBe('true');

        // Another work starts a new chat - this one has no abstract.
        clickNode('w2');
        await vi.waitFor(() => {
            if (!card() || card().querySelector('h2').textContent !== 'Rammed earth walls') throw new Error('not switched');
        });
        expect(card().getAttribute('data-card-tab')).toBe('details');
        await vi.waitFor(() => { if (!q('[data-testid="analysis-card-abstract-none"]')) throw new Error('abstract not settled'); });
        q('[data-testid="analysis-card-tab-chat"]').click();
        await flush();
        expect(messages()).toHaveLength(0);
        expect(q('[data-testid="analysis-chat-quick"]')).not.toBeNull();
        expect(text('[data-testid="analysis-chat-basis"]')).toBe('There is no abstract for this work. Answers rest only on the title and the details from OpenAlex.');

        // A Collection: "Ask about …" opens the card in the chat.
        goTo('#/analyse?collection_id=4');
        await vi.waitFor(() => {
            if (!q('[data-testid="analysis-start"]')) throw new Error('scope not switched');
        });
        q('[data-testid="analysis-start"]').click();
        await vi.waitFor(() => { if (!netNode('w1')) throw new Error('net not drawn'); });
        const ask = q('[data-testid="analysis-chat-collection"]');
        expect(ask.textContent.trim()).toBe('Ask about “Paper X”');
        ask.click();
        const c = await vi.waitFor(() => {
            const el = card();
            if (!el || !q('[data-testid="analysis-chat-capped"]')) throw new Error('collection chat not open');
            return el;
        });
        expect(c.querySelector('.lb-an-card-kicker').textContent.trim()).toBe('Collection');
        expect(c.querySelector('h2').textContent).toBe('Paper X');
        expect(text('[data-testid="analysis-chat-capped"]')).toBe('The chat knows the first 20 of 25 items of this collection.');
        expect(c.getAttribute('data-card-tab')).toBe('chat');
        const details = q('[data-testid="analysis-card-tab-details"]');
        expect(details.getAttribute('aria-disabled')).toBe('true');
        expect(details.getAttribute('title')).toBe('Details exist for a selected work');
        details.click();
        await flush();
        expect(card().getAttribute('data-card-tab')).toBe('chat');
        expect(text('[data-testid="analysis-chat-basis"]')).toBe('Answers from the abstract and an excerpt of the PDF of each item.');
        await vi.waitFor(() => { if (document.activeElement !== c.querySelector('h2')) throw new Error('focus not on the title'); });
        const collInput = q('[data-testid="analysis-chat-input"]');
        expect(collInput.getAttribute('placeholder')).toBe('Question about the collection…');
        typeInto(collInput, 'What connects them?');
        await flush();
        q('[data-testid="analysis-chat-send"]').click();
        await vi.waitFor(() => { if (chatCalls(calls).length !== 3) throw new Error('not sent'); });
        const collCall = chatCalls(calls)[2];
        expect(collCall.body.paper_ids).toHaveLength(20);
        expect(collCall.body.paper_ids[0]).toBe(100);
        expect(collCall.body.external_papers).toBeUndefined();
        await vi.waitFor(() => { if (messages().length !== 2) throw new Error('no answer'); });

        // Esc closes it; the focus goes back to "Ask about …".
        document.activeElement.blur();
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await flush();
        expect(card()).toBeNull();
        expect(document.activeElement).toBe(q('[data-testid="analysis-chat-collection"]'));
    });
});
