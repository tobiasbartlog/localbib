// Sidebar focus mode (#161): opening a paper folds the sidebar to its icon
// rail and closing unfolds it again — without ever writing the stored
// preference, and without overriding a fold the reader undid by hand.
import { describe, it, expect, beforeEach } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

const PAPER = {
    id: 81, title: 'Focus Mode', authors: 'Kern, D.', year: 2026,
    doi: '', journal: '', filename: '', notes: 'x', categories: [], custom_fields: [],
};

const sidebar = () => document.querySelector('.lb-sidebar');
const isFolded = () => sidebar().className.includes('is-collapsed');
const foldToggle = () => document.querySelector('.lb-collapse');

async function goto(hash) {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await flush(); await flush(); await flush();
}

describe('sidebar focus mode', () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it('folds on open, unfolds on close, and never writes the stored preference', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock([
            { method: 'GET', url: '/api/papers/81', reply: PAPER },
            { method: 'GET', url: '/api/papers/81/references', reply: { references: [] } },
            { method: 'GET', url: '/api/papers/81/bibtex', reply: { key: 'k2026' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');
        await flush();
        expect(isFolded()).toBe(false);

        await goto('#/paper/81');
        expect(isFolded()).toBe(true);
        expect(localStorage.getItem('lbSidebarCollapsed')).toBeNull();

        await goto('#/');
        expect(isFolded()).toBe(false);
        expect(localStorage.getItem('lbSidebarCollapsed')).toBeNull();

        // Unfolding by hand while a paper is open ends the automatism: closing
        // the paper restores nothing over that choice.
        await goto('#/paper/81');
        expect(isFolded()).toBe(true);
        foldToggle().click();
        await flush();
        expect(isFolded()).toBe(false);
        expect(localStorage.getItem('lbSidebarCollapsed')).toBe('0');

        await goto('#/');
        expect(isFolded()).toBe(false);
    });
});
