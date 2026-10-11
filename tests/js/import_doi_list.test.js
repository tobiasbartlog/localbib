// Import a DOI list (#227): the import page's "DOIs" section, prefilled by a
// link (`/import?dois=…&collection=…`), previews what is new and what the
// library already holds through the DOI existence check, creates nothing
// until the click, then creates through the batch endpoint for DOIs and puts
// every Item into the target collection — resolved before the first write.
// The tests share one booted app and run in order.
import { describe, it, expect, vi } from 'vitest';
import { installFetchMock, defaultRoutes, flush, goTo, typeInto } from './helpers.js';

const q = (sel) => document.querySelector(sel);
const qa = (sel) => [...document.querySelectorAll(sel)];
const rowStates = () => qa('[data-testid="import-doi-row"]').map((r) => [
    r.querySelector('[data-testid="import-doi-value"]').textContent.trim(), r.getAttribute('data-state')]);
const input = () => q('[data-testid="import-doi-input"]');
const target = () => q('[data-testid="import-doi-target-select"]');
const SICI = '10.1002/(SICI)1097-4636(199706)35:4<419::AID-JBM1>3.0.CO;2-L';

function deferred() {
    let release;
    const promise = new Promise((resolve) => { release = resolve; });
    return { promise, release };
}

const KNOWN = { '10.1000/have': 7 };
const state = { holdExists: null, holdBatch: null, holdCollection: null, nextId: 20 };
// Creating a collection: answers 9, or fails while `status` is set.
const CREATE_COLLECTION = {
    method: 'POST', url: '/api/collections',
    reply: ({ body }) => {
        const answer = () => (CREATE_COLLECTION.status
            ? { detail: 'error.collection_name_empty' } : { id: 9, name: body.name, item_count: 0 });
        return state.holdCollection ? state.holdCollection.promise.then(answer) : answer();
    },
};
const ids = {};
const answerExists = (body) => ({ papers: Object.fromEntries(body.dois.map((d) => [d, KNOWN[d] ?? null])) });
const answerBatch = (body) => {
    const results = body.items.map((it) => {
        ids[it.doi] = ids[it.doi] || ++state.nextId;
        return { doi: it.doi, created: true, paper_id: ids[it.doi], citekey: '', pdf: it.doi.endsWith('a') ? 'fetched' : 'none' };
    });
    return { total: results.length, created: results.length, existing: 0, errors: 0, results };
};

async function shows(sel) {
    return vi.waitFor(() => {
        const el = q(sel);
        if (!el) throw new Error('not shown: ' + sel);
        return el;
    });
}

describe('import a DOI list', () => {
    let calls;
    const writes = () => calls.filter((c) => c.method !== 'GET' && c.url !== '/api/papers/by-doi/exists');

    it('previews a prefilled list and creates only after the click, into the target collection', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        calls = installFetchMock([
            { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
            { method: 'GET', url: '/api/import/pending', reply: { files: [], count: 0 } },
            { method: 'GET', url: /^\/api\/collections(\?.*)?$/, reply: [
                { id: 3, name: 'Other', item_count: 0 }, { id: 4, name: 'Paper X', item_count: 1 }] },
            {
                method: 'POST', url: '/api/papers/by-doi/exists',
                reply: ({ body }) => (state.holdExists
                    ? state.holdExists.promise.then(() => answerExists(body)) : answerExists(body)),
            },
            {
                method: 'POST', url: '/api/papers/by-doi/batch',
                reply: ({ body }) => (state.holdBatch
                    ? state.holdBatch.promise.then(() => answerBatch(body)) : answerBatch(body)),
            },
            CREATE_COLLECTION,
            {
                method: 'POST', url: /^\/api\/collections\/\d+\/items$/,
                reply: ({ body }) => ({ added_ids: body.paper_ids.filter((id) => id !== 7), already_ids: [7] }),
            },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        goTo('#/import?dois=' + encodeURIComponent('10.1000/new-a, https://doi.org/10.1000/HAVE')
            + '&dois=' + encodeURIComponent('10.1000/new-b not-a-doi 10.1000/new-a') + '&collection=4');

        // Prefilled and previewed at once: the existence check is a read.
        const summary = await shows('[data-testid="import-doi-summary"]');
        expect(input().value)
            .toBe('10.1000/new-a\nhttps://doi.org/10.1000/HAVE\n10.1000/new-b\nnot-a-doi\n10.1000/new-a');
        expect(summary.textContent).toContain('3 DOIs: 1 already in the library, 2 new');
        expect(rowStates()).toEqual([
            ['10.1000/new-a', 'new'], ['10.1000/have', 'existing'], ['10.1000/new-b', 'new'], ['not-a-doi', 'invalid'],
        ]);
        expect(q('[data-testid="import-doi-invalid"]').textContent).toContain('1 value is not a DOI and is skipped');
        expect(q('[data-testid="import-doi-row"][data-state="existing"] a').getAttribute('href')).toBe('#/paper/7');
        const exists = calls.filter((c) => c.url === '/api/papers/by-doi/exists');
        expect(exists.map((c) => c.body.dois)).toEqual([['10.1000/new-a', '10.1000/have', '10.1000/new-b']]);
        await vi.waitFor(() => { if (target().value !== '4') throw new Error('target not preselected'); });

        // Nothing is created before the click.
        await flush();
        expect(writes()).toEqual([]);

        const commit = q('[data-testid="import-doi-commit"]');
        expect(commit.textContent).toContain('Create 2 items');
        commit.click();

        const result = await shows('[data-testid="import-doi-result"]');
        expect(result.textContent).toContain('2 created, 1 already present.');
        const batch = calls.filter((c) => c.url === '/api/papers/by-doi/batch');
        expect(batch.map((c) => c.body)).toEqual([{ items: [{ doi: '10.1000/new-a' }, { doi: '10.1000/new-b' }] }]);
        const assign = calls.find((c) => c.method === 'POST' && c.url === '/api/collections/4/items');
        expect(assign.body).toEqual({ paper_ids: [ids['10.1000/new-a'], 7, ids['10.1000/new-b']] });
        expect(q('[data-testid="import-doi-collection"]').textContent).toBe('Added to collection “Paper X”');
        expect(rowStates()).toEqual([
            ['10.1000/new-a', 'created'], ['10.1000/have', 'existing'], ['10.1000/new-b', 'created'], ['not-a-doi', 'invalid'],
        ]);
        expect(q('[data-testid="import-doi-row"][data-state="created"] a').getAttribute('href'))
            .toBe('#/paper/' + ids['10.1000/new-a']);
    });

    it('a typed list that holds nothing new offers nothing to create without a collection', async () => {
        const before = calls.length;
        typeInto(input(), 'doi:10.1000/have');
        await flush();
        expect(q('[data-testid="import-doi-result"]')).toBeNull();
        target().value = '';
        target().dispatchEvent(new Event('change'));
        q('[data-testid="import-doi-check"]').click();

        const nothing = await shows('[data-testid="import-doi-nothing"]');
        expect(nothing.textContent).toContain('Everything is already in the library');
        expect(q('[data-testid="import-doi-summary"]').textContent).toContain('1 DOI: 1 already in the library, 0 new');
        expect(q('[data-testid="import-doi-commit"]')).toBeNull();
        expect(calls.slice(before).filter((c) => c.method !== 'GET' && c.url !== '/api/papers/by-doi/exists')).toEqual([]);
    });

    it('splits a pasted list only where a new DOI starts, so ; and , inside a DOI survive', async () => {
        typeInto(input(), SICI + ', 10.1000/a,b;10.1000/c\ndoi:10.1000/d https://www.doi.org/10.1000/C.');
        await flush();
        q('[data-testid="import-doi-check"]').click();
        await shows('[data-testid="import-doi-summary"]');
        expect(rowStates()).toEqual([
            [SICI.toLowerCase(), 'new'], ['10.1000/a,b', 'new'], ['10.1000/c', 'new'], ['10.1000/d', 'new'],
        ]);
    });

    it('applies the collection of every link: its own, none, or a hint when it is not available', async () => {
        const linkTo = async (hash, value) => {
            goTo(hash);
            await vi.waitFor(() => {
                if (input().value !== value) throw new Error('not prefilled: ' + input().value);
                if (!q('[data-testid="import-doi-summary"]')) throw new Error('no preview');
            });
            await flush();
        };
        await linkTo('#/import?dois=10.1000/a1&collection=4', '10.1000/a1');
        expect(target().value).toBe('4');
        // The next publication's list without a collection: not into "Paper X".
        await linkTo('#/import?dois=10.1000/b1', '10.1000/b1');
        expect(target().value).toBe('');
        expect(q('[data-testid="import-doi-target-unavailable"]')).toBeNull();
        // An archived or deleted collection is not offered: none, and why.
        await linkTo('#/import?dois=10.1000/c1&collection=99', '10.1000/c1');
        expect(target().value).toBe('');
        expect(q('[data-testid="import-doi-target-unavailable"]').textContent)
            .toContain('The collection from the link is not available');
        await linkTo('#/import?dois=10.1000/d1&collection=3', '10.1000/d1');
        expect(target().value).toBe('3');
        expect(q('[data-testid="import-doi-target-unavailable"]')).toBeNull();
    });

    it('lets only the latest check set the preview', async () => {
        state.holdExists = deferred();
        const before = calls.length;
        goTo('#/import?dois=10.1000/old1');
        await vi.waitFor(() => {
            if (!calls.slice(before).some((c) => c.url === '/api/papers/by-doi/exists')) throw new Error('no check');
        });
        goTo('#/import?dois=10.1000/new1');
        await vi.waitFor(() => { if (input().value !== '10.1000/new1') throw new Error('not prefilled'); });
        const held = state.holdExists;
        state.holdExists = null;
        held.release();
        await shows('[data-testid="import-doi-summary"]');
        await flush();
        expect(rowStates()).toEqual([['10.1000/new1', 'new']]);
        expect(q('[data-testid="import-doi-summary"]').textContent).toContain('1 DOI: 0 already in the library, 1 new');
    });

    it('creates a new collection before the first Item and holds a link that arrives meanwhile', async () => {
        typeInto(input(), '10.1000/n1 10.1000/n2');
        await flush();
        target().value = 'new';
        target().dispatchEvent(new Event('change'));
        typeInto(await shows('[data-testid="import-doi-target-new-name"]'), 'Fresh');
        q('[data-testid="import-doi-check"]').click();
        await shows('[data-testid="import-doi-commit"]');
        await flush();

        state.holdBatch = deferred();
        const before = calls.length;
        q('[data-testid="import-doi-commit"]').click();
        await vi.waitFor(() => {
            if (!calls.slice(before).some((c) => c.url === '/api/papers/by-doi/batch')) throw new Error('no batch');
        });
        const order = calls.slice(before).map((c) => c.method + ' ' + c.url);
        expect(order.indexOf('POST /api/collections')).toBeGreaterThan(-1);
        expect(order.indexOf('POST /api/collections')).toBeLessThan(order.indexOf('POST /api/papers/by-doi/batch'));
        expect(calls.find((c) => c.method === 'POST' && c.url === '/api/collections').body).toEqual({ name: 'Fresh' });

        // A link while Items are being created: the list being created stays.
        goTo('#/import?dois=10.1000/g1&collection=4');
        const queued = await shows('[data-testid="import-doi-queued"]');
        expect(queued.textContent).toContain('Another list arrived');
        expect(q('[data-testid="import-doi-open-queued"]')).toBeNull();  // not while creating
        expect(input().value).toBe('10.1000/n1 10.1000/n2');

        const held = state.holdBatch;
        state.holdBatch = null;
        held.release();
        // The result stays — the waiting list does not wipe it.
        const result = await shows('[data-testid="import-doi-result"]');
        expect(result.textContent).toContain('2 created');
        const assign = calls.find((c) => c.method === 'POST' && c.url === '/api/collections/9/items');
        expect(assign.body).toEqual({ paper_ids: [ids['10.1000/n1'], ids['10.1000/n2']] });
        expect(q('[data-testid="import-doi-collection"]').textContent).toBe('Added to collection “Fresh”');
        await flush();
        expect(input().value).toBe('10.1000/n1 10.1000/n2');
        expect(q('[data-testid="import-doi-result"]')).not.toBeNull();

        // Only the click opens the waiting list.
        (await shows('[data-testid="import-doi-open-queued"]')).click();
        await vi.waitFor(() => {
            if (input().value !== '10.1000/g1') throw new Error('queued link not applied');
            if (target().value !== '4') throw new Error('queued collection not applied');
        });
        await shows('[data-testid="import-doi-summary"]');
        expect(q('[data-testid="import-doi-queued"]')).toBeNull();
        expect(q('[data-testid="import-doi-result"]')).toBeNull();
    });

    it('a failed target keeps its error, and the progress of the last run, until the next list is opened', async () => {
        typeInto(input(), '10.1000/n3');
        await flush();
        target().value = 'new';
        target().dispatchEvent(new Event('change'));
        typeInto(await shows('[data-testid="import-doi-target-new-name"]'), 'Broken');
        q('[data-testid="import-doi-check"]').click();
        await shows('[data-testid="import-doi-commit"]');
        await flush();

        CREATE_COLLECTION.status = 422;
        state.holdCollection = deferred();
        const before = calls.length;
        q('[data-testid="import-doi-commit"]').click();
        await vi.waitFor(() => {
            if (!calls.slice(before).some((c) => c.method === 'POST' && c.url === '/api/collections')) throw new Error('no POST');
        });
        // While the target is created, the progress is this run's, not 2 of 2.
        expect(q('[data-testid="import-doi-progress"]').textContent).toContain('0 of 1');

        goTo('#/import?dois=10.1000/h1');
        await shows('[data-testid="import-doi-queued"]');
        const held = state.holdCollection;
        state.holdCollection = null;
        held.release();
        const error = await shows('[data-testid="import-doi-error"]');
        expect(error.textContent).toBe('A collection needs a name.');
        await flush();
        expect(input().value).toBe('10.1000/n3');  // the waiting list did not take over
        expect(calls.slice(before).some((c) => c.url === '/api/papers/by-doi/batch')).toBe(false);
        delete CREATE_COLLECTION.status;

        q('[data-testid="import-doi-open-queued"]').click();
        await vi.waitFor(() => { if (input().value !== '10.1000/h1') throw new Error('not opened'); });
        await shows('[data-testid="import-doi-summary"]');
        expect(q('[data-testid="import-doi-error"]')).toBeNull();
    });
});
