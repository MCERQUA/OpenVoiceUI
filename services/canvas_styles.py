"""Canvas Style System — per-tenant design-system store + renderer.

Styles are DATA, not prose. Each style is a JSON definition (tokens + agent
instructions + a full template/demo page). The tenant picks one via the
Canvas Styles picker page; activation renders:

  canvas-pages/canvas-styles/ACTIVE-STYLE.md      <- agent reads this before building any page
  canvas-pages/canvas-styles/active-template.html <- agent copies this as the starting point
  canvas-pages/canvas-styles/active.json          <- {style_id, activated_at}

Everything lives under CANVAS_PAGES_DIR/canvas-styles/ because that dir is
bind-mounted into BOTH the openvoiceui and openclaw containers at
/app/runtime/canvas-pages — the agent sees activation instantly with no
compose or image change.

Built-in presets ship with the app in default-styles/*.json (read-only).
Tenant customs live in canvas-pages/canvas-styles/custom/*.json.
Customs are never deleted — "delete" archives them (archived: true).
"""
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from services.paths import APP_ROOT, CANVAS_PAGES_DIR

logger = logging.getLogger(__name__)

PRESETS_DIR = APP_ROOT / 'default-styles'
STYLES_DIR = CANVAS_PAGES_DIR / 'canvas-styles'
CUSTOM_DIR = STYLES_DIR / 'custom'
ACTIVE_FILE = STYLES_DIR / 'active.json'
ACTIVE_STYLE_MD = STYLES_DIR / 'ACTIVE-STYLE.md'
ACTIVE_TEMPLATE = STYLES_DIR / 'active-template.html'

# The id the serve-time injector uses when a page predates the style system
# (or no style is active). Matches the historical hardcoded dark base.
LEGACY_STYLE_ID = 'legacy-dark'

_lock = threading.Lock()

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,63}$')
_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$')

# Tokens that must exist and be hex colors — the serve-time injector and the
# picker preview both rely on them.
REQUIRED_COLOR_TOKENS = ('bg', 'surface', 'text', 'heading', 'brand')


def _validate(style: dict) -> str | None:
    """Return an error string, or None if the definition is acceptable."""
    if not isinstance(style, dict):
        return 'style must be an object'
    sid = style.get('id', '')
    if not isinstance(sid, str) or not _ID_RE.fullmatch(sid):
        return 'id must be a lowercase slug (a-z, 0-9, dashes, 2-64 chars)'
    if not isinstance(style.get('name'), str) or not style['name'].strip():
        return 'name is required'
    if style.get('base') not in ('light', 'dark'):
        return "base must be 'light' or 'dark'"
    tokens = style.get('tokens')
    if not isinstance(tokens, dict):
        return 'tokens object is required'
    for key in REQUIRED_COLOR_TOKENS:
        val = tokens.get(key)
        if not isinstance(val, str) or not _HEX_RE.fullmatch(val.strip()):
            return f'tokens.{key} must be a #rrggbb hex color'
    if not isinstance(style.get('instructions'), str) or len(style['instructions'].strip()) < 40:
        return 'instructions (agent design guidance, markdown) is required'
    if not isinstance(style.get('template_html'), str) or '<html' not in style['template_html'].lower():
        return 'template_html must be a complete HTML document'
    return None


def _ensure_dirs():
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)


def load_presets() -> dict[str, dict]:
    """Built-in styles shipped with the app image (read-only)."""
    presets = {}
    if PRESETS_DIR.exists():
        for f in sorted(PRESETS_DIR.glob('*.json')):
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                data['preset'] = True
                presets[data['id']] = data
            except Exception as exc:
                logger.error('Bad preset %s: %s', f, exc)
    return presets


def load_customs() -> dict[str, dict]:
    customs = {}
    if CUSTOM_DIR.exists():
        for f in sorted(CUSTOM_DIR.glob('*.json')):
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                data['preset'] = False
                customs[data['id']] = data
            except Exception as exc:
                logger.error('Bad custom style %s: %s', f, exc)
    return customs


def get_style(style_id: str) -> dict | None:
    """Custom styles shadow presets with the same id (clone-and-edit keeps id distinct, but be safe)."""
    customs = load_customs()
    if style_id in customs:
        return customs[style_id]
    return load_presets().get(style_id)


def list_styles(include_archived: bool = False) -> list[dict]:
    """Summaries (no template/instructions bodies) of presets + customs."""
    out = []
    merged = {**load_presets(), **load_customs()}
    for sid, s in merged.items():
        if s.get('archived') and not include_archived:
            continue
        out.append({
            'id': sid,
            'name': s.get('name', sid),
            'tagline': s.get('tagline', ''),
            'description': s.get('description', ''),
            'base': s.get('base', 'dark'),
            'preset': bool(s.get('preset')),
            'archived': bool(s.get('archived')),
            'tokens': s.get('tokens', {}),
        })
    return out


def save_custom(style: dict) -> None:
    _ensure_dirs()
    style = dict(style)
    style['preset'] = False
    style.setdefault('created_at', datetime.now().isoformat())
    style['updated_at'] = datetime.now().isoformat()
    path = CUSTOM_DIR / f"{style['id']}.json"
    path.write_text(json.dumps(style, indent=2), encoding='utf-8')


def get_active() -> dict:
    try:
        if ACTIVE_FILE.exists():
            return json.loads(ACTIVE_FILE.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning('Failed reading active style pointer: %s', exc)
    return {'style_id': None}


def get_active_style_id() -> str | None:
    return get_active().get('style_id')


def apply_tokens_to_template(template_html: str, tokens: dict) -> str:
    """Re-bind token values into the template's CSS custom properties.

    Templates declare :root{--bg:…;--brand:…} matching token names. When a
    clone edits tokens, the stored template still carries the source style's
    literal values — rewrite every `--<token>:` declaration so token edits
    actually change the rendered page. Safe on originals (no-op rewrite).
    """
    out = template_html
    for key, val in tokens.items():
        if not isinstance(val, str) or not val.strip():
            continue
        out = re.sub(
            r'(--' + re.escape(key) + r'\s*:\s*)[^;}]+',
            lambda m: m.group(1) + val.strip(),
            out,
        )
    return out


def rendered_template(style: dict) -> str:
    return apply_tokens_to_template(style.get('template_html', ''), style.get('tokens', {}))


def _token_table_md(tokens: dict) -> str:
    lines = ['| Token | Value |', '|---|---|']
    for k, v in tokens.items():
        lines.append(f'| `--{k}` | `{v}` |')
    return '\n'.join(lines)


def render_active_files(style: dict) -> None:
    """Write ACTIVE-STYLE.md + active-template.html for the agent to consume."""
    _ensure_dirs()
    md = (
        f"# ACTIVE CANVAS STYLE: {style['name']}\n\n"
        f"> {style.get('tagline', '')}\n>\n"
        f"> This is the design system the user chose for THEIR canvas pages.\n"
        f"> Every canvas page you create MUST follow it. Do not fall back to the\n"
        f"> old dark default. Start every new page by COPYING THE FILE —\n"
        f"> `cp /app/runtime/canvas-pages/canvas-styles/active-template.html /app/runtime/canvas-pages/<new-page>.html`\n"
        f"> — then EDIT the copy: replace demo content section by section, keep the\n"
        f"> full <style> block. NEVER retype/regenerate the whole template by hand\n"
        f"> (it is ~30KB; regenerating it takes minutes and introduces drift).\n\n"
        f"**Base mode:** {style.get('base')}\n\n"
        f"## Design tokens\n\n{_token_table_md(style.get('tokens', {}))}\n\n"
        f"## Design rules\n\n{style['instructions'].strip()}\n\n"
        f"## Layout & mobile width (UNIVERSAL — applies to every style)\n\n"
        f"The host injects a 25px safe-area padding on html/body. That is the ONLY\n"
        f"horizontal inset content needs. NEVER stack more side padding on mobile:\n"
        f"- Page wrappers: `max-width` + `margin:0 auto` for desktop, but at small\n"
        f"  sizes side padding must be 0 — e.g. `@media (max-width:640px){{ .wrap{{padding-left:0;padding-right:0}} }}`\n"
        f"- Cards/sections keep only their own internal padding (16-20px max on mobile).\n"
        f"- Text gets the widest possible measure on phones; backgrounds/washes may\n"
        f"  bleed edge-to-edge (negative margins into the safe area are fine for\n"
        f"  decorative backgrounds — just never for text).\n"
        f"- Never nest padded container inside padded container on mobile.\n\n"
        f"## Icons\n\nNEVER use emoji as UI icons. Copy professional inline SVG icons from\n"
        f"`/app/runtime/canvas-pages/canvas-styles/icons.html` (stroke:currentColor — they\n"
        f"inherit this style's colors automatically).\n\n"
        f"## Self-check\n\nAfter writing a page, run\n"
        f"`curl -s http://openvoiceui:5001/api/canvas/lint/<page-id>` and fix every issue it reports.\n\n"
        f"---\n_Rendered {datetime.now().isoformat()} from style `{style['id']}`"
        f" ({'preset' if style.get('preset') else 'custom'}). Do not edit this file"
        f" — change styles via the Canvas Styles page._\n"
    )
    ACTIVE_STYLE_MD.write_text(md, encoding='utf-8')
    ACTIVE_TEMPLATE.write_text(rendered_template(style), encoding='utf-8')
    (STYLES_DIR / 'icons.html').write_text(_icon_library_html(), encoding='utf-8')


# ---------------------------------------------------------------------------
# Page lint — deterministic quality checks agents can't argue with
# ---------------------------------------------------------------------------

_EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF'
    '⬀-⯿✀-➿️]'
)
_BANNED_HEX = (
    '#764ba2', '#667eea', '#8b5cf6', '#a855f7', '#9333ea', '#7c3aed',
    '#6d28d9', '#c026d3', '#d946ef', '#ec4899', '#db2777',
)


def lint_page_html(html: str) -> list[str]:
    """Return a list of human-readable issues found in canvas page HTML."""
    issues = []
    emojis = _EMOJI_RE.findall(html)
    if emojis:
        sample = ''.join(dict.fromkeys(emojis))[:12]
        issues.append(
            f'EMOJI-AS-UI: {len(emojis)} emoji found ({sample}). Emoji are banned as '
            f'UI icons — replace with inline SVG. Copy professional icons from '
            f'/app/runtime/canvas-pages/canvas-styles/icons.html (or the active template). '
            f'Only keep emoji the user explicitly asked for in content.'
        )
    low = html.lower()
    banned_found = [h for h in _BANNED_HEX if h in low]
    if banned_found:
        issues.append(f'BANNED-COLOR: purple/pink family hex in page: {", ".join(banned_found)}')
    if 'cdn.tailwindcss.com' in low or 'stackpath.bootstrapcdn' in low or 'cdn.jsdelivr.net/npm/bootstrap' in low:
        issues.append('CDN-FRAMEWORK: Tailwind/Bootstrap CDN scripts are banned on canvas pages — inline all CSS.')
    if '<meta name="viewport"' not in low:
        issues.append('MISSING-VIEWPORT: add <meta name="viewport" content="width=device-width, initial-scale=1">')
    if 'page-icon' not in low:
        issues.append('MISSING-PAGE-ICON: add <meta name="page-icon" content="..."> for the desktop icon.')
    active = get_active_style_id()
    if active:
        style = get_style(active)
        if style and style.get('base') == 'light' and ('background:#0a0a0a' in low.replace(' ', '') or 'background:#0d1117' in low.replace(' ', '')):
            issues.append(
                f'STYLE-MISMATCH: page uses the old dark default background but the active '
                f'style "{active}" is a light theme. Rebuild from canvas-styles/active-template.html.'
            )
    return issues


# Style-agnostic inline SVG icon set (stroke:currentColor so it inherits any
# style's palette). Written to canvas-styles/icons.html on activation so
# agents have a copy-paste source instead of falling back to emoji.
_ICON_PATHS = {
    'dashboard': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    'chart': '<line x1="4" y1="20" x2="20" y2="20"/><rect x="6" y="11" width="3" height="6" rx="0.5"/><rect x="11" y="7" width="3" height="10" rx="0.5"/><rect x="16" y="13" width="3" height="4" rx="0.5"/>',
    'trend-up': '<polyline points="3 17 9 11 13 15 21 7"/><polyline points="15 7 21 7 21 13"/>',
    'users': '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20v-1a6.5 6.5 0 0 1 13 0v1"/><path d="M16 5a3.5 3.5 0 0 1 0 7"/><path d="M18.5 13.5a6.5 6.5 0 0 1 3 5.5v1"/>',
    'user': '<circle cx="12" cy="8" r="4"/><path d="M4.5 21v-1a7.5 7.5 0 0 1 15 0v1"/>',
    'check-circle': '<circle cx="12" cy="12" r="9"/><polyline points="8.5 12.5 11 15 16 9.5"/>',
    'x-circle': '<circle cx="12" cy="12" r="9"/><line x1="9.5" y1="9.5" x2="14.5" y2="14.5"/><line x1="14.5" y1="9.5" x2="9.5" y2="14.5"/>',
    'alert': '<path d="M12 3 2.5 20h19L12 3z"/><line x1="12" y1="10" x2="12" y2="14"/><line x1="12" y1="17" x2="12" y2="17.01"/>',
    'info': '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><line x1="12" y1="8" x2="12" y2="8.01"/>',
    'dollar': '<line x1="12" y1="2.5" x2="12" y2="21.5"/><path d="M17 6.5H9.5a3.25 3.25 0 0 0 0 6.5h5a3.25 3.25 0 0 1 0 6.5H6.5"/>',
    'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/><line x1="3" y1="10" x2="21" y2="10"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
    'mail': '<rect x="3" y="5" width="18" height="14" rx="2"/><polyline points="3.5 7 12 13 20.5 7"/>',
    'phone': '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.5" y1="15.5" x2="21" y2="21"/>',
    'download': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="3" x2="12" y2="15"/>',
    'upload': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 8 12 3 17 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    'edit': '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>',
    'plus': '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    'star': '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26"/>',
    'home': '<path d="M3 9.5 12 3l9 6.5V20a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 20z"/><polyline points="9 21.5 9 13 15 13 15 21.5"/>',
    'folder': '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    'document': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/>',
    'globe': '<circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/><path d="M12 3a14.5 14.5 0 0 1 4 9 14.5 14.5 0 0 1-4 9 14.5 14.5 0 0 1-4-9 14.5 14.5 0 0 1 4-9z"/>',
    'shield': '<path d="M12 22s8-4 8-10V5.5L12 2.5 4 5.5V12c0 6 8 10 8 10z"/>',
    'zap': '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10"/>',
    'settings': '<line x1="5" y1="4" x2="5" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/><line x1="19" y1="4" x2="19" y2="20"/><circle cx="5" cy="9" r="2"/><circle cx="12" cy="15" r="2"/><circle cx="19" cy="8" r="2"/>',
    'arrow-right': '<line x1="4" y1="12" x2="20" y2="12"/><polyline points="13 5 20 12 13 19"/>',
}


def _icon_library_html() -> str:
    cells = []
    for name, body in _ICON_PATHS.items():
        svg = (f'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" '
               f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               f'stroke-linejoin="round" aria-hidden="true">{body}</svg>')
        code = svg.replace('&', '&amp;').replace('<', '&lt;')
        cells.append(f'<div class="cell"><div class="glyph">{svg}</div>'
                     f'<div class="name">{name}</div><pre>{code}</pre></div>')
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Canvas Icon Library</title><style>'
        'body{font-family:system-ui,sans-serif;line-height:1.4}'
        'h1{font-size:20px} p{max-width:70ch}'
        '.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}'
        '.cell{border:1px solid rgba(128,128,128,.35);border-radius:10px;padding:12px}'
        '.glyph{margin-bottom:6px}.name{font-weight:600;font-size:13px;margin-bottom:6px}'
        'pre{font-size:10px;white-space:pre-wrap;word-break:break-all;opacity:.75;margin:0}'
        '</style></head><body><h1>Canvas Icon Library</h1>'
        '<p>Professional inline SVG icons for canvas pages. stroke=currentColor so they '
        'inherit any style\'s palette. COPY the markup — never use emoji as UI icons.</p>'
        f'<div class="grid">{"".join(cells)}</div></body></html>'
    )


def icon_svg(name: str, size: int = 24) -> str | None:
    body = _ICON_PATHS.get(name)
    if body is None:
        return None
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{body}</svg>')


def activate(style_id: str) -> dict | None:
    """Set the tenant's active style and render agent-facing files."""
    style = get_style(style_id)
    if style is None:
        return None
    with _lock:
        _ensure_dirs()
        ACTIVE_FILE.write_text(json.dumps({
            'style_id': style_id,
            'activated_at': datetime.now().isoformat(),
        }, indent=2), encoding='utf-8')
        render_active_files(style)
    return style


# ---------------------------------------------------------------------------
# Serve-time base CSS
# ---------------------------------------------------------------------------

_LEGACY_TOKENS = {
    'bg': '#0a0a0a', 'text': '#e2e8f0', 'heading': '#ffffff',
    'brand': '#fb923c', 'scrollbar': '#3a3a42',
}

_base_css_cache: dict[str, bytes] = {}


def clear_base_css_cache() -> None:
    _base_css_cache.clear()


def base_css_for(style_id: str | None) -> bytes:
    """The <style id="canvas-base-styles"> block injected into served pages.

    Colors are wrapped in :where() so they carry ZERO specificity — they are a
    fallback for unstyled markup and can never override page-authored CSS
    (the old hardcoded block silently beat page body{background} rules).
    Padding + scrollbar rules keep their historical !important behavior.
    """
    key = style_id or LEGACY_STYLE_ID
    cached = _base_css_cache.get(key)
    if cached is not None:
        return cached

    tokens = dict(_LEGACY_TOKENS)
    if style_id and style_id != LEGACY_STYLE_ID:
        style = get_style(style_id)
        if style:
            t = style.get('tokens', {})
            tokens['bg'] = t.get('bg', tokens['bg'])
            tokens['text'] = t.get('text', tokens['text'])
            tokens['heading'] = t.get('heading', tokens['text'])
            tokens['brand'] = t.get('brand', tokens['brand'])
            tokens['scrollbar'] = t.get(
                'scrollbar',
                '#c4cad4' if style.get('base') == 'light' else '#3a3a42')

    css = (
        '<style id="canvas-base-styles">'
        ':root{'
        '--canvas-safe-top:25px;'
        '--canvas-safe-right:25px;'
        '--canvas-safe-bottom:25px;'
        '--canvas-safe-left:25px;}'
        'html,body{'
        'padding:25px!important;'
        'box-sizing:border-box!important;}'
        ':where(html,body){'
        f"color:{tokens['text']};"
        f"background:{tokens['bg']};}}"
        f":where(h1,h2,h3,h4){{color:{tokens['heading']};}}"
        f":where(a){{color:{tokens['brand']};}}"
        '*,html,body{scrollbar-width:thin;'
        f"scrollbar-color:{tokens['scrollbar']} transparent;}}"
        '::-webkit-scrollbar{width:5px!important;height:5px!important;}'
        '::-webkit-scrollbar-track{background:transparent!important;}'
        f"::-webkit-scrollbar-thumb{{background:{tokens['scrollbar']}!important;border-radius:99px!important;}}"
        '::-webkit-scrollbar-thumb:hover{background:#555!important;}'
        '</style>'
    )
    encoded = css.encode('utf-8')
    _base_css_cache[key] = encoded
    return encoded
