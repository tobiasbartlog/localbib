// LocalBib.components.MarkdownEditor (ADR-0026): the Vue face of the editor
// seam. A value that arrives while the editor is still being built must end
// up in the editor - otherwise the first keystroke would save only itself
// over the whole stored text.
import { describe, it, expect } from 'vitest';
import { installFetchMock, defaultRoutes, flush } from './helpers.js';

describe('MarkdownEditor component', () => {
    it('shows a value that arrived while the editor was created', async () => {
        document.body.innerHTML = '<div id="app"></div>';
        installFetchMock(defaultRoutes());
        await import('../../static/app.js');
        await flush();

        const seam = window.LB_MD_EDITOR;
        const fake = seam.createMarkdownEditor;
        let release;
        const gate = new Promise((r) => { release = r; });
        let ta = null;
        seam.createMarkdownEditor = async (root, opts) => {
            const ed = await fake(root, opts);
            ta = root.querySelector('textarea');
            await gate;
            return ed;
        };
        try {
            const host = document.createElement('div');
            document.body.append(host);
            const app = Vue.createApp({
                components: { MarkdownEditor: window.LocalBib.components.MarkdownEditor },
                data: () => ({ text: '' }),
                template: '<MarkdownEditor v-model="text" />',
            });
            const vm = app.mount(host);
            await flush();
            expect(ta).toBeTruthy();
            expect(ta.value).toBe('');

            vm.text = 'Gespeicherter Text';   // the entity load lands mid-create
            await flush();
            release();
            await flush(); await flush();
            expect(ta.value).toBe('Gespeicherter Text');
            expect(vm.text).toBe('Gespeicherter Text');
            app.unmount();
        } finally {
            seam.createMarkdownEditor = fake;
        }
    });
});
