#!/usr/bin/env python3
"""Assemble default-styles/<id>.json from default-styles/src/<id>/ parts.

Usage: python3 default-styles/build.py [id ...]   (no args = all)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / 'src'


def build(style_dir: Path) -> str:
    meta = json.loads((style_dir / 'style.json').read_text(encoding='utf-8'))
    meta['instructions'] = (style_dir / 'instructions.md').read_text(encoding='utf-8')
    meta['template_html'] = (style_dir / 'template.html').read_text(encoding='utf-8')
    out = HERE / f"{meta['id']}.json"
    out.write_text(json.dumps(meta, indent=2), encoding='utf-8')
    return str(out)


def main():
    ids = sys.argv[1:]
    dirs = [SRC / i for i in ids] if ids else sorted(d for d in SRC.iterdir() if d.is_dir())
    for d in dirs:
        print('built', build(d))


if __name__ == '__main__':
    main()
