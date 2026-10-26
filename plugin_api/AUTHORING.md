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
| `settings` | Fields the core renders generically on your Add-on's page under Settings → Add-ons: `{key, type (string\|secret\|path\|bool), label (locale key), default}`. `secret` is shown, never returned, once saved — the same discipline as the core's own LLM keys. |
| `frontend` | `{script, stylesheet?, assets?, locales}` — paths relative to the Bundle root, all under `frontend/`. |
| `nav` | Optional sidebar entry: `{label (locale key), icon (inline SVG), route, view}`. |
| `slots` | Which of the four Slots this Add-on contributes to (see below). |

## Permissions (Berechtigungen)

Declared in `plugin.json`'s `permissions`; the consent dialog shows each as
one plain sentence before the Add-on's first activation, and again for any
new permission an update declares. The list is closed at seven:

| Permission | Unlocks | Durchgesetzt (enforced) or erklärt (declared only) |
|---|---|---|
| `library.read` | `api.library` — read Items, abstracts, full texts, Collections, change events | **Enforced**: `None` unless declared |
| `library.write` | `api.library.create_by_doi(...)` — add Items to the library (needs `library.read` too, whose handle carries the method) | **Enforced**: the method raises `PermissionError` unless declared |
| `llm` | `api.llm` — the user's configured LLM connections (costs them money) | **Enforced** |
| `settings.core` | `api.settings.core()` — the polite mailto, the OpenAlex key, the UI language, the base folder | **Enforced** |
| `network` | talking to external services on your own | Declared only — in-process code can always open a socket |
| `files` | nothing yet — reserved; `api.files` is always `None` | **Reserved**: declaring it is valid, it grants nothing |
| `storage` | nothing yet — reserved; `api.storage` is always `None` | **Reserved**: declaring it is valid, it grants nothing |

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
`template/__init__.py` shows the pattern (`if api.library is not None: ...`).
"Reserved" (`files`, `storage`) means the name is part of the closed list
and declaring it keeps your Manifest valid, but the core has no service
behind it yet: the attribute is always `None`. Keep your own data in your
own files under a folder setting (a `path` setting in your Manifest).

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

## The library: methods

Everything on `api.library` except `create_by_doi` reads and needs only
`library.read`. Items are addressed by their stored Cite Key.

| Method | Returns |
|---|---|
| `get_reference(citekey)` | `{citekey, id, title, authors, year, journal, doi, abstract, has_pdf, volume, issue, pages}` or `None` — `id` is the Item id (core route `/paper/{id}`), `has_pdf` says whether its PDF is in the library, `volume`/`issue`/`pages` are `None` when the Item's metadata does not know them (since 2.3) |
| `search_references(query, limit=20)` | a list of those dicts (semantic ranking when the user has embeddings, lexical otherwise) |
| `get_abstract(citekey)` / `get_full_text(citekey)` | a string or `None` — many Items have no extractable text; degrade to the abstract |
| `export_bibtex()` | the whole library as one `.bib` string |
| `search_passages(query, limit=10)` | text passages ranked like the core's passage search: `[{citekey, title, year, text, page_start, page_end, printed_page_start, printed_page_end, score}]` — `text` is the whole passage (since 2.5) |
| `get_passages(citekey, offset=0, limit=20)` | `{citekey, total, passages: [{index, text, page_start, page_end, printed_page_start, printed_page_end}]}` in reading order; `None` for an unknown key, `total: 0` = no extracted text (since 2.5) |
| `list_collections()` | every Collection — active, archived **and deleted** — by name (since 2.1) |
| `get_collection(collection_id)` | one Collection or `None` (since 2.1) |
| `get_collection_refs(collection_id)` | its Items as Cite Keys, oldest addition first; `[]` for an unknown id (since 2.1) |
| `export_collection_bibtex(collection_id)` | its Items as one `.bib` string, keyed by the same Cite Keys (since 2.1) |
| `on_change(callback)` | subscribes to change events; returns the unsubscriber |

**Pages** (since 2.5): `page_start`/`page_end` are 1-based PDF pages
(`None` when unknown). `printed_page_start`/`printed_page_end` are the pages
printed on the work: the PDF's own page label, else the Item's metadata
`pages` range when its length equals the PDF's page count, else `None`. The
core never guesses one — when it is `None`, cite the PDF page and say that it
is one. Guard both passage methods with `getattr(api.library,
"search_passages", None)` for hosts older than 2.5.

A **Collection** is a hand-curated list of Items, shaped
`{id, name, description, archived, archived_at, deleted, deleted_at,
created_at, updated_at, item_count}` (timestamps `YYYY-MM-DD HH:MM:SS`,
UTC). When the user deletes one, the core keeps a *tombstone*: it vanishes
from every list the user sees, but these methods still return it with
`deleted: true` and its last membership frozen — so an Add-on that was off at
the time can still pick up the final state. Guard the Collection methods with
`getattr(api.library, "list_collections", None)` if you support hosts older
than contract 2.1.

**Change events** arrive as plain dicts; ignore any `type` you do not know.
A Collection event is

```python
{"type": "collection", "action": "created" | "renamed" | "updated" | "archived"
 | "restored" | "deleted" | "members_changed", "collection_id": 42}
```

(`updated` = description changed; `members_changed` also fires when a deleted
Item leaves a Collection). An event says *that* something changed, not what
it changed to — read the state back. Callbacks run on the core's writing
thread right after its commit: keep them quick (record, schedule, debounce),
expect bursts during an import, and unsubscribe in `deactivate()` (the core
also drops a forgotten subscription when it unloads the Add-on). In tests,
`InMemoryLibrary(collections=[...])` holds Collections with the same
semantics, and its `add_collection`, `rename_collection`,
`set_collection_description`, `archive_collection`, `delete_collection`,
`set_collection_refs` and `emit_collection` fire the same events.

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
core, JSON in and out), `isActive(id)` / the `events.changed` custom event
and, since 2.6, `importBibtex(text, options)` and the PDF viewer
`components.PdfPreview` (below).

### The Markdown editor (contract 2.4)

Notes are written in the core's WYSIWYG Markdown editor; reuse it instead of
a `<textarea>` so the user gets the same formatting, citations and links
everywhere:

```js
const { MarkdownEditor } = window.LocalBib.components;
// in a component: components: { MarkdownEditor }
// <markdown-editor v-model="draft.body" :resolvers="resolvers"
//                  placeholder="…" :readonly="false" @blur="save" />
```

| Prop / event | Meaning |
|---|---|
| `v-model` (`modelValue`) | The Markdown text. `update:modelValue` fires **only after the user edited**; a text that was loaded and not touched is never handed back changed, and an edit that ends where it started hands back the original bytes. Saving on `update:modelValue` therefore never rewrites a user's file just because it was opened. |
| `readonly` | Shows the text with live chips, no editing. |
| `placeholder` | Shown in an empty block. |
| `resolvers` | Optional. `{ wikilink: { search(q) → [{id, title, kindLabel?}] or a Promise, resolve(id) → {title, href, missing, kindLabel?} or null (or a Promise), title?: 'header of the [[ list' } }`. Without it, `[[id]]` stays a neutral chip showing its id. Citations need nothing: the core resolves them against the library. |
| `focus` / `blur` / `ready` | Focus entered / left the editor (a blur hands out pending typing first); the editor finished loading. The component's `flush()` method does the same for a save on leave. |

The syntax is plain Markdown plus two inline forms that stay readable outside
LocalBib (Pandoc): citations `[@citekey]`, `[@citekey, S. 12]`, `[@a; @b]` —
the chip opens the Item, with a page locator the PDF on that page — and
wikilinks `[[id]]` / `[[id|Alias]]`. Inside code both stay text. Typing `@`
searches the library, `[[` your `resolvers.wikilink.search`; `/` opens the
block menu. A renamed citekey is shown as missing, never rewritten.

For a read view outside the editor, `LocalBib.renderMarkdown(text, opts)`
returns sanitised HTML with the same chips (`opts.wikilink(id) → {title,
href, missing}`, synchronous, resolves wikilinks); `LocalBib.hydrateMarkdown(el)`
labels the citation chips inside `el` from the library ("Müller 2021") and
makes them open their Item.

### Handing BibTeX to the import (contract 2.6)

An Add-on that found entries the library lacks — say, in a `.bib` file of a
user's project — hands them to the core's own BibTeX import instead of
creating Items itself:

```js
if (typeof window.LocalBib.importBibtex === 'function') {   // a core before 2.6 has none
  window.LocalBib.importBibtex(bibtexText, { collection: 42, filename: 'old.bib' });
}
```

| Argument | Meaning |
|---|---|
| `text` | The BibTeX text, e.g. a cut-out of the entries you found. Keys stay as written: a new entry keeps its key (made unique only if the library already uses it for another Item); an entry the library already holds (same DOI, else a close title) is matched and keeps the library's key, never duplicated. |
| `collection` | Optional Collection id to preselect as the import's target. Only an active Collection is preselected; `null`/absent, or one that is not available, leaves the choice to the user. |
| `filename` | Optional name the import shows and records as the source (default `import.bib`; `.bib` is added when missing). |

It returns `true` when the text was handed over and `false` for an empty
text. The text stays in the page's memory only. The core opens its import
page (`/import`) with the BibTeX dialog: with a preselected Collection the
preview runs at once, otherwise the user picks the target first. **Nothing is
created until the user confirms there** — every Item of the import, new and
matched, lands in the chosen Collection, like a `.bib` the user picked
themselves. So, like the DOI import route below, it needs no `library.write`.
Hide your button when the function is missing (a core before 2.6).

To let the user pick a `.bib` file themselves instead, send them to
`/import?bibtex=1&collection=<id>`: the import page opens its BibTeX dialog
at once, with that Collection preselected (`collection` optional, as below).
A core before 2.6 ignores `bibtex` and shows the import page as it is.

### The PDF viewer (contract 2.6)

`window.LocalBib.components.PdfPreview` is the detail view's own PDF preview
as a component — the same viewer and the same frame around a passage:

```js
const { PdfPreview } = window.LocalBib.components;   // a core before 2.6 has none
// in a component: components: { PdfPreview }
// <pdf-preview :paper-id="id" :page="12" :find="passage.text" @state="onState" />
```

| Prop / event | Meaning |
|---|---|
| `paperId` | The Item id (`get_reference(...)["id"]`, or the lookup endpoint below). Another id loads that Item's PDF. |
| `page` | The PDF page (1-based, never the printed page) to show. |
| `find` | Optional passage text, as for the detail route's `?find=` below: the paragraph where it starts **on that page** is framed. Not found there, or not unambiguous, means the page only — never a guessed spot. |
| `state` | Emitted on every change: `{status, page, pages, found}`. `status` is `loading`, `ready`, `nopdf` (the Item has no PDF — show your own hint or none) or `failed`; `page`/`pages` follow the reader's scrolling; `found` is `true` (framed), `false` (page only) or `null` (nothing to look for). |

It fills its parent (give it a height, e.g. a flex child with
`min-height: 0`). Check `PdfPreview` in `LocalBib.components` before using it
and fall back to linking the detail route on an older core.

### Linking to a passage in an Item's PDF (contract 2.6)

The Item detail route takes query parameters that open its PDF preview at a
spot — e.g. for a passage that `search_passages`/`get_passages` returned:

```js
this.$router.push({ path: `/paper/${id}`, query: { page: 12, find: passage.text, back: '/yourid/page' } });
```

| Parameter | Meaning |
|---|---|
| `page` | The PDF page (1-based, the passage's `page_start` — never the printed page). The preview opens on it. |
| `find` | The passage text (its start is enough; the core reads only the first words). The core looks for where it starts **on that page** and frames the paragraph there, briefly pulsing. Not found there, or found more than once, means no frame — only the page jump. The core never guesses a spot. Needs `page`. |
| `back` | An internal route the detail view offers a way back to: a key "← Back to <label>" at the start of its head, `label` being the nav label of the route's area (your registered nav item for `/yourid/…`), else just "Back". Only a route starting with `/` — not `//`, no backslash, whitespace or scheme — counts; anything else is ignored and the head stays as it is. The key returns with `router.push(back)`: your view mounts again, so keep what it must restore (scroll position, an open selection) in `sessionStorage` yourself. |

A core before 2.6 ignores `find` and `back` and only goes to the page.
Without a PDF the Item opens without a preview; check `has_pdf` first
(`get_reference`, or the lookup endpoint below) if your link promises one.
Behind `find` is the core's `GET /api/papers/{id}/locate?page=&text=`
(`{page, rect}`, `rect` in page fractions or `null`); it serves the detail
view and is not a stable endpoint of its own — link to the route instead.

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
ADR-0021, and these app routes for a view to send the user to
(`this.$router.push(...)` or `<router-link>`) — anything else may change
release to release without notice:

| Category | Endpoint |
|---|---|
| Item list | `GET /api/papers` |
| Item by DOI | `POST /api/papers/by-doi`, `POST /api/papers/by-doi/batch` — creates; an Item with that DOI answers untouched (`created: false`) |
| DOI existence check | `POST /api/papers/by-doi/exists` — `{dois: [...]}` → `{papers: {<DOI as sent>: <Item id> or null}}`; reads only |
| BibTeX of an Item | `GET /api/papers/{id}/bibtex` |
| Item lookup (citations) | `GET /api/papers/lookup?q=&limit=` (title, authors, citekey, DOI; at most 50) or `?keys=<citekey>&keys=<citekey>` → `{papers: [{id, cite_key, title, authors, year, doi, has_pdf}]}`; light rows, reads only |
| OA-PDF fetch | `POST /api/papers/{id}/fetch-oa-pdf` |
| Model list | `GET /api/llm/models` |
| Item detail (route) | `/paper/{id}` — `id` is the Item id (`get_reference(...)["id"]`); since 2.6 with `?page=N&find=<passage>&back=<route>` (see "Linking to a passage") |
| Collection (route) | `/collection/{id}` |
| Import with a DOI list (route) | `/import?dois=<doi>&dois=<doi>&collection=<id>` — both parameters optional; pass one `dois` per DOI (a resolver link or `doi:` in front is fine), `collection` preselects the target Collection if it is an active one |
| BibTeX import dialog (route) | `/import?bibtex=1&collection=<id>` — since 2.6: opens the BibTeX dialog, the user picks the `.bib` file; `collection` optional as above |

The import route creates nothing by itself: the page shows the DOIs, previews
through the DOI existence check what is new and what the library already
holds, and creates only when the user confirms. Sending the user there is
how an Add-on hands over literature it found missing without adding Items
itself — it needs no `library.write` for that.

Pass each DOI as its own `dois` parameter: real DOIs contain commas and
semicolons (Wiley's `…3.0.CO;2-L`), and the page splits one parameter at a
comma or semicolon only where a new DOI starts. Only an active Collection is
preselected; an archived, deleted or unknown `collection` falls back to "No
collection", and the page tells the user so. A link without `collection`
selects "No collection", even if the page was open with another one.

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
- Settings: your `plugin.json`'s `settings` keys live in your own namespace
  automatically; you never see another Add-on's. `api.settings.set(key,
  value)` stores `str`, `bool` and `None` as they are, turns `int`/`float`
  into `str`, and raises `TypeError` for anything else — the core and the
  `plugin_api.testing` fake alike (`plugin_api.setting_value`).

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
(`InMemoryLibrary`, `DummyLlm`, `InMemorySettings`) plus
`make_api(permissions, ...)`, which assembles a `PluginApi` exactly the way
the core does — a service whose permission is not declared is `None`, even if
you pass one, and the reserved `files`/`storage` are always `None`.
(`NullFiles` and `TempSqliteStorage` stay importable for your own unit tests
of code that will use those services once they exist.) See
`localbib-addon-template/tests/test_template_addon.py` for the pattern.
