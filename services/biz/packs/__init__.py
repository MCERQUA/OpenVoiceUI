"""Trade/business packs for the JamBot client backend (design section 6, amendment 2026-11-17).

A pack ships centrally in the image (default-schemas/packs/<name>/) and a business turns it
on; a business never invents one. Turning a pack on is additive and is never undone (data
shape only grows): its collections appear in the registry, and its extensions add fields
and enum values to core collections.

  default-schemas/packs/<name>/pack.json
    {"name": "<name>", "prefix": "<p>", "version": "1.0.0", "label": "...", "description": "...",
     "collections": ["<p>_thing", ...],               one <p>_thing.json beside pack.json each
     "extends": {"<core collection>": {"fields": {"<p>_field": {...}},
                                        "enum_add": {"<enum field>": ["value", ...]}}},
     "hooks": "<python module in services/biz/packs/>" | null}

Rules the loader enforces (a pack that breaks one is not loaded, and the reason is logged):
  - every pack collection and every field a pack adds to a core collection starts with "<prefix>_"
  - enum_add only appends values to an existing enum field
  - a pack collection never replaces a core one

PACK HOOKS CONTRACT (the extension point, reviewed apart from any one pack). A hooks module
may define:
  check_write(store, op, collection, data, current, *, by, agent, via_pack)
      Called by the store BEFORE every create / edit / archive / unarchive on a collection the
      pack owns or extends, after schema validation and the staff_only check. op is 'create',
      'edit', 'archive' or 'unarchive'; data is the validated new data (for an edit, the record
      as it will be after the change); current is the stored record (None on create);
      via_pack is True only when the pack's own route made the call. Raise SchemaError to
      refuse. It must not write. On 'create' only, it may set server-derived fields of the new
      record in data (a field the schema declares; whatever a caller sent there is overwritten):
      pack:risk-transfer stamps a waiver's bound_evidence this way (rules 1.6.0, ruling W).
  register_routes(bp, api, ctx)
      Add routes under <api>/<prefix>/... to the biz blueprint at import time. Every route must
      call ctx.require_pack(name) first (404 when the business has not turned the pack on), and
      reads the store and the caller's role from ctx. Pack routes get the same authentication
      as every /api/biz/v1 route; they never take a tenant.
A hook never bypasses a wall: the tenant, the store, the staff_only flag and the agent's
source rule all apply before it runs.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PACK_NAME_RE = re.compile(r'^[a-z][a-z0-9-]{1,30}$')
PACK_PREFIX_RE = re.compile(r'^[a-z]{2,8}$')
HOOKS_MODULE_RE = re.compile(r'^[a-z][a-z0-9_]{1,40}$')

_HOOKS: dict = {}          # pack name -> hooks module (tests may seed this)


def _bad(name, why):
    logger.error('biz pack %s not loaded: %s', name, why)
    return None


def load_pack(pack_dir: Path, core_names) -> dict | None:
    """One pack's manifest + collection specs, or None (logged) when it breaks a rule."""
    name = pack_dir.name
    try:
        manifest = json.loads((pack_dir / 'pack.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _bad(name, 'pack.json missing or not JSON')
    if manifest.get('name') != name or not PACK_NAME_RE.match(name):
        return _bad(name, 'name must match its directory')
    prefix = manifest.get('prefix')
    if not isinstance(prefix, str) or not PACK_PREFIX_RE.match(prefix):
        return _bad(name, 'prefix must be 2-8 lower-case letters')
    hooks = manifest.get('hooks')
    if hooks is not None and (not isinstance(hooks, str) or not HOOKS_MODULE_RE.match(hooks)):
        return _bad(name, 'hooks must be a module name')
    collections = {}
    for coll in manifest.get('collections') or []:
        if not isinstance(coll, str) or not coll.startswith(prefix + '_') or coll in core_names:
            return _bad(name, f'collection {coll!r} must be named {prefix}_<name> and not be a core name')
        try:
            spec = json.loads((pack_dir / f'{coll}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return _bad(name, f'{coll}.json missing or not JSON')
        if spec.get('collection') != coll or not isinstance(spec.get('fields'), dict):
            return _bad(name, f'{coll}.json must name itself and carry fields')
        collections[coll] = spec
    extends = manifest.get('extends') or {}
    if not isinstance(extends, dict):
        return _bad(name, 'extends must be an object')
    for core, ext in extends.items():
        if core not in core_names or not isinstance(ext, dict):
            return _bad(name, f'extends {core!r}: not a core collection')
        for fname in (ext.get('fields') or {}):
            if not fname.startswith(prefix + '_'):
                return _bad(name, f'field {core}.{fname} added by a pack must start with {prefix}_')
        for fname, vals in (ext.get('enum_add') or {}).items():
            if not isinstance(vals, list) or not all(isinstance(v, str) and v for v in vals):
                return _bad(name, f'enum_add {core}.{fname} must be a list of text values')
    return {'name': name, 'prefix': prefix, 'version': manifest.get('version'),
            'label': manifest.get('label') or name, 'description': manifest.get('description') or '',
            'hooks': hooks, 'collections': collections, 'extends': extends, 'dir': str(pack_dir)}


def load_packs(packs_dir: Path, core_names) -> dict:
    out = {}
    packs_dir = Path(packs_dir)
    if not packs_dir.is_dir():
        return out
    for d in sorted(packs_dir.iterdir()):
        if d.is_dir() and (d / 'pack.json').is_file():
            pack = load_pack(d, set(core_names))
            if pack:
                out[pack['name']] = pack
    return out


def merge_into(merged: dict, pack: dict) -> None:
    """Add one enabled pack to a merged registry (in place). Core always wins a collision."""
    for coll, spec in pack['collections'].items():
        if coll in merged:
            continue
        s = json.loads(json.dumps(spec))
        s['layer'] = 'pack'
        s['pack'] = pack['name']
        s.setdefault('required', [])
        merged[coll] = s
    for core, ext in pack['extends'].items():
        target = merged.get(core)
        if target is None:
            continue
        fields = target['fields']
        for fname, fspec in (ext.get('fields') or {}).items():
            if fname not in fields:
                fields[fname] = dict(fspec, layer='pack', pack=pack['name'])
        for fname, vals in (ext.get('enum_add') or {}).items():
            f = fields.get(fname)
            if f is None or f.get('type') != 'enum':
                continue
            f['values'] = list(f.get('values') or []) + [v for v in vals if v not in (f.get('values') or [])]
        target.setdefault('packs', [])
        if pack['name'] not in target['packs']:
            target['packs'].append(pack['name'])


def hooks_for(pack: dict | None):
    """The pack's hooks module (imported once), or None."""
    if not pack:
        return None
    name = pack['name']
    if name in _HOOKS:
        return _HOOKS[name]
    mod = None
    if pack.get('hooks'):
        mod = importlib.import_module(f'{__name__}.{pack["hooks"]}')
    _HOOKS[name] = mod
    return mod


def register_routes(bp, api, ctx, packs_dir: Path) -> list:
    """Import every shipped pack's hooks and let it add its routes (once, at import time)."""
    done = []
    for name, pack in load_packs(packs_dir, ctx.core_names()).items():
        mod = hooks_for(pack)
        if mod is not None and hasattr(mod, 'register_routes'):
            mod.register_routes(bp, api, ctx)
            done.append(name)
    return done
