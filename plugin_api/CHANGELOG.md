# Changelog — localbib-plugin-api

The package version follows semver; its major number is the Add-on Contract
version (`plugin_api.API_VERSION`). A core accepts exactly one contract
version; an Add-on built against another one is shown as incompatible.

## Unreleased

Added (no contract change, `API_VERSION` stays `2`):

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
