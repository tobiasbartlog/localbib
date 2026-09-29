# localbib-plugin-api

The **Add-on Contract** of [LocalBib](https://github.com/tobiasbartlog/localbib):
Python protocols, the Add-on Manifest schema, test fakes and a command-line
tool for Add-on authors. Published as its own package (ADR-0021) so an Add-on
can be developed and tested without the LocalBib core. MIT-licensed, unlike
the AGPL-3.0 core.

```bash
pip install localbib-plugin-api          # the contract: types, manifest, testing
pip install "localbib-plugin-api[dev]"   # + the localbib-addon CLI
pip install "localbib-plugin-api[schema]" # + a jsonschema cross-check (optional)
```

The package's semver major equals `plugin_api.API_VERSION`, the Add-on
Contract version a core accepts; see `CHANGELOG.md` for what changed between
contract versions.

## The `localbib-addon` CLI

Ships with the `dev` extra. Stdlib + this package only, so it works before an
Add-on has any other dependency installed.

### `localbib-addon check <folder>`

Validates a Bundle folder (the layout `plugin_api.load_manifest` and the
LocalBib core loader expect): the Manifest against `manifest.schema.json`,
the Bundle structure (a Python package under the Add-on id, `frontend/` with
exactly one script), the namespace rules the core enforces at load time
(`nav.view`/`nav.route`, locale keys — all prefixed with `<id>.`), locale
parity across every shipped language, a best-effort scan of the script for
route/key literals outside the namespace, a CSS class-prefix check and a
Python build-tag plausibility check.

```bash
localbib-addon check path/to/my-addon
```

Problems are printed as `ERROR: ...` or `WARNING: ...` lines. Only errors set
exit status 1 — a CSS class without the `<id>-` prefix, a stray literal the
script scan cannot be sure about, or an unusual `python` build tag are
warnings, not failures.

### `localbib-addon build <folder> --out <dest>`

Packs the folder into a Zip, next to its SHA-256 checksum file and an index
snippet (the fields the Marketplace-Index CI and the Release Console read:
`id`, `version`, `api_version`, `min_core`, `python`, the artifact filename,
its size and its checksum).

```bash
localbib-addon build path/to/my-addon --out dist/
```

The Zip is built deterministically — fixed entry order, fixed 1980-01-01
timestamps, fixed file attributes — so two builds of the same source are
byte-identical. `__pycache__`, `*.pyc`/`*.pyo`, `tests/` and the destination
directory itself are never packed from the source folder. For a Manifest with
a CPython build tag (`cp313-win_amd64`) the build instead adds freshly
compiled, unchecked-hash bytecode for every `.py` in the Zip; it must then run
on that CPython version (`--no-bytecode` opts out). See `AUTHORING.md`.
