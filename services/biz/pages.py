"""The company-system page registry (app_pages) and the shared menu shell model.

Design section 9. A tenant's "company system" is the set of canvas pages that work on
its business data. Each one is registered here once, and the shared menu shell
(/jb/v1/shell.js, served from the image) draws one consistent menu from these rows.

  section  fixed taxonomy, the same words for every client, so menus stay consistent
  kind     tool | dashboard | list | form | report
  shell    office (left rail + bottom bar) | field (slim top bar, for crews) | off
  id       the canvas page id = the file name without .html (the canvas serve rule)

Only sections with at least one registered page render, so the menu grows as the
client builds. A page file is never edited to get the shell: OVU injects it at serve
time (routes/canvas.py), so removing a registry row gives the page back unchanged.

Every row lives in the tenant's own business.db (the same jail cell as its records):
no row can name another tenant, a path outside this tenant's canvas folder, or a URL.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from services.biz.schema import SchemaError

SECTIONS = (
    ('home', 'Home', 'home'),
    ('customers', 'Customers', 'users'),
    ('sales', 'Sales', 'document'),
    ('jobs', 'Jobs', 'clipboard'),
    ('money', 'Money', 'dollar'),
    ('stock', 'Stock', 'box'),
    ('team', 'Team', 'user'),
    ('files', 'Files', 'folder'),
    ('reports', 'Reports', 'chart'),
)
SECTION_IDS = tuple(s[0] for s in SECTIONS)
KINDS = ('tool', 'dashboard', 'list', 'form', 'report')
SHELLS = ('office', 'field', 'off')
MAX_TABS = 5
MAX_PAGES = 200

# Icons the shell can draw, beyond the canvas icon library (services/canvas_styles._ICON_PATHS).
# Same 24x24 stroke format: stroke=currentColor, so they follow every style's palette.
SHELL_ICON_PATHS = {
    'clipboard': '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1"/>'
                 '<line x1="9" y1="10" x2="15" y2="10"/><line x1="9" y1="14" x2="15" y2="14"/>',
    'box': '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><polyline points="3 8 12 13 21 8"/><line x1="12" y1="13" x2="12" y2="21"/>',
    'truck': '<rect x="2" y="6" width="12" height="10"/><path d="M14 9h4l3 3v4h-7"/>'
             '<circle cx="6" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
    'wrench': '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-2.4z"/>',
    'receipt': '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z"/><line x1="9" y1="8" x2="15" y2="8"/>'
               '<line x1="9" y1="12" x2="15" y2="12"/>',
    'list': '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>'
            '<line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/>'
            '<circle cx="4" cy="18" r="1"/>',
    'map-pin': '<path d="M12 22s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/>',
    'menu': '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/>',
    'more': '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
    'back': '<polyline points="15 5 8 12 15 19"/>',
    'close': '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
}
_SHELL_CHROME_ICONS = ('menu', 'more', 'back', 'close', 'plus')


def icon_paths() -> dict:
    """Every icon a registry row may name: the canvas library plus the shell's own."""
    try:
        from services.canvas_styles import _ICON_PATHS
        merged = dict(_ICON_PATHS)
    except Exception:            # the shell must still work if the style module changes
        merged = {}
    merged.update(SHELL_ICON_PATHS)
    return merged


FILE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.html$')
VERB_HREF_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.html(?:[?#][A-Za-z0-9=&_.%#-]{0,200})?$')
_TEXT_BAD_RE = re.compile(r'[<>\x00-\x1f\x7f]')
_SELECTOR_BAD_RE = re.compile(r'[<>{};\\\x00-\x1f\x7f]')
_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$')
_FONT_BAD_RE = re.compile(r'[<>{};\\()\x00-\x1f\x7f]')

FIELDS = ('file', 'label', 'section', 'kind', 'collections', 'icon', 'order', 'tab', 'verb', 'verb_href',
          'shell', 'legacy_nav')
MUTABLE = tuple(f for f in FIELDS if f != 'file')


def _bad(field, message, code='validation_failed', status=422):
    return SchemaError(status, code, message, field=field)


def _text(field, value, limit, *, required=False):
    if value is None:
        if required:
            raise _bad(field, f'{field} is required')
        return None
    if not isinstance(value, str):
        raise _bad(field, f'{field} must be text')
    value = value.strip()
    if not value:
        if required:
            raise _bad(field, f'{field} is required')
        return None
    if len(value) > limit:
        raise _bad(field, f'{field} must be at most {limit} characters')
    if _TEXT_BAD_RE.search(value):
        raise _bad(field, f'{field} cannot contain < > or control characters')
    return value


def check_file(value) -> str:
    """A page file is a bare file name in this tenant's canvas folder. Never a path, never a URL."""
    if not isinstance(value, str) or not value:
        raise _bad('file', 'file is required: the page file name, e.g. "job-tracker.html"')
    if '/' in value or '\\' in value or '..' in value or ':' in value:
        raise SchemaError(422, 'page_file_refused',
                          'file must be a bare page file name in this business\'s own canvas folder '
                          '(no folders, no other business, no URLs)', field='file')
    if not FILE_RE.match(value):
        raise _bad('file', 'file must look like "job-tracker.html" (letters, digits, - and _, ending .html)')
    return value


def clean_page(payload, *, partial: bool, known_collection) -> dict:
    """Validate a registry payload. partial=True for PATCH. Unknown keys are refused."""
    if not isinstance(payload, dict):
        raise SchemaError(400, 'bad_request', 'Body must be a JSON object')
    allowed = MUTABLE if partial else FIELDS
    extra = sorted(set(payload) - set(allowed) - {'id'})
    if extra:
        raise SchemaError(422, 'unknown_keys', 'Unknown keys for a registry page', keys=extra[:20],
                          allowed=list(allowed))
    out = {}
    if not partial:
        out['file'] = check_file(payload.get('file'))
        pid = payload.get('id')
        if pid is not None and pid != out['file'][:-5]:
            raise _bad('id', 'id is the page file name without .html; leave it out or make them match')
    elif 'id' in payload:
        raise _bad('id', 'id cannot be changed; register the new page instead')

    def has(k):
        return (not partial) or k in payload

    if has('label'):
        out['label'] = _text('label', payload.get('label'), 60, required=True)
    if has('section'):
        sec = payload.get('section')
        if sec not in SECTION_IDS:
            raise _bad('section', 'section must be one of: ' + ', '.join(SECTION_IDS))
        out['section'] = sec
    if has('kind'):
        kind = payload.get('kind', 'tool') if not partial else payload.get('kind')
        if kind not in KINDS:
            raise _bad('kind', 'kind must be one of: ' + ', '.join(KINDS))
        out['kind'] = kind
    if has('collections'):
        cols = payload.get('collections') or []
        if not isinstance(cols, list) or len(cols) > 12 or not all(isinstance(c, str) for c in cols):
            raise _bad('collections', 'collections must be a list of up to 12 collection names')
        for c in cols:
            if not known_collection(c):
                raise SchemaError(422, 'unknown_collection',
                                  'collections names a collection this business does not have '
                                  '(GET /api/biz/v1/schema lists them)', field='collections', collection=c[:60])
        out['collections'] = list(dict.fromkeys(cols))
    if has('icon'):
        icon = payload.get('icon')
        if icon is not None and icon not in icon_paths():
            raise _bad('icon', 'icon must be one of: ' + ', '.join(sorted(icon_paths())))
        out['icon'] = icon
    if has('order'):
        order = payload.get('order', 100)
        if order is None:
            order = 100
        if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 9999:
            raise _bad('order', 'order must be a whole number from 0 to 9999')
        out['order'] = order
    if has('tab'):
        tab = payload.get('tab', False)
        if not isinstance(tab, bool):
            raise _bad('tab', 'tab must be true or false')
        out['tab'] = tab
    if has('verb'):
        out['verb'] = _text('verb', payload.get('verb'), 40)
    if has('verb_href'):
        href = payload.get('verb_href')
        if href in (None, ''):
            out['verb_href'] = None
        elif not isinstance(href, str) or not VERB_HREF_RE.match(href) or '..' in href:
            raise _bad('verb_href', 'verb_href must be a page of this business, e.g. "intake.html#new"')
        else:
            out['verb_href'] = href
    if has('shell'):
        shell = payload.get('shell', 'office') if not partial else payload.get('shell')
        if shell not in SHELLS:
            raise _bad('shell', 'shell must be one of: ' + ', '.join(SHELLS))
        out['shell'] = shell
    if has('legacy_nav'):
        sel = payload.get('legacy_nav')
        if sel in (None, ''):
            out['legacy_nav'] = None
        elif not isinstance(sel, str) or len(sel) > 200 or _SELECTOR_BAD_RE.search(sel):
            raise _bad('legacy_nav', 'legacy_nav must be a CSS selector (up to 200 characters, no < > { } ; \\)')
        else:
            out['legacy_nav'] = sel.strip()
    if partial and not out:
        raise SchemaError(422, 'nothing_to_change', 'Send at least one field to change')
    return out


def row_to_page(row) -> dict:
    return {
        'id': row['id'], 'file': row['file'], 'label': row['label'], 'section': row['section'],
        'kind': row['kind'], 'collections': json.loads(row['collections'] or '[]'), 'icon': row['icon'],
        'order': row['ord'], 'tab': bool(row['tab']), 'verb': row['verb'], 'verb_href': row['verb_href'],
        'shell': row['shell'], 'legacy_nav': row['legacy_nav'], 'first_of_system': bool(row['first_of_system']),
        'version': row['version'], 'created_at': row['created_at'], 'created_by': row['created_by'],
        'updated_at': row['updated_at'], 'updated_by': row['updated_by'], 'archived_at': row['archived_at'],
        'source': json.loads(row['source']) if row['source'] else None,
    }


_COLUMN = {'order': 'ord'}


class PageRegistryMixin:
    """BizStore methods for app_pages. Needs _tx, _read, _writable, check_source, _activity, registry."""

    def _known_collection(self, name) -> bool:
        spec = self.registry.get(name) if isinstance(name, str) else None
        return bool(spec) and not spec.get('system')

    def pages_list(self, *, archived=False) -> list:
        sql = 'SELECT * FROM app_pages'
        if not archived:
            sql += ' WHERE archived_at IS NULL'
        sql += ' ORDER BY ord, label'
        with self._read() as conn:
            rows = conn.execute(sql).fetchall()
        order = {s: i for i, s in enumerate(SECTION_IDS)}
        pages = [row_to_page(r) for r in rows]
        pages.sort(key=lambda p: (order.get(p['section'], 99), p['order'], p['label'].lower()))
        return pages

    def page_get(self, pid) -> dict:
        if not isinstance(pid, str) or not FILE_RE.match(pid + '.html'):
            raise SchemaError(404, 'not_found', 'No such registered page')
        with self._read() as conn:
            row = conn.execute('SELECT * FROM app_pages WHERE id = ?', (pid,)).fetchone()
        if row is None:
            raise SchemaError(404, 'not_found', 'No such registered page')
        return row_to_page(row)

    def page_for_serve(self, file_name):
        """The live registry row for a served page file, or None. Read-only, one indexed lookup."""
        if not isinstance(file_name, str) or not FILE_RE.match(file_name):
            return None
        with self._read() as conn:
            row = conn.execute('SELECT * FROM app_pages WHERE id = ? AND file = ? AND archived_at IS NULL',
                               (file_name[:-5], file_name)).fetchone()
        return row_to_page(row) if row else None

    def page_register(self, payload, *, by, source=None, agent=False, pages_dir=None) -> dict:
        from services.biz.store import utcnow     # late import: store imports this module
        self._writable()
        source = self.check_source(source, agent=agent)
        clean = clean_page(payload, partial=False, known_collection=self._known_collection)
        if pages_dir is not None and not (Path(pages_dir) / clean['file']).is_file():
            raise SchemaError(422, 'page_not_found',
                              'Build the page first: there is no such page file in this business\'s canvas '
                              'folder', field='file', file=clean['file'])
        pid = clean['file'][:-5]
        now = utcnow()
        with self._tx() as conn:
            row = conn.execute('SELECT * FROM app_pages WHERE id = ?', (pid,)).fetchone()
            if row is not None:
                raise SchemaError(409, 'page_registered',
                                  'This page is already registered; PATCH it (or unarchive it) instead',
                                  current=row_to_page(row))
            n = conn.execute('SELECT COUNT(*) FROM app_pages').fetchone()[0]
            if n >= MAX_PAGES:
                raise SchemaError(409, 'registry_full', f'A company system holds at most {MAX_PAGES} pages')
            first = n == 0
            conn.execute(
                'INSERT INTO app_pages(id, tenant, file, label, section, kind, collections, icon, ord, tab, verb, '
                'verb_href, shell, legacy_nav, first_of_system, version, created_at, created_by, updated_at, '
                'updated_by, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)',
                (pid, self.tenant, clean['file'], clean['label'], clean['section'], clean.get('kind', 'tool'),
                 json.dumps(clean.get('collections') or []), clean.get('icon'), clean.get('order', 100),
                 int(clean.get('tab', False)), clean.get('verb'), clean.get('verb_href'),
                 clean.get('shell', 'office'), clean.get('legacy_nav'), int(first), now, by, now, by,
                 json.dumps(source) if source else None))
            self._activity(conn, collection='app_pages', record_id=pid, type_='page_registered', by=by,
                           source=source, diff={'set': clean, 'first_of_system': first})
            row = conn.execute('SELECT * FROM app_pages WHERE id = ?', (pid,)).fetchone()
        return row_to_page(row)

    def page_update(self, pid, patch, *, expected_version, by, source=None, agent=False) -> dict:
        from services.biz.store import utcnow
        self._writable()
        source = self.check_source(source, agent=agent)
        clean = clean_page(patch, partial=True, known_collection=self._known_collection)
        if not isinstance(expected_version, int):
            raise SchemaError(428, 'if_match_required', 'Send If-Match: <version> (the version you read)')
        current = self.page_get(pid)
        if current['archived_at']:
            raise SchemaError(409, 'archived', 'This page is archived; unarchive it first')
        with self._tx() as conn:
            row = conn.execute('SELECT * FROM app_pages WHERE id = ?', (pid,)).fetchone()
            if row['version'] != expected_version:
                raise SchemaError(409, 'version_conflict',
                                  'Someone else changed this page entry first. Reload to see their change.',
                                  current=row_to_page(row))
            old = row_to_page(row)
            changes = {k: [old[k], v] for k, v in clean.items() if old[k] != v}
            if not changes:
                out = row_to_page(row)
                out['unchanged'] = True
                return out
            sets, args = [], []
            for k, (_, v) in changes.items():
                sets.append(f'{_COLUMN.get(k, k)} = ?')
                if k == 'collections':
                    v = json.dumps(v)
                elif k == 'tab':
                    v = int(v)
                args.append(v)
            now = utcnow()
            cur = conn.execute(
                f'UPDATE app_pages SET {", ".join(sets)}, version = version + 1, updated_at = ?, updated_by = ?, '
                'source = COALESCE(?, source) WHERE id = ? AND version = ?',
                (*args, now, by, json.dumps(source) if source else None, pid, expected_version))
            if cur.rowcount != 1:
                raise SchemaError(409, 'version_conflict', 'Someone else changed this page entry first.')
            self._activity(conn, collection='app_pages', record_id=pid, type_='page_updated', by=by,
                           source=source, diff={'changed': changes})
            row = conn.execute('SELECT * FROM app_pages WHERE id = ?', (pid,)).fetchone()
        return row_to_page(row)

    def page_set_archived(self, pid, archived: bool, *, by, source=None, agent=False) -> dict:
        from services.biz.store import utcnow
        self._writable()
        source = self.check_source(source, agent=agent)
        self.page_get(pid)
        with self._tx() as conn:
            row = conn.execute('SELECT * FROM app_pages WHERE id = ?', (pid,)).fetchone()
            if bool(row['archived_at']) == archived:
                return row_to_page(row)
            now = utcnow()
            conn.execute('UPDATE app_pages SET archived_at = ?, version = version + 1, updated_at = ?, '
                         'updated_by = ? WHERE id = ?', (now if archived else None, now, by, pid))
            self._activity(conn, collection='app_pages', record_id=pid,
                           type_='page_archived' if archived else 'page_unarchived', by=by, source=source)
            row = conn.execute('SELECT * FROM app_pages WHERE id = ?', (pid,)).fetchone()
        return row_to_page(row)

    def company_brand(self) -> dict:
        with self._read() as conn:
            row = conn.execute("SELECT data FROM records WHERE collection = 'company' AND archived_at IS NULL "
                               'ORDER BY created_at LIMIT 1').fetchone()
        data = json.loads(row['data']) if row else {}
        brand = data.get('brand') if isinstance(data.get('brand'), dict) else {}
        logo = brand.get('logo') if isinstance(brand.get('logo'), str) else None
        if logo and ('..' in logo or not logo.startswith(('/uploads/', '/pages/'))):
            logo = None          # only this business's own files; never a remote URL in the shell
        return {'name': data.get('name') if isinstance(data.get('name'), str) else None,
                'logo': logo, 'colors': brand.get('colors') if isinstance(brand.get('colors'), dict) else {},
                'fonts': brand.get('fonts') if isinstance(brand.get('fonts'), dict) else {}}


# ── the shell model ─────────────────────────────────────────────────────────
THEME_TOKENS = ('bg', 'surface', 'surface-2', 'border', 'text', 'text-secondary', 'text-muted', 'heading',
                'brand', 'brand-contrast', 'accent', 'radius', 'font-body', 'font-display')
# company.brand.colors keys (owner words) -> theme tokens
BRAND_COLOR_MAP = {'primary': 'brand', 'brand': 'brand', 'secondary': 'accent', 'accent': 'accent',
                   'background': 'bg', 'surface': 'surface', 'text': 'text', 'heading': 'heading',
                   'on_primary': 'brand-contrast'}


def build_theme(style_tokens: dict | None, brand: dict) -> dict:
    """Theme tokens for the shell: the tenant's canvas style, then company brand colours on top.

    Only hex colours, plain font stacks and small radii pass; anything else is dropped,
    so a stored value can never become CSS outside its own property.
    """
    theme = {}
    for k in THEME_TOKENS:
        v = (style_tokens or {}).get(k)
        if isinstance(v, str):
            theme[k] = v.strip()
    for key, token in BRAND_COLOR_MAP.items():
        v = (brand.get('colors') or {}).get(key)
        if isinstance(v, str):
            theme[token] = v.strip()
    for key in ('body', 'display'):
        v = (brand.get('fonts') or {}).get(key)
        if isinstance(v, str):
            theme['font-' + key] = v.strip()
    clean = {}
    for k, v in theme.items():
        if k.startswith('font-'):
            if len(v) <= 200 and not _FONT_BAD_RE.search(v):
                clean[k] = v
        elif k == 'radius':
            if re.fullmatch(r'\d{1,2}(?:\.\d)?px', v):
                clean[k] = v
        elif _HEX_RE.match(v):
            clean[k] = v
    return clean


def shell_model(pages: list, *, company: dict, theme: dict, page_exists=None) -> dict:
    """What /jb/v1/shell.js draws: sections in taxonomy order, phone tabs, icons, company, theme."""
    icons = icon_paths()
    live = [p for p in pages if not p['archived_at'] and (page_exists is None or page_exists(p['file']))]
    sections = []
    for sid, label, icon in SECTIONS:
        items = [p for p in live if p['section'] == sid]
        if not items:
            continue
        sections.append({'id': sid, 'label': label, 'icon': icon, 'pages': [
            {'id': p['id'], 'file': p['file'], 'label': p['label'], 'kind': p['kind'],
             'icon': p['icon'] or icon, 'verb': p['verb'], 'verb_href': p['verb_href'],
             'shell': p['shell'], 'tab': p['tab']} for p in items]})
    flat = [pg for s in sections for pg in s['pages']]
    tabs = []
    if sections and sections[0]['id'] == 'home':
        tabs.append(sections[0]['pages'][0]['id'])
    for pg in flat:
        if len(tabs) >= MAX_TABS - 1:
            break
        if pg['tab'] and pg['id'] not in tabs:
            tabs.append(pg['id'])
    if len(flat) <= MAX_TABS:      # a small system: every page fits on the bar, no "More" needed
        tabs = [pg['id'] for pg in flat]
    used = {s['icon'] for s in sections} | {pg['icon'] for pg in flat} | set(_SHELL_CHROME_ICONS)
    return {
        'sections': sections, 'tabs': tabs, 'more': len(flat) > len(tabs), 'count': len(flat),
        'company': {'name': company.get('name'), 'logo': company.get('logo')},
        'theme': theme, 'icons': {k: icons[k] for k in sorted(used) if k in icons},
        'section_taxonomy': [{'id': s, 'label': lab} for s, lab, _ in SECTIONS],
    }
