import { defineConfig } from 'vitest/config';

// Test runner for the buildless Vue-3-CDN SPA (issue #54). static/app.js is a
// plain browser script that expects `Vue`/`VueRouter` globals and string
// templates — tests/js/setup.js provides those globals, and the alias below
// swaps in the full Vue build (runtime + template compiler), matching the
// vue.global.prod.js bundle the CDN serves.
export default defineConfig({
    resolve: {
        alias: {
            vue: 'vue/dist/vue.esm-bundler.js',
        },
    },
    define: {
        __VUE_OPTIONS_API__: true,
        __VUE_PROD_DEVTOOLS__: false,
        __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: false,
    },
    test: {
        environment: 'jsdom',
        include: ['tests/js/**/*.test.js'],
        setupFiles: ['tests/js/setup.js'],
    },
});
