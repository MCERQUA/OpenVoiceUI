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
        f"> old dark default. Start every new page by copying\n"
        f"> `/app/runtime/canvas-pages/canvas-styles/active-template.html`\n"
        f"> (full <style> block included) and replace the demo content.\n\n"
        f"**Base mode:** {style.get('base')}\n\n"
        f"## Design tokens\n\n{_token_table_md(style.get('tokens', {}))}\n\n"
        f"## Design rules\n\n{style['instructions'].strip()}\n\n"
        f"---\n_Rendered {datetime.now().isoformat()} from style `{style['id']}`"
        f" ({'preset' if style.get('preset') else 'custom'}). Do not edit this file"
        f" — change styles via the Canvas Styles page._\n"
    )
    ACTIVE_STYLE_MD.write_text(md, encoding='utf-8')
    ACTIVE_TEMPLATE.write_text(rendered_template(style), encoding='utf-8')


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
