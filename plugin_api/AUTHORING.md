# Writing a LocalBib Add-on

This is the Add-on Contract as an author reads it: everything an Add-on may
rely on, what the core enforces versus merely records, and how to get a
Bundle from your machine into the Marketplace. The fastest way to a working
Bundle is cloning `localbib-addon-template`
(<https://github.com/tobiasbartlog/localbib-addon-template>) and following
its `README.md`; this document is the reference to come back to.

Vocabulary (Add-on, Bundle, Manifest, Marketplace-Index, Slot, Vertrauensstufe,
Berechtigung, Zustimmung, Dev-Suchpfad) follows the LocalBib domain glossary.
Contract version: `API_VERSION = 2` (`plugin_api.API_VERSION`).

## Bundle structure

A Bundle is a Zip (or, for development, a plain folder) with this layout:

```
plugin.json          # the Manifest — see below
<id>/                # your Python package, named after your Add-on id
  __init__.py         #   must expose a module-level `plugin` (see "Registration")
vendor/               # optional: vendored third-party dependencies
frontend/
  <script>.js          # exactly one script — your Add-on's one entry point
  <stylesheet>.css      # optional
  locales/
    en.json, de.json, ... # one file per language in plugin.json's "languages"
  <other assets>        # images, fonts, extra scripts (e.g. an editor mode)
schemas/              # optional: your own JSON Schemas, if you keep a Markdown
                       # archive with a schema-first record format
tests/                # your own tests (never shipped in the built Zip —
                       # `localbib-addon build` excludes it)
```

Installing a Bundle never needs `pip`, a compiler or the network beyond the
download itself — vendor anything you need under `vendor/`. A Bundle either
runs on any Python (`"python": "any"`) or names the exact CPython build tag it
was made for (`cp313-win_amd64`), because the packaged app is a specific
Python build and your native extension may not be.

**The standard library is complete.** The packaged app bundles the whole
standard library (only the GUI toolkit `tkinter`/`turtle`, the test suites,
`idlelib`, `ensurepip`, `venv` and `lib2to3` are left out), so anything under
`vendor/` may import any other stdlib module. Vendor third-party packages, never
stdlib modules.

**Bytecode for a build tag.** The packaged app never writes `.pyc` files, so
for a Bundle with a build tag `localbib-addon build` ships them: every `.py` in
the Zip — your package and `vendor/` — gets an unchecked-hash `.pyc` in the
usual `__pycache__/<name>.cpython-313.pyc` next to its source, which the import
system uses as is (the unpacked files' dates do not matter). Bytecode is tied
to the interpreter, so the build must run on the CPython version the tag names
(the build workflow does this for you, see "Submitting to the Marketplace-Index") and
refuses otherwise; `--no-bytecode` ships sources only, at the cost of
recompiling them on every start of the app. A `vendor/` file that does not
compile ships as source with a warning; a syntax error in your own package
fails the build. `"python": "any"` Bundles get no bytecode.

**A vendor lock instead of a committed `vendor/`.** Compiled wheels do not
belong in your source repository. Put a hash-pinned requirements file named
after the build tag next to `plugin.json` —
`requirements-vendor-cp313-win_amd64.txt`, one `name==version
--hash=sha256:…` line per package, *including* every dependency (it is
installed with `--no-deps --require-hashes`). The build workflow then installs
exactly those wheels for that interpreter and platform into `vendor/` and
writes the tag into the built Manifest's `python`. Keep `"python": "any"` in
your source `plugin.json`: the source tree stays loadable as a Dev-Suchpfad on
any interpreter that has the libraries installed, while the released Zip is
offered only to the app build it was made for. One lock per Bundle.

## Manifest fields (`plugin.json`)

Validated against `manifest.schema.json` (`plugin_api.validate_manifest`);
`localbib-addon check` runs this plus the structural and namespace checks
below. Required fields: `id`, `name`, `version`, `author`, `license`,
`tagline`, `languages`, `default_language`, `api_version`, `min_core`,
`python`, `permissions`, `frontend`.

| Field | Meaning |
|---|---|
| `id` | Your Add-on id: `^[a-z][a-z0-9_]{1,39}$`. Also the Python package name, the route segment, and the namespace prefix of every view key, Slot key, locale key and CSS class (see "Namespace rules"). Chosen once — changing it later is a new Add-on to the Marketplace. |
| `name`, `tagline` (≤ 80 chars), `description` (Markdown) | What the Marketplace card and slide-over show. |
| `version` | Semver. The tag your build workflow releases must be exactly `v<version>`. |
| `author`, `license` (SPDX expression), `homepage` | Attribution and the linked source repository the index CI checks is reachable. |
| `languages`, `default_language` | ISO 639-1 codes. A user whose UI language is not in `languages` sees `default_language`, never key paths. |
| `api_version` | The Add-on Contract version you built against (`plugin_api.API_VERSION`, currently `2`; the core accepts only `2`). |
| `min_core` | Semver floor on the LocalBib core version (checked only in the packaged app, which carries a version; a source checkout has none). |
| `python` | `"any"` for a pure Bundle, otherwise a CPython build tag. |
| `permissions` | A subset of the seven Berechtigungen — see below. |
| `settings` | Fields the core renders generically in your Marketplace settings section: `{key, type (string\|secret\|path\|bool), label (locale key), default}`. `secret` is shown, never returned, once saved — the same discipline as the core's own LLM keys. |
| `frontend` | `{script, stylesheet?, assets?, locales}` — paths relative to the Bundle root, all under `frontend/`. |
| `nav` | Optional sidebar entry: `{label (locale key), icon (inline SVG), route, view}`. |
| `slots` | Which of the four Slots this Add-on contributes to (see below). |

## Permissions (Berechtigungen)

Declared in `plugin.json`'s `permissions`; the consent dialog shows each as
one plain sentence before the Add-on's first activation, and again for any
new permission an update declares. The list is closed at seven:

| Permission | Unlocks | Durchgesetzt (enforced) or erklärt (declared only) |
|---|---|---|
| `library.read` | `api.library` — read Items, abstracts, full texts | **Enforced**: `None` unless declared |
| `library.write` | `api.library.create_by_doi(...)` — add Items to the library (needs `library.read` too, whose handle carries the method) | **Enforced**: the method raises `PermissionError` unless declared |
| `llm` | `api.llm` — the user's configured LLM connections (costs them money) | **Enforced** |
| `settings.core` | `api.settings.core()` — the polite mailto, the OpenAlex key, the UI language, the base folder | **Enforced** |
| `network` | talking to external services on your own | Declared only — in-process code can always open a socket |
| `files` | `api.files` — watch your own folders under the library | **Enforced** |
| `storage` | `api.storage` — your own SQLite database (`api.storage.open_plugin_db(name)`) | **Enforced** |

"Enforced" means the core hands over the matching `PluginApi` attribute (see
`plugin_api.GATED_SERVICES`) only when you declared the permission; otherwise
it is `None` and your Add-on must degrade (`library.write` is the one
permission without an attribute of its own: it unlocks the writing method on
`api.library`, and the Manifest check refuses it without `library.read`).
"Declared only" means the core cannot check it — it is your statement to
the user, not a sandbox. Enforcement covers the Python contract; your
frontend runs in the app's page and reaches the stable REST endpoints below
either way, so declare `library.write` also when only your frontend adds
Items. Ask for only what you use: `localbib-addon-template`'s
`template/__init__.py` shows the pattern (`if api.storage is not None: ...`).

`create_by_doi(doi, *, title="", authors=None, year=None, journal="",
abstract="")` is the same intake as `POST /api/papers/by-doi`: an Item with
that DOI is returned untouched (`created: false`), a new one gets your
metadata, the core fills empty fields from CrossRef/OpenAlex and tries an
Open-Access PDF, and records your Add-on's id as the Item's origin. It
returns `{doi, created, paper_id, citekey, pdf}` (`pdf` is `fetched` or
`none`), raises `ValueError` for an empty DOI, and blocks on the network —
call it from a sync route or a worker thread. In tests,
`plugin_api.testing.InMemoryLibrary` implements it without the network and
`make_api` applies the same gate.

## Registration and Slots

Your one frontend script calls the global registration function exactly
once, synchronously, while it loads:

```js
window.LocalBib.registerPlugin({
  id: 'yourid',
  views: { 'yourid.main': { template: '...' } },      // Vue component objects
  subroutes: [{ path: '/yourid/detail/:id', view: 'yourid.detail' }],
  slots: { 'item-detail-aside': { key: 'yourid.aside', component: { ... } } },
  locales: { /* optional: inline catalogs, override the shipped files key by key */ },
});
```

`window.LocalBib` also gives you `t(key, vars)` / `tn(key, n, vars)` (your
locale catalog under your namespace), `api(url, options)` (fetch against the
core, JSON in and out) and `isActive(id)` / the `events.changed` custom event.

The four Slots, each a Vue component with fixed props:

| Slot | Props | Contract |
|---|---|---|
| `item-list-filter` | `selection` (v-model) | Emits `update:selection` with `null` or `{value, label, citeKeys}`; the core intersects `citeKeys` into the Item list's existing `cite_keys` parameter. |
| `item-detail-aside` | `itemId`, `citeKey` | Renders alongside an Item's detail view. |
| `research-chat-context` | `enabled` (v-model), `context` (v-model) | Your own toggle; while `enabled`, `context` (a string or `null`) is merged into the Research Chat's existing `extra_context`. |
| `settings` | — | Optional: only needed if your Add-on wants a custom settings UI instead of the core's generic rendering of `plugin.json`'s `settings` field. |

`localbib-addon-template`'s `frontend/template.js` is a complete, minimal
working example of a view and an `item-detail-aside` Slot.

## Stable core endpoints

The Add-on Contract guarantees these core REST endpoints for a frontend to
call directly (`window.LocalBib.api(...)`), in the categories fixed by
ADR-0021 — anything else may change release to release without notice:

| Category | Endpoint |
|---|---|
| Item list | `GET /api/papers` |
| Item by DOI | `POST /api/papers/by-doi`, `POST /api/papers/by-doi/batch`, `POST /api/papers/by-doi/exists` |
| BibTeX of an Item | `GET /api/papers/{id}/bibtex` |
| OA-PDF fetch | `POST /api/papers/{id}/fetch-oa-pdf` |
| Model list | `GET /api/llm/models` |

One of the maintainer's own official Add-ons, predating this contract as the
migration source for what used to be core-only features, also still relies
on a few broader endpoints that are not (yet) part of the stable guarantee —
documented here so this list stays honest about what is actually in use, not
just what is promised: `POST /api/papers/bulk-delete`,
`GET /api/export/bibtex`, `POST /api/import/bibtex/preview`,
`POST /api/import/bibtex/commit`, `POST /api/import/bibtex/oa-check`. A
third-party Add-on should not depend on these; a development-repo test keeps
this whole table in sync with what the official Add-ons' frontends actually
call, so it cannot silently drift.

Every route *you* register must live under `/api/plugins/<your id>/` — the
core refuses to activate an Add-on whose router escapes that prefix.

## Design tokens

Use the core's CSS custom properties instead of hard-coded colours or timing,
so your Add-on matches both themes automatically:

- **Chart tokens**: `--lb-chart-1` … `--lb-chart-8` (the fixed, colour-vision-
  deficiency-checked series order), `--lb-chart-other` (remainder bucket),
  `--lb-chart-unassigned` (a named "not assigned" zone).
- **Motion tokens**: `--lb-ease-out` (the one easing curve — fast start, long
  settle, no bounce).
- General layout/spacing/colour tokens (`--space-*`, `--text-muted`, etc.) as
  used in `localbib-addon-template`'s `frontend/template.css`.

## Locale rules

One JSON file per language under `frontend/locales/`, declared in
`plugin.json`'s `frontend.locales`. Every key must start with `<your id>.`
(`localbib-addon check` fails otherwise). If a user's UI language is not
among your `languages`, they see your `default_language`'s catalog, never a
key path; within a language you do ship, a key missing from *that* language
but present in another fails `localbib-addon check`'s locale-parity check —
ship complete catalogs. Add-on locale texts are never merged into the core's
own `core.en.js`/`core.de.js`.

## Namespace rules

The core enforces these at load time (a violation fails activation, and
`localbib-addon check` catches most of them before you ever run the core):

- Router prefix: every route under `/api/plugins/<id>/`.
- View keys, Slot keys and `nav.view`: prefixed `<id>.`.
- `nav.route` and every subroute: `/​<id>` or `/<id>/...`.
- Locale keys: prefixed `<id>.` in every shipped language.
- CSS classes: prefixed `<id>-` (a `localbib-addon check` warning, not an
  error — static analysis of a stylesheet cannot always tell).
- Settings and storage: your `plugin.json`'s `settings` keys and
  `api.storage.open_plugin_db(name)` databases live in your own namespace
  automatically; you never see another Add-on's.

## Dev-Suchpfad (development path)

Point `LOCALBIB_PLUGIN_DEV_PATHS` (an environment variable, `os.pathsep`-
separated for more than one) at your Bundle folder, or use the Marketplace's
"Aus Ordner laden" to do the same without an environment variable. The core
loads it exactly as it would an installed Bundle — same namespace
enforcement, same Slots, same consent dialog — except it is shown under
**Entwicklung**, is never auto-updated from the index, and needs no packaging
step. This is how the maintainer runs the official Add-ons during
development, and how `tests/test_addon_template.py` proves the template
Bundle loads.

## Submitting to the Marketplace-Index

1. `localbib-addon build . --out dist` — a deterministic Zip, its SHA-256
   checksum and an index snippet (`dist/<id>-<version>.index.json`).
2. Tag a release `v<version>` matching `plugin.json`; the build workflow
   shipped with `localbib-addon-template` (and with every official Add-on)
   attaches the Zip, checksum and snippet to the GitHub Release
   automatically — a checksum is never typed by hand. It builds on the
   Python your build tag names (the vendor lock's, else `python`:
   `cp313-…` → 3.13; `any` → 3.13), so the shipped bytecode matches the
   app's interpreter, and fills `vendor/` from the lock first.
3. Open a pull request against `localbib-plugins`
   (<https://github.com/tobiasbartlog/localbib-plugins>) adding your entry to
   `index.json`, following its `PULL_REQUEST_TEMPLATE.md`. Its CI
   (`checks/validate_submission.py`) checks the index schema, that your
   release asset and `homepage` are reachable, the artifact's checksum, the
   Manifest inside the Zip against your index entry, `localbib-addon check`
   on the unpacked Bundle, and that `license` is set.
4. Every Add-on submitted this way is shown as **Drittanbieter**
   (third-party), never as **Offiziell** — the review checks the shape of
   your submission, never its behaviour or trustworthiness. An Add-on runs
   with all of LocalBib's own rights on the user's machine; the consent
   dialog says so, first for a Drittanbieter Add-on.

## Testing without the core

`plugin_api.testing` ships fakes for every gated service
(`InMemoryLibrary`, `DummyLlm`, `InMemorySettings`, `NullFiles`,
`TempSqliteStorage`) plus `make_api(permissions, ...)`, which assembles a
`PluginApi` exactly the way the core does — a service whose permission is not
declared is `None`, even if you pass one. See
`localbib-addon-template/tests/test_template_addon.py` for the pattern.
