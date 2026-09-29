// hello — frontend of the fixture Add-on (Add-on Contract 2).
// One script per Bundle. Everything it registers lives under the id:
// view keys and slot keys start with "hello.", routes with "/hello/",
// locale keys with "hello.", CSS classes with "hello-".
//
// The core loads this file before it mounts its router and expects exactly
// one synchronous call to LocalBib.registerPlugin while it runs. Texts come
// from the locale files next to it (frontend/locales/*.json) through $t.
(function () {
  'use strict';
  var LB = window.LocalBib;
  if (!LB || typeof LB.registerPlugin !== 'function') return;

  LB.registerPlugin({
    id: 'hello',
    views: {
      // The nav item of the Manifest ("route": "/hello") points here.
      'hello.main': {
        template:
          '<section class="hello-view">' +
          '<h1 class="hello-title">{{ $t("hello.view.title") }}</h1>' +
          '<p class="hello-intro">{{ $t("hello.view.intro") }}</p>' +
          '<router-link class="hello-link" to="/hello/greeting/42">{{ $t("hello.view.open") }}</router-link>' +
          '</section>'
      },
      'hello.greeting': {
        template:
          '<section class="hello-view">' +
          '<h1 class="hello-title">{{ $t("hello.greeting.title", { id: $route.params.id }) }}</h1>' +
          '<router-link class="hello-link" to="/hello">{{ $t("hello.greeting.back") }}</router-link>' +
          '</section>'
      }
    },
    subroutes: [
      { path: '/hello/greeting/:id', view: 'hello.greeting' }
    ],
    // The four Slots. "settings" is occupied without a component: the core
    // renders the fields declared in plugin.json generically.
    slots: {
      // Item list filter: offers options, hands the core a cite-key set.
      'item-list-filter': {
        key: 'hello.filter',
        component: {
          props: ['selection'],
          emits: ['update:selection'],
          computed: {
            current: function () { return this.selection ? this.selection.value : ''; }
          },
          methods: {
            choose: function (ev) {
              var value = ev.target.value;
              this.$emit('update:selection', value === 'greeted'
                ? { value: 'greeted', label: LB.t('hello.filter.greeted'), citeKeys: ['hello2024', 'hello2025'] }
                : null);
            }
          },
          template:
            '<label class="hello-filter">' +
            '<span class="lb-field-label">{{ $t("hello.filter.label") }}</span>' +
            '<select class="lb-input hello-filter-select" :value="current" @change="choose">' +
            '<option value="">{{ $t("hello.filter.all") }}</option>' +
            '<option value="greeted">{{ $t("hello.filter.greeted") }}</option>' +
            '</select></label>'
        }
      },
      // Item detail aside: gets the Item id and its cite key.
      'item-detail-aside': {
        key: 'hello.aside',
        component: {
          props: ['itemId', 'citeKey'],
          template: '<aside class="hello-aside" :data-item-id="itemId">{{ $t("hello.aside.label") }} {{ citeKey }}</aside>'
        }
      },
      // Research Chat context: its own toggle; while on, one text block.
      'research-chat-context': {
        key: 'hello.chat',
        component: {
          props: ['enabled'],
          emits: ['update:enabled', 'update:context'],
          methods: {
            flip: function () {
              var on = !this.enabled;
              this.$emit('update:enabled', on);
              this.$emit('update:context', on ? LB.t('hello.chat.block') : null);
            }
          },
          template:
            '<label class="hello-chat">' +
            '<input type="checkbox" class="hello-chat-toggle" :checked="enabled" @change="flip" /> ' +
            '{{ $t("hello.chat.label") }}</label>'
        }
      }
    }
  });
})();
