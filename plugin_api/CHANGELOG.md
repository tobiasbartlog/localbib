# Changelog — localbib-plugin-api

The package version follows semver; its major number is the Add-on Contract
version (`plugin_api.API_VERSION`). A core accepts exactly one contract
version; an Add-on built against another one is shown as incompatible.

## 2.6.0 — BibTeX into the core's import, the PDF viewer, links to a passage

Added (additive, `API_VERSION` stays `2`; frontend contract, no Python API
change):

- `window.LocalBib.importBibtex(text, { collection, filename })` hands BibTeX
  text to the core's import: the import page opens its BibTeX dialog with the
  text as the `.bib` file, previews it (at once when `collection` names an
  active Collection, which is preselected as the target; otherwise after the
  user picks one) and creates only when the user confirms — new entries keep
  their keys, entries the library holds are matched, and all of them land in
  the chosen Collection. The text stays in memory. Returns `true`, or `false`
  for an empty text. Feature-check it
  (`typeof LocalBib.importBibtex === 'function'`) for cores before 2.6.
- The Item detail route `/paper/{id}` takes `?page=N&find=<passage text>`:
  the PDF preview opens on PDF page `N` and frames the paragraph where the
  passage starts on that page — no frame when it is not found there or not
  unambiguous, never a guessed spot. `?back=<route>` puts a key "← Back to
  <label>" at the start of the detail head (`label` = the nav label of the
  route's area, else "Back") that returns there with `router.push`; only an
  internal route counts (starts with `/`, not `//`, no backslash, whitespace
  or scheme), anything else is ignored. A core before 2.6 ignores `find` and
  `back` and only goes to the page.
- `window.LocalBib.components.PdfPreview` — the detail view's PDF preview as
  a component: props `paperId`, `page`, `find` (frames the passage's
  paragraph on that page like `?find=`, the page only when it is not found),
  event `state` `{status: loading|ready|nopdf|failed, page, pages, found}`.
  Feature-check it in `LocalBib.components` for cores before 2.6.
- The import route takes `?bibtex=1` (with the optional `collection=<id>`):
  the BibTeX dialog opens at once, for a `.bib` file the user picks.

## 2.5.0 — Passages with page numbers

Added (additive, `API_VERSION` stays `2`; needs only `library.read`):

- `LibraryApi.search_passages(query, limit=10)` — the library's text
  passages ranked like the core's passage search (hybrid semantic + BM25,
  BM25 alone without embeddings): `[{citekey, title, year, text, page_start,
  page_end, printed_page_start, printed_page_end, score}]`. `text` is the
  whole passage, not an excerpt.
- `LibraryApi.get_passages(citekey, offset=0, limit=20)` — one Item's
  passages in reading order: `{citekey, total, passages: [{index, text,
  page_start, page_end, printed_page_start, printed_page_end}]}`; `None` for
  an unknown Cite Key, `total: 0` when the Item has no extracted text.
- `page_*` are 1-based PDF pages; `printed_page_*` is the page printed on the
  work — the PDF's page label, else the metadata `pages` range when its
  length equals the PDF's page count — and `None` when neither is reliable.
  The core never guesses a printed page. Guard both methods with
  `getattr(library, "search_passages", None)` for hosts before 2.5.
- `plugin_api.testing.InMemoryLibrary` takes `passages=[...]` per reference
  and answers both methods in the same shape.

## 2.4.0 — The Markdown editor for Add-ons

Added (additive, `API_VERSION` stays `2`; frontend contract, no Python API
change):

- `window.LocalBib.components.MarkdownEditor` — the core's WYSIWYG Markdown
  editor as a Vue component (`v-model`, `readonly`, `placeholder`,
  `resolvers`, events `focus`/`blur`/`ready`, method `flush()`). It emits
  only after an edit, so saving on `update:modelValue` never rewrites an
  untouched file. Citations `[@key, S. 12]` resolve against the library;
  wikilinks `[[id|Alias]]` through the Add-on's `resolvers.wikilink`.
- `window.LocalBib.renderMarkdown(text, opts)` — sanitised HTML with the
  same citation/wikilink chips; `window.LocalBib.hydrateMarkdown(el)` labels
  and links the citation chips of a rendered element.
- Stable core endpoint `GET /api/papers/lookup` (`q`/`limit` or repeatable
  `keys`) → `{papers: [{id, cite_key, title, authors, year, doi, has_pdf}]}`.

## 2.3.0 — Reference details for literature checks

Added (additive, `API_VERSION` stays `2`):

- `LibraryApi.get_reference` (and every dict `search_references` returns)
  carries `has_pdf` (bool — the Item's PDF is in the library), `volume`,
  `issue` and `pages` (str or `None`; the core fills them from what the Item's
  stored metadata knows, e.g. a BibTeX import). `id` (the Item id, for the
  core's Item route `/paper/{id}`) is now documented as part of the answer.
  Add-ons that read only the old keys are unaffected.
- `plugin_api.testing.InMemoryLibrary` answers the same shape: missing keys
  of a seeded reference are filled with defaults (`id` stable per Cite Key,
  `has_pdf` False, the rest `None`).

## 2.2.0 — Reserved permissions, one settings value rule

Changed (`API_VERSION` stays `2`):

- `files` and `storage` are **reserved** Berechtigungen: declaring them stays
  valid (existing Manifests keep loading), but they grant nothing. The core
  never had a host service behind them, so `api.files` and `api.storage`
  were always `None` in the core; the docs called them "Enforced" and the
  fakes handed over `NullFiles` / `TempSqliteStorage`. Now the docs, the
  consent dialog and `plugin_api.testing.make_api` agree with the core: both
  attributes are always `None`, whatever is declared or passed.
- `PermissionInfo` gains `reserved: bool = False` (additive); `files` and
  `storage` are `enforced=False, service=None, reserved=True` and no longer
  appear in `GATED_SERVICES`. The Marketplace's permission rows carry
  `reserved`, shown as "Reserved — grants nothing yet".
- `NullFiles` and `TempSqliteStorage` stay importable for an Add-on's own
  unit tests.
- `SettingsApi.set` has one value rule, in the core and in
  `plugin_api.testing.InMemorySettings` alike (new `plugin_api.setting_value`):
  `str`, `bool` and `None` are stored as they are; `int` and `float` become
  their `str` (what the core always kept on disk; the fake used to keep the
  number); anything else (list, dict, bytes, ...) raises `TypeError` and
  stores nothing (the core used to accept it and lose it as `None` on the
  next load). Store structured data as a JSON string of your own.

## 2.1.0 — Collections

Added (additive, `API_VERSION` stays `2`):

- `LibraryApi` reads Collections under `library.read` — no new permission:
  `list_collections()` (active, archived and tombstoned, with `deleted` /
  `deleted_at`), `get_collection(id)`, `get_collection_refs(id)` (the Items
  as stored Cite Keys, oldest addition first) and
  `export_collection_bibtex(id)`. A Collection is `{id, name, description,
  archived, archived_at, deleted, deleted_at, created_at, updated_at,
  item_count}`. Guard with `getattr(library, "list_collections", None)` for
  hosts before 2.1.
- `on_change` now delivers Collection events
  `{"type": "collection", "action": ..., "collection_id": int}` with the
  actions in the new `COLLECTION_EVENT_ACTIONS` (`created`, `renamed`,
  `updated`, `archived`, `restored`, `deleted`, `members_changed`). Before
  2.1 the core's `on_change` never fired.
- `plugin_api.testing.InMemoryLibrary` takes `collections=[...]` and gains
  `add_collection`, `rename_collection`, `set_collection_description`,
  `archive_collection`, `delete_collection`, `set_collection_refs` and
  `emit_collection`, which fire the same events as the core.
- `localbib-addon build` precompiles bytecode when the Manifest names a
  CPython build tag (`"python": "cp313-win_amd64"`): every `.py` in the Zip,
  the Add-on's own package and `vendor/`, gets an unchecked-hash `.pyc`
  (PEP 552) in the usual `__pycache__/<name>.cpython-313.pyc` place, next to
  its source. The app's frozen exe never writes bytecode, so without it an
  installed Bundle recompiled its whole stack on every start. The build must
  run on the CPython minor the tag names and refuses otherwise;
  `--no-bytecode` ships sources only. `"python": "any"` never gets bytecode.
  A `vendor/` file that does not compile ships as source with a warning; one
  in the Add-on's own code fails the build. Builds stay byte-identical.
  `BuildResult` gains `bytecode_files` and `warnings`; `BuildError` is new.
- The core promises the complete standard library to every Bundle: the
  release exe bundles all of it (minus GUI, test suites and CPython's
  internal test modules), so a vendorised package may import any stdlib
  module.

## 2.0.0 — Contract 2: the Bundle era

Breaking:

- `API_VERSION` is `2`. Contract 1 is no longer supported by the core; an
  Add-on declares `api_version: 2` in its `plugin.json`
  (`PluginManifest.api_version = 2` in Python, now the default).
- `PluginApi` gains `files`, `storage` and `settings`. The gated services
  (`library`, `llm`, `files`, `storage`) are handed over **only** when the
  Manifest declares the matching permission, and are `None` otherwise.
- `SettingsApi` gains `core() -> CoreSettings | None`: the named core read set
  (polite mailto, OpenAlex key, UI language, base folder), answered only with
  `settings.core` declared. `get`/`set` stay in the Add-on's own namespace.

Added:

- `PluginManifest` carries the Manifest fields additively: `author`,
  `license` (SPDX), `homepage`, `tagline` (<= 80 chars), `description`
  (Markdown), `languages`, `default_language`, `min_core`, `python` (`any` or
  a build tag such as `cp313-win_amd64`), `permissions`, `settings`,
  `frontend`, `nav`, `slots`. New value types `SettingField`, `FrontendSpec`.
- The seven permissions as a closed enum `Permission` (`library.read`,
  `library.write`, `llm`, `settings.core`, `network`, `files`, `storage`) and
  `PERMISSIONS`, which marks each as enforced (the core gates a service or
  method on it) or declared only (`network`).
- `LibraryApi.create_by_doi(doi, *, title, authors, year, journal, abstract)
  -> {doi, created, paper_id, citekey, pdf}`: the core's DOI intake (dedup by
  DOI, enrichment, best-effort Open-Access PDF, origin = the Add-on id) for an
  Add-on's Python. It is the one writing method and is gated by
  `library.write`, which is therefore enforced: without it the call raises
  `PermissionError`. `validate_manifest()` refuses `library.write` without
  `library.read`, whose `library` handle carries the method.
  `testing.InMemoryLibrary` implements it offline (`created`,
  `write_allowed`), and `make_api` applies the same gate.
- `manifest.schema.json` (JSON Schema 2020-12) for `plugin.json`, and a
  stdlib-only `validate_manifest()` / `load_manifest()` that interpret it —
  no `jsonschema` needed at runtime (extra `[schema]` for cross-checks).
- `SLOTS`: the four core UI slots (`item-list-filter`, `item-detail-aside`,
  `research-chat-context`, `settings`).
- Slots (frontend): `LocalBib.registerPlugin({slots: {'<slot>': {key:
  '<id>.…', component}}})` fills one of the four; any other slot name rejects
  the registration. The core renders a slot only while its Add-on is active:
  - `item-list-filter` — props `{selection}`, emits `update:selection` with
    `{value, label, citeKeys: string[]} | null`. The cite keys go to the
    existing `cite_keys` parameter of `GET /api/papers` (intersected across
    sources; empty = no Items); each selection counts in the filter badge and
    is cleared by "reset" and when the Add-on is switched off.
  - `item-detail-aside` — props `{itemId, citeKey}`; keyed on the Item id, so
    it remounts on navigation to another Item.
  - `research-chat-context` — props `{enabled}`, emits `update:enabled`
    (boolean; the component draws its own toggle) and `update:context`
    (the text block, or null). While enabled, a non-empty block is appended
    to the existing `extra_context` of `POST /api/research-chat/ask`.
  - `settings` — props `{addonId, fields, values, save}` (`save(values)` PUTs
    `/api/plugins/{id}/settings`, `null` = unchanged, and resolves to the new
    values). Without a component the core renders the Manifest's declared
    fields generically (`string`, `secret` shown only as `key_hint`, `path`,
    `bool`); the label is the field's `label` key, else
    `<id>.settings.<key>`, else the key.
  Slot texts come from the Add-on's own locale files (`<id>.` keys), in the
  UI language or else the Add-on's `default_language`.
- Stable design tokens an Add-on stylesheet may rely on (both themes):
  `--lb-chart-1` … `--lb-chart-8`, `--lb-chart-other`,
  `--lb-chart-unassigned` (the neutral name of the former untopiced chart
  token), `--lb-dur-1` … `--lb-dur-4`, `--lb-ease-out`. Every other custom
  property may change without notice.
- `plugin_api.testing`: fakes to test an Add-on without the core —
  `InMemoryLibrary`, `DummyLlm` (canned answers, optional deterministic
  embeddings), `InMemorySettings`, `NullFiles`, `TempSqliteStorage`, and
  `make_api(permissions, ...)`, which assembles a `PluginApi` with undeclared
  services set to `None`.
- Standalone packaging (`pyproject.toml`), MIT.
- The `localbib-addon` CLI (extra `[dev]`, stdlib + this package only):
  `check <folder>` validates a Bundle's Manifest, structure, namespace
  prefixes (`nav.view`/`nav.route`, locale keys), locale parity per shipped
  language, a CSS class-prefix check and a Python build-tag plausibility
  check (warnings, never blocking, where static analysis cannot be sure);
  `build <folder> --out <dest>` packs a deterministic Zip (fixed entry order
  and timestamps — two builds of the same source are byte-identical), its
  SHA-256 checksum file and a Marketplace-Index version snippet.

## 1.0.0 — Contract 1

The in-repo contract: `PluginManifest(id, name, version, min_api_version)`,
`NavItem`, `UiRegistry`, `ApiRegistry`, `LibraryApi`, `LlmApi`, `FilesApi`,
`StorageApi`, `SettingsApi(get, set)`, `PluginApi(ui, routes, llm, library)`,
`LocalBibPlugin`. Never published as a package.
