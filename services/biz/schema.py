"""Schema registry for the JamBot client backend.

Three layers, merged at read time (design section 6):
  core      default-schemas/core/<collection>.json   ships in the OVU image
  overlay   <db_dir>/schema/custom/<collection>.json  per tenant, additive only
            - on a core collection: extra fields, each named x_<name>
            - a custom collection: named c_<name>, fields named freely
            - adopted library fields, keyed by their library key (sf_..., bi_...)

The shared FIELD LIBRARY (default-schemas/fields/library.json) is a platform
catalog of field DEFINITIONS only (key, label, type, unit, trade, range, values,
source). It ships with the engine; tenants read it and adopt from it, never
write it. A tenant's new custom field is filed as a candidate definition in its
own db (field_proposals); a platform promotion step (services/biz/promote.py)
vets candidates through services/biz/field_gate.py, and a vetted definition
reaches other tenants only through a release. Data never crosses tenants.

"Pages are free, data shape is governed": a record shape changes only through
this registry, and only additively. A type change, an enum value removal or a
new required field is refused; a field is retired with "retired": true, never
removed.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path

from services.paths import BIZ_SCHEMAS_DIR

CORE_DIR = BIZ_SCHEMAS_DIR / 'core'
PACKS_DIR = BIZ_SCHEMAS_DIR / 'packs'
LIBRARY_PATH = BIZ_SCHEMAS_DIR / 'fields' / 'library.json'
LIBRARY_KEY_RE = re.compile(r'^(sf|bi|fg|gen)_[a-z0-9][a-z0-9_]{0,40}$')

COLLECTION_RE = re.compile(r'^(__probe__|[a-z][a-z0-9_]{0,40})$')
CUSTOM_COLLECTION_RE = re.compile(r'^c_[a-z0-9][a-z0-9_]{0,37}$')
FIELD_RE = re.compile(r'^[a-z][a-z0-9_]{0,40}$')
CUSTOM_FIELD_RE = re.compile(r'^x_[a-z0-9][a-z0-9_]{0,37}$')
ID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

SCALAR_TYPES = {'string', 'text', 'email', 'phone', 'url', 'number', 'money',
                'integer', 'boolean', 'date', 'datetime', 'enum', 'ref', 'secret'}
TYPES = SCALAR_TYPES | {'list', 'object'}
LIST_ITEM_TYPES = {'string', 'number', 'integer', 'boolean', 'date', 'object'}

# Never accepted as record field names: they are record metadata or identity.
RESERVED_FIELDS = {'id', 'tenant', 'collection', 'version', 'created_at', 'created_by',
                   'updated_at', 'updated_by', 'archived_at', 'source', 'data'}

_FIELD_SPEC_KEYS = {'type', 'label', 'values', 'ref', 'items', 'retired', 'description', 'unit', 'min', 'max'}
# Carried only by fields adopted from the shared library (set by Registry.adopt).
_LIBRARY_SPEC_KEYS = {'library', 'library_version', 'trade', 'source'}
_STR_LIMITS = {'string': 1000, 'text': 50000, 'email': 320, 'phone': 40, 'url': 2000, 'secret': 1000}
MAX_CUSTOM_FIELDS = 200
MAX_CUSTOM_COLLECTIONS = 100

# Card / bank numbers: a 13-19 digit run (spaces or dashes allowed) that passes Luhn.
_PAN_RE = re.compile(r'(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)')
# Record ids (uuid4) are blanked before the scan: about 0.2% of random uuid4 ids carry a
# dash-separated 13-19 digit run that passes Luhn, and were refused as card numbers.
_UUID_TOKEN_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
# Stored-file ids are sha256 hex: about 1% carry a Luhn-valid 13-19 digit run, and a document
# pointing at its own file (biz-file:<id>) was refused as a card number. Only the exact
# 'biz-file:<64 hex>' token is blanked (a bare 64-hex run is NOT: padding a card number with hex
# letters must not smuggle it past the scan). Fields declared "format": "sha256" are validated as
# exactly 64 lower-case hex characters and left out of the scan instead.
_BIZ_FILE_TOKEN_RE = re.compile(r'biz-file:[0-9a-f]{64}(?![0-9A-Za-z])')
SHA256_RE = re.compile(r'^[0-9a-f]{64}\Z')


class SchemaError(Exception):
    """A request the registry refuses. status/code are the HTTP answer."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self):
        return {'error': self.message, 'code': self.code, **self.extra}


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def contains_card_number(value) -> bool:
    """True if any string inside value carries a Luhn-valid 13-19 digit run."""
    if isinstance(value, str):
        value = _BIZ_FILE_TOKEN_RE.sub(' ', _UUID_TOKEN_RE.sub(' ', value))
        for m in _PAN_RE.finditer(value):
            digits = re.sub(r'\D', '', m.group(0))
            if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                return True
        return False
    if isinstance(value, dict):
        return any(contains_card_number(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_card_number(v) for v in value)
    return False


def mask_secret(value):
    if not isinstance(value, str) or not value:
        return value
    tail = value[-4:] if len(value) > 8 else ''
    return '••••' + tail


def _load_json_dir(path: Path) -> dict:
    out = {}
    if not path.is_dir():
        return out
    for f in sorted(path.glob('*.json')):
        try:
            spec = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        name = spec.get('collection') or f.stem
        if name == f.stem and COLLECTION_RE.match(name):
            out[name] = spec
    return out


class Registry:
    """Merged core + tenant overlay. Reloaded from disk on every change."""

    def __init__(self, overlay_dir: Path, core_dir: Path = CORE_DIR, library_path: Path = LIBRARY_PATH,
                 packs_dir: Path = PACKS_DIR):
        from services.biz import packs as _packs
        self.core_dir = Path(core_dir)
        self.overlay_dir = Path(overlay_dir)
        self._lock = threading.Lock()
        self._core = _load_json_dir(self.core_dir)
        self.library = load_library(library_path)
        self.available_packs = _packs.load_packs(packs_dir, self._core.keys())
        self.enabled_packs = []
        self._merged = {}
        self.reload()

    def set_packs(self, names):
        """The packs this business turned on (from meta). Unknown names are ignored, never guessed."""
        self.enabled_packs = [n for n in (names or []) if n in self.available_packs]
        self.reload()

    def pack_of(self, collection: str):
        """The enabled packs that own or extend a collection (their hooks run on its writes)."""
        spec = self._merged.get(collection) or {}
        names = ([spec['pack']] if spec.get('pack') else []) + list(spec.get('packs') or [])
        return [self.available_packs[n] for n in names if n in self.available_packs]

    # ── reading ──────────────────────────────────────────────────────────
    def reload(self):
        overlay = _load_json_dir(self.overlay_dir)
        merged = {}
        for name, spec in self._core.items():
            merged[name] = json.loads(json.dumps(spec))
            merged[name]['layer'] = 'core'
        from services.biz import packs as _packs
        for pname in self.enabled_packs:
            _packs.merge_into(merged, self.available_packs[pname])
        for name, ov in overlay.items():
            if name in merged:
                for fname, fspec in (ov.get('fields') or {}).items():
                    if fname in merged[name]['fields']:
                        continue
                    if CUSTOM_FIELD_RE.match(fname):
                        merged[name]['fields'][fname] = dict(fspec, layer='custom')
                    elif fspec.get('library') == fname and fname in self.library['fields']:
                        merged[name]['fields'][fname] = dict(fspec, layer='library')
            elif CUSTOM_COLLECTION_RE.match(name):
                spec = json.loads(json.dumps(ov))
                spec['layer'] = 'custom'
                spec.setdefault('required', [])
                spec.setdefault('fields', {})
                merged[name] = spec
        self._merged = merged

    def collections(self) -> dict:
        return self._merged

    def get(self, name: str):
        return self._merged.get(name)

    def require(self, name: str) -> dict:
        if not isinstance(name, str) or not COLLECTION_RE.match(name):
            raise SchemaError(404, 'unknown_collection', 'No such collection', collection=str(name)[:60])
        spec = self._merged.get(name)
        if spec is None:
            raise SchemaError(404, 'unknown_collection', 'No such collection', collection=name)
        return spec

    def public(self, name: str | None = None):
        if name is not None:
            return self.require(name)
        return {n: s for n, s in self._merged.items() if not s.get('system')}

    # ── validation ───────────────────────────────────────────────────────
    def validate(self, collection: str, data, *, partial: bool, engine_too_old: bool = False):
        """Return (clean_data, refs) or raise SchemaError.

        refs = [(field, target_collection, id), ...] for the store to check exist.
        """
        spec = self.require(collection)
        if not isinstance(data, dict):
            raise SchemaError(422, 'bad_data', '"data" must be an object')
        fields = spec.get('fields') or {}
        clean, refs, errors = {}, [], []
        for key, value in data.items():
            if key in RESERVED_FIELDS or not isinstance(key, str) or key.startswith('_'):
                errors.append({'field': str(key)[:60], 'problem': 'reserved name'})
                continue
            fspec = fields.get(key)
            if fspec is None:
                errors.append({'field': key[:60], 'problem': 'unknown field'})
                continue
            if fspec.get('retired') and value is not None:
                errors.append({'field': key, 'problem': 'field is retired'})
                continue
            if value is None:
                clean[key] = None
                continue
            problem = self._check_value(fspec, value, refs, key)
            if problem:
                errors.append({'field': key, 'problem': problem})
            else:
                clean[key] = value
        if not partial:
            for req in spec.get('required') or []:
                v = clean.get(req)
                if v is None or v == '' or v == []:
                    errors.append({'field': req, 'problem': 'required'})
        if errors:
            unknown_only = all(e['problem'] == 'unknown field' for e in errors)
            if engine_too_old and unknown_only:
                raise SchemaError(409, 'engine_too_old',
                                  'This record uses fields a newer engine added; this engine will not write them',
                                  fields=errors)
            raise SchemaError(422, 'validation_failed', 'Record does not match the schema', fields=errors)
        # ref values are record ids, never free text: they are not scanned at all
        if contains_card_number({k: v for k, v in clean.items() if fields.get(k, {}).get('type') != 'ref'
                                 and fields.get(k, {}).get('format') != 'sha256'}):
            raise SchemaError(422, 'card_number_refused',
                              'Card and bank numbers are never stored. Keep the last 4 digits only.')
        return clean, refs

    def _check_value(self, fspec, value, refs, key):
        t = fspec.get('type')
        if t in _STR_LIMITS:
            if not isinstance(value, str):
                return 'must be text'
            if len(value) > _STR_LIMITS[t]:
                return f'longer than {_STR_LIMITS[t]} characters'
            if t == 'email' and value and '@' not in value:
                return 'not an email address'
            if t == 'url' and value and not (value.startswith(('http://', 'https://', '/'))):
                return 'not a link (http(s):// or /path)'
            if fspec.get('format') == 'sha256' and not SHA256_RE.match(value):
                return 'must be a sha256 id (64 lower-case hex characters)'
            return None
        if t in ('number', 'money', 'integer'):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return 'must be a number'
            if t == 'integer' and not isinstance(value, int):
                return 'must be a whole number'
            lo, hi = fspec.get('min'), fspec.get('max')
            if lo is not None and value < lo:
                return f'below the minimum {lo}'
            if hi is not None and value > hi:
                return f'above the maximum {hi}'
            return None
        if t == 'boolean':
            return None if isinstance(value, bool) else 'must be true or false'
        if t == 'date':
            if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}$', value):
                return 'must be a date YYYY-MM-DD'
            try:
                date.fromisoformat(value)
            except ValueError:
                return 'not a real date'
            return None
        if t == 'datetime':
            if not isinstance(value, str) or len(value) < 10:
                return 'must be an ISO date-time'
            try:
                datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                return 'must be an ISO date-time'
            return None
        if t == 'enum':
            return None if value in (fspec.get('values') or []) else 'not one of the allowed values'
        if t == 'ref':
            if not isinstance(value, str) or not ID_RE.match(value):
                return 'must be a record id'
            refs.append((key, fspec.get('ref'), value))
            return None
        if t == 'list':
            if not isinstance(value, list) or len(value) > 1000:
                return 'must be a list (max 1000 items)'
            items = fspec.get('items', 'string')
            for item in value:
                if items == 'object':
                    if not isinstance(item, dict):
                        return 'list items must be objects'
                else:
                    p = self._check_value({'type': items}, item, refs, key)
                    if p:
                        return 'list item ' + p
            return None
        if t == 'object':
            return None if isinstance(value, dict) else 'must be an object'
        return 'field has an unknown type'

    def secret_fields(self, collection: str):
        spec = self.get(collection) or {}
        return [k for k, f in (spec.get('fields') or {}).items() if f.get('type') == 'secret']

    # ── additive custom schema ───────────────────────────────────────────
    def _check_field_spec(self, name, fspec):
        if not isinstance(fspec, dict):
            raise SchemaError(422, 'bad_field_spec', 'A field spec must be an object', field=name)
        extra = set(fspec) - _FIELD_SPEC_KEYS
        if 'required' in fspec:
            raise SchemaError(409, 'additive_violation',
                              'A custom field cannot be required: existing records would break', field=name)
        if extra:
            raise SchemaError(422, 'bad_field_spec', 'Unknown keys in field spec', field=name, keys=sorted(extra))
        t = fspec.get('type')
        if t not in TYPES:
            raise SchemaError(422, 'bad_field_spec', 'Unknown field type', field=name, allowed=sorted(TYPES))
        if t == 'enum':
            vals = fspec.get('values')
            if not isinstance(vals, list) or not vals or not all(isinstance(v, str) and v for v in vals):
                raise SchemaError(422, 'bad_field_spec', 'An enum needs a list of text values', field=name)
        if t == 'ref' and fspec.get('ref') not in self._merged:
            raise SchemaError(422, 'bad_field_spec', 'A ref must point at an existing collection', field=name)
        if t == 'list' and fspec.get('items', 'string') not in LIST_ITEM_TYPES:
            raise SchemaError(422, 'bad_field_spec', 'Unknown list item type', field=name)
        if 'label' in fspec and not isinstance(fspec['label'], str):
            raise SchemaError(422, 'bad_field_spec', 'label must be text', field=name)
        if 'retired' in fspec and not isinstance(fspec['retired'], bool):
            raise SchemaError(422, 'bad_field_spec', 'retired must be true or false', field=name)
        for k in ('min', 'max'):
            v = fspec.get(k)
            if v is not None and (t not in ('number', 'money', 'integer') or isinstance(v, bool)
                                  or not isinstance(v, (int, float))):
                raise SchemaError(422, 'bad_field_spec', f'{k} needs a number field and a number', field=name)
        if fspec.get('unit') is not None and not isinstance(fspec['unit'], str):
            raise SchemaError(422, 'bad_field_spec', 'unit must be text', field=name)

    @staticmethod
    def _check_additive(name, old, new):
        if old.get('type') != new.get('type'):
            raise SchemaError(409, 'additive_violation',
                              'A field type never changes. Add a new field and retire this one.',
                              field=name, was=old.get('type'), asked=new.get('type'))
        if old.get('type') == 'enum':
            dropped = [v for v in old.get('values', []) if v not in new.get('values', [])]
            if dropped:
                raise SchemaError(409, 'additive_violation', 'Enum values are never removed',
                                  field=name, removed=dropped)
        if old.get('type') == 'ref' and old.get('ref') != new.get('ref'):
            raise SchemaError(409, 'additive_violation', 'A ref target never changes', field=name)
        if old.get('type') == 'list' and old.get('items', 'string') != new.get('items', 'string'):
            raise SchemaError(409, 'additive_violation', 'A list item type never changes', field=name)

    def add_custom(self, payload):
        """Apply an additive change. Returns (merged collection spec, [new field names])."""
        if not isinstance(payload, dict):
            raise SchemaError(422, 'bad_request', 'Body must be an object')
        name = payload.get('collection')
        fields = payload.get('fields')
        if not isinstance(name, str) or not COLLECTION_RE.match(name):
            raise SchemaError(422, 'bad_collection_name',
                              'Collection names are lower-case letters, digits and _')
        if not isinstance(fields, dict) or not fields:
            raise SchemaError(422, 'bad_request', '"fields" must be a non-empty object')
        with self._lock:
            self.reload()
            existing = self._merged.get(name)
            # a pack collection is governed like a core one: tenants add x_ fields only
            is_core = existing is not None and existing.get('layer') in ('core', 'pack')
            if existing is not None and existing.get('system'):
                raise SchemaError(409, 'system_collection', 'This collection is reserved')
            if existing is None and not CUSTOM_COLLECTION_RE.match(name):
                raise SchemaError(422, 'bad_collection_name',
                                  'A new collection must be named c_<name> so a core upgrade never collides')
            overlay_path = self.overlay_dir / f'{name}.json'
            try:
                overlay = json.loads(overlay_path.read_text(encoding='utf-8')) if overlay_path.exists() else {}
            except (OSError, ValueError):
                raise SchemaError(500, 'overlay_unreadable', 'The tenant schema overlay is unreadable')
            ov_fields = dict(overlay.get('fields') or {})
            new_fields = []
            if existing is None and len(_load_json_dir(self.overlay_dir)) >= MAX_CUSTOM_COLLECTIONS:
                raise SchemaError(409, 'limit', 'Too many custom collections')
            for fname, fspec in fields.items():
                if not isinstance(fname, str) or not FIELD_RE.match(fname) or fname in RESERVED_FIELDS:
                    raise SchemaError(422, 'bad_field_name', 'Field names are lower-case letters, digits and _',
                                      field=str(fname)[:60])
                if LIBRARY_KEY_RE.match(fname):
                    raise SchemaError(422, 'library_key_reserved',
                                      'sf_/bi_/fg_/gen_ names belong to the shared field library: '
                                      'adopt the library field (POST /schema/adopt) instead', field=fname)
                if is_core and not CUSTOM_FIELD_RE.match(fname):
                    if fname in existing['fields']:
                        raise SchemaError(409, 'additive_violation',
                                          'Core fields are changed only by a JamBot release', field=fname)
                    raise SchemaError(422, 'bad_field_name',
                                      'Custom fields on a core collection are named x_<name>', field=fname)
                self._check_field_spec(fname, fspec)
                old = (existing or {}).get('fields', {}).get(fname)
                if old is not None:
                    self._check_additive(fname, old, fspec)
                else:
                    new_fields.append(fname)
                ov_fields[fname] = {k: v for k, v in fspec.items()}
            if len(ov_fields) > MAX_CUSTOM_FIELDS:
                raise SchemaError(409, 'limit', 'Too many custom fields on this collection')
            new_overlay = {'collection': name, 'fields': ov_fields}
            if not is_core:
                label = payload.get('label') or overlay.get('label') or name[2:].replace('_', ' ').title()
                new_overlay.update({
                    'label': str(label)[:80],
                    'label_plural': str(payload.get('label_plural') or overlay.get('label_plural') or label)[:80],
                    'title_field': payload.get('title_field') or overlay.get('title_field'),
                    'description': str(payload.get('description') or overlay.get('description') or '')[:500],
                })
                tf = new_overlay['title_field']
                if tf is not None and tf not in ov_fields:
                    raise SchemaError(422, 'bad_request', 'title_field must be one of the fields')
            self._write_overlay(overlay_path, new_overlay)
            return self._merged[name], new_fields

    def _write_overlay(self, overlay_path: Path, new_overlay: dict):
        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        tmp = overlay_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(new_overlay, indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, overlay_path)
        self.reload()

    # ── shared field library ─────────────────────────────────────────────
    def adopt(self, collection: str, key: str) -> dict:
        """Adopt a library field into one of this tenant's collections (idempotent)."""
        lib = self.library['fields'].get(key) if isinstance(key, str) else None
        if lib is None:
            raise SchemaError(404, 'unknown_library_field', 'No such field in the shared library',
                              key=str(key)[:60])
        with self._lock:
            self.reload()
            spec = self.require(collection)
            if spec.get('system') or spec.get('read_through'):
                raise SchemaError(409, 'system_collection', 'This collection does not take custom fields')
            have = spec['fields'].get(key)
            if have is not None:
                if have.get('library') == key:
                    return self._merged[collection]
                raise SchemaError(409, 'field_exists', 'This collection already has a field with that name')
            overlay_path = self.overlay_dir / f'{collection}.json'
            try:
                overlay = json.loads(overlay_path.read_text(encoding='utf-8')) if overlay_path.exists() else {}
            except (OSError, ValueError):
                raise SchemaError(500, 'overlay_unreadable', 'The tenant schema overlay is unreadable')
            overlay.setdefault('collection', collection)
            fields = dict(overlay.get('fields') or {})
            fields[key] = library_field_spec(key, lib, self.library.get('library_version'))
            overlay['fields'] = fields
            self._write_overlay(overlay_path, overlay)
            return self._merged[collection]


def library_field_spec(key: str, lib: dict, version) -> dict:
    """The overlay spec for an adopted library field (definition only)."""
    out = {'type': lib['type'], 'label': lib['label'], 'library': key, 'library_version': version}
    for k in ('unit', 'min', 'max', 'values', 'items', 'description', 'trade', 'source'):
        if lib.get(k) is not None:
            out[k] = lib[k]
    return out


def load_library(path: Path = LIBRARY_PATH) -> dict:
    """Load and sanity-check the shared field library. A bad entry is dropped, never guessed."""
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'library_version': None, 'sources': {}, 'fields': {}}
    fields = {}
    for key, f in (raw.get('fields') or {}).items():
        if not LIBRARY_KEY_RE.match(key) or not isinstance(f, dict):
            continue
        if f.get('type') not in TYPES - {'ref', 'secret', 'object'}:
            continue
        if f['type'] == 'enum' and not f.get('values'):
            continue
        fields[key] = f
    return {'library_version': raw.get('library_version'), 'sources': raw.get('sources') or {},
            'fields': fields}
