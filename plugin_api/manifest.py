"""The Add-on Manifest, the seven Berechtigungen and the manifest validator.

Stdlib-only on purpose: the core imports this at runtime and must never need
``jsonschema``. The single source of truth for the manifest shape is
``manifest.schema.json`` next to this file; :func:`validate_manifest`
interprets the subset of JSON Schema that file uses (``type``, ``enum``,
``pattern``, ``minLength``/``maxLength``, ``minimum``, ``required``,
``properties``, ``additionalProperties``, ``items``, ``minItems``,
``uniqueItems``, local ``$ref``). Any full JSON Schema validator reaches the
same verdict on the same file — the package tests cross-check that when
``jsonschema`` is installed (extra ``[schema]``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

SCHEMA_PATH = Path(__file__).with_name("manifest.schema.json")

#: The file name of the Manifest at the root of a Bundle.
MANIFEST_FILENAME = "plugin.json"


# =============================================================================
# Berechtigungen (Add-on Permissions)
# =============================================================================

class Permission(str, Enum):
    """The seven Berechtigungen of Add-on Contract 2. The list is closed."""

    LIBRARY_READ = "library.read"
    LIBRARY_WRITE = "library.write"
    LLM = "llm"
    SETTINGS_CORE = "settings.core"
    NETWORK = "network"
    FILES = "files"
    STORAGE = "storage"


@dataclass(frozen=True)
class PermissionInfo:
    """How the core treats one Berechtigung.

    ``enforced`` is True (**durchgesetzt**) when the core hands the matching
    host service (or, for ``library.write``, the one writing method,
    ``LibraryApi.create_by_doi``) over only if the permission is declared,
    False (**erklaert**)
    when the core cannot check it and it is the author's declaration only.
    ``service`` names the ``PluginApi`` attribute the permission unlocks
    (``None`` for a permission that unlocks no service).
    """

    permission: Permission
    enforced: bool
    service: Optional[str]


PERMISSIONS: Mapping[Permission, PermissionInfo] = {
    Permission.LIBRARY_READ: PermissionInfo(Permission.LIBRARY_READ, True, "library"),
    # No service of its own: it unlocks LibraryApi.create_by_doi on the
    # ``library`` handle (which library.read hands over); without it that
    # method raises PermissionError.
    Permission.LIBRARY_WRITE: PermissionInfo(Permission.LIBRARY_WRITE, True, None),
    Permission.LLM: PermissionInfo(Permission.LLM, True, "llm"),
    # Unlocks SettingsApi.core(); the plugin-scoped get/set needs no permission.
    Permission.SETTINGS_CORE: PermissionInfo(Permission.SETTINGS_CORE, True, None),
    # In-process code can always open a socket.
    Permission.NETWORK: PermissionInfo(Permission.NETWORK, False, None),
    Permission.FILES: PermissionInfo(Permission.FILES, True, "files"),
    Permission.STORAGE: PermissionInfo(Permission.STORAGE, True, "storage"),
}

#: The ``PluginApi`` services that exist only when their permission is declared.
GATED_SERVICES: Mapping[str, Permission] = {
    info.service: perm for perm, info in PERMISSIONS.items() if info.service
}


def parse_permissions(values: Iterable[Union[str, Permission]]) -> frozenset[Permission]:
    """Turn declared permission names into :class:`Permission` members.

    Raises ``ValueError`` on a name outside the closed list.
    """
    out: set[Permission] = set()
    for value in values:
        try:
            out.add(Permission(value))
        except ValueError:
            raise ValueError(f"unknown permission: {value!r}") from None
    return frozenset(out)


def granted_services(permissions: Iterable[Union[str, Permission]]) -> frozenset[str]:
    """The gated ``PluginApi`` services a set of declared permissions unlocks."""
    perms = parse_permissions(permissions)
    return frozenset(s for s, p in GATED_SERVICES.items() if p in perms)


# =============================================================================
# Manifest dataclasses
# =============================================================================

#: The four Slots of the core UI an Add-on may contribute to.
SLOTS: tuple[str, ...] = (
    "item-list-filter",
    "item-detail-aside",
    "research-chat-context",
    "settings",
)

#: The field types a declared Add-on setting may have.
SETTING_TYPES: tuple[str, ...] = ("string", "secret", "path", "bool")


@dataclass(frozen=True)
class NavItem:
    """A navigation entry a plugin contributes to the LocalBib sidebar.

    ``view`` is the frontend view key the SPA maps to a Vue component; the core
    never renders the view itself, it only relays this descriptor to the SPA via
    ``GET /api/plugins/nav``.
    """

    id: str
    label: str
    icon: str                    # inline SVG markup, rendered via v-html
    route: str                   # hash-router path, e.g. "/notes"
    view: str                    # frontend component key, e.g. "notes"


@dataclass(frozen=True)
class SettingField:
    """One setting an Add-on declares; the core can render it generically."""

    key: str
    type: str                    # one of SETTING_TYPES
    label: str                   # locale key
    default: Any = None


@dataclass(frozen=True)
class FrontendSpec:
    """The Bundle's frontend files, paths relative to the Bundle root."""

    script: str
    stylesheet: Optional[str] = None
    assets: tuple[str, ...] = ()
    # language code -> locale file; excluded from hashing (dicts are unhashable)
    locales: Mapping[str, str] = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class PluginManifest:
    """Identity and declarations of a plugin (the Add-on Manifest).

    Only ``id``/``name``/``version`` are required in Python, so an in-code
    manifest from contract 1 still constructs. A Bundle's ``plugin.json`` is
    held to the full schema (:func:`validate_manifest`).
    """

    id: str                      # e.g. "notes"
    name: str
    version: str
    api_version: int = 2
    author: str = ""
    license: str = ""            # SPDX expression
    homepage: str = ""
    tagline: str = ""            # <= 80 characters
    description: str = ""        # Markdown
    languages: tuple[str, ...] = ()
    default_language: str = ""
    min_core: str = "0.0.0"
    python: str = "any"          # "any" or a build tag like "cp313-win_amd64"
    permissions: frozenset[Permission] = frozenset()
    settings: tuple[SettingField, ...] = ()
    frontend: Optional[FrontendSpec] = None
    nav: Optional[NavItem] = None
    slots: tuple[str, ...] = ()

    def declares(self, permission: Union[str, Permission]) -> bool:
        return Permission(permission) in self.permissions

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PluginManifest":
        """Build a manifest from a *valid* ``plugin.json`` document.

        Call :func:`validate_manifest` first (or use :func:`load_manifest`);
        this constructor does not re-check the schema.
        """
        fe = data.get("frontend")
        frontend = None
        if fe is not None:
            frontend = FrontendSpec(
                script=fe["script"],
                stylesheet=fe.get("stylesheet"),
                assets=tuple(fe.get("assets", ())),
                locales=dict(fe.get("locales", {})),
            )
        nav_data = data.get("nav")
        nav = None
        if nav_data is not None:
            nav = NavItem(
                id=data["id"],
                label=nav_data["label"],
                icon=nav_data.get("icon", ""),
                route=nav_data["route"],
                view=nav_data["view"],
            )
        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            api_version=int(data.get("api_version", 2)),
            author=data.get("author", ""),
            license=data.get("license", ""),
            homepage=data.get("homepage", ""),
            tagline=data.get("tagline", ""),
            description=data.get("description", ""),
            languages=tuple(data.get("languages", ())),
            default_language=data.get("default_language", ""),
            min_core=data.get("min_core", "0.0.0"),
            python=data.get("python", "any"),
            permissions=parse_permissions(data.get("permissions", ())),
            settings=tuple(
                SettingField(s["key"], s["type"], s["label"], s.get("default"))
                for s in data.get("settings", ())
            ),
            frontend=frontend,
            nav=nav,
            slots=tuple(data.get("slots", ())),
        )


# =============================================================================
# Validation
# =============================================================================

class ManifestError(ValueError):
    """A ``plugin.json`` that does not satisfy the manifest schema."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def load_schema() -> dict:
    """The manifest JSON Schema shipped with this package."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


_TYPES: Mapping[str, Any] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def _resolve(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(value: Any, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), root, path, errors)
        return
    where = path or "(root)"
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            errors.append(f"{where}: expected {' or '.join(types)}")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
        return
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if _is_type(value, "number") and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{where}: below minimum {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: needs at least {schema['minItems']} item(s)")
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in value:
                if item in seen:
                    errors.append(f"{where}: duplicate item {item!r}")
                seen.append(item)
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], root, f"{path}[{i}]", errors)
    if isinstance(value, dict):
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{where}: missing required field {key!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            sub = f"{path}.{key}" if path else key
            if key in props:
                _check(item, props[key], root, sub, errors)
            elif extra is False:
                errors.append(f"{where}: unknown field {key!r}")
            elif isinstance(extra, dict):
                _check(item, extra, root, sub, errors)


def validate_manifest(data: Any, schema: Optional[dict] = None) -> list[str]:
    """Check a parsed ``plugin.json`` against the manifest schema.

    Returns the list of problems as ``"<path>: <message>"`` strings; an empty
    list means valid. Beyond the schema it checks the cross-field rules a
    JSON Schema cannot express cleanly: ``default_language`` is one of
    ``languages``, and ``library.write`` comes with ``library.read`` (the
    writing method lives on the ``library`` handle that read hands over, so
    write alone would unlock nothing).
    """
    schema = schema if schema is not None else load_schema()
    errors: list[str] = []
    _check(data, schema, schema, "", errors)
    if not errors and data["default_language"] not in data["languages"]:
        errors.append("default_language: must be one of languages")
    permissions = (data.get("permissions") or []) if not errors else []
    if Permission.LIBRARY_WRITE.value in permissions and Permission.LIBRARY_READ.value not in permissions:
        errors.append("permissions: library.write needs library.read")
    return errors


def load_manifest(path: Union[str, Path]) -> PluginManifest:
    """Read, validate and parse a ``plugin.json`` (or a Bundle folder holding one).

    Raises :class:`ManifestError` listing every problem.
    """
    path = Path(path)
    if path.is_dir():
        path = path / MANIFEST_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError([f"{path.name}: {exc}"]) from exc
    errors = validate_manifest(data)
    if errors:
        raise ManifestError(errors)
    return PluginManifest.from_dict(data)
