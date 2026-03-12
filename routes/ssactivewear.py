"""
routes/ssactivewear.py — S&S Activewear API Proxy Blueprint

Proxies requests to the S&S Activewear wholesale apparel API so that
canvas pages can search products, check inventory, and display images
without exposing API credentials to the browser.

Endpoints:
  GET /api/ssactivewear/search?q=<query>           — search styles
  GET /api/ssactivewear/products?style=<id>         — get products by style
  GET /api/ssactivewear/products/<sku>              — get product by SKU
  GET /api/ssactivewear/inventory?style=<id>        — check inventory
  GET /api/ssactivewear/inventory/<sku>             — check inventory by SKU
  GET /api/ssactivewear/brands                      — list all brands
  GET /api/ssactivewear/categories                  — list all categories
  GET /api/ssactivewear/transit/<zip>               — days in transit

Requires env vars: SS_ACCOUNT, SS_API_KEY
"""

import logging
import os

import requests as http_requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

ssactivewear_bp = Blueprint('ssactivewear', __name__)

SS_BASE = os.environ.get('SS_API_BASE', 'https://api-ca.ssactivewear.com/v2')
SS_IMAGE_BASE = 'https://www.ssactivewear.com/'


def _get_auth():
    """Return (account, key) tuple or None if not configured."""
    account = os.environ.get('SS_ACCOUNT')
    key = os.environ.get('SS_API_KEY')
    if not account or not key:
        return None
    return (account, key)


def _ss_get(path, params=None):
    """Make authenticated GET to S&S API. Returns (data, error, status_code)."""
    auth = _get_auth()
    if not auth:
        return None, 'S&S Activewear not configured (missing SS_ACCOUNT/SS_API_KEY)', 503

    url = f'{SS_BASE}/{path.lstrip("/")}'
    try:
        resp = http_requests.get(url, auth=auth, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json(), None, 200
        elif resp.status_code == 404:
            return [], None, 200  # Not found = empty results
        elif resp.status_code == 429:
            return None, 'Rate limit exceeded. Try again in a moment.', 429
        else:
            return None, f'S&S API error: {resp.status_code}', resp.status_code
    except http_requests.exceptions.Timeout:
        return None, 'S&S API timeout', 504
    except Exception as e:
        logger.error(f'S&S API request failed: {e}')
        return None, str(e), 500


def _fix_image_url(relative_path):
    """Convert relative S&S image path to full URL."""
    if not relative_path:
        return ''
    if relative_path.startswith('http'):
        return relative_path
    return SS_IMAGE_BASE + relative_path


def _format_product(p):
    """Format a raw S&S product object for the frontend."""
    return {
        'sku': p.get('sku', ''),
        'gtin': p.get('gtin', ''),
        'skuID': p.get('skuID_Master', ''),
        'styleID': p.get('styleID', ''),
        'brand': p.get('brandName', ''),
        'style': p.get('styleName', ''),
        'title': f"{p.get('brandName', '')} {p.get('styleName', '')}",
        'color': p.get('colorName', ''),
        'colorCode': p.get('colorCode', ''),
        'colorFamily': p.get('colorFamily', ''),
        'size': p.get('sizeName', ''),
        'sizeOrder': p.get('sizeOrder', ''),
        'price': p.get('customerPrice') or p.get('piecePrice', 0),
        'piecePrice': p.get('piecePrice', 0),
        'dozenPrice': p.get('dozenPrice', 0),
        'casePrice': p.get('casePrice', 0),
        'salePrice': p.get('salePrice', 0),
        'mapPrice': p.get('mapPrice', 0),
        'qty': p.get('qty', 0),
        'caseQty': p.get('caseQty', 0),
        'countryOfOrigin': p.get('countryOfOrigin', ''),
        'image': _fix_image_url(p.get('colorFrontImage', '')),
        'imageBack': _fix_image_url(p.get('colorBackImage', '')),
        'imageSide': _fix_image_url(p.get('colorSideImage', '')),
        'imageOnModel': _fix_image_url(p.get('colorOnModelFrontImage', '')),
        'swatch': _fix_image_url(p.get('colorSwatchImage', '')),
        'warehouses': p.get('warehouses', []),
    }


def _format_style(s):
    """Format a raw S&S style object for the frontend."""
    return {
        'styleID': s.get('styleID', ''),
        'partNumber': s.get('partNumber', ''),
        'brand': s.get('brandName', ''),
        'style': s.get('styleName', ''),
        'title': s.get('title', ''),
        'description': s.get('description', ''),
        'baseCategory': s.get('baseCategory', ''),
        'image': _fix_image_url(s.get('styleImage', '')),
        'brandImage': _fix_image_url(s.get('brandImage', '')),
        'newStyle': s.get('newStyle', False),
    }


@ssactivewear_bp.route('/api/ssactivewear/search')
def ss_search():
    """Search styles by keyword. Returns formatted style objects with images."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'styles': [], 'error': 'Missing search query'}), 400

    data, err, status = _ss_get('styles/', params={'search': q})
    if err:
        return jsonify({'styles': [], 'error': err}), status

    styles = [_format_style(s) for s in (data or [])]
    return jsonify({'styles': styles, 'count': len(styles)})


@ssactivewear_bp.route('/api/ssactivewear/products')
def ss_products():
    """Get products by style/partnumber/styleid. Returns all SKUs with images."""
    style = request.args.get('style')
    partnumber = request.args.get('partnumber')
    styleid = request.args.get('styleid')
    warehouses = request.args.get('warehouses')

    params = {}
    if style:
        params['style'] = style
    elif partnumber:
        params['partnumber'] = partnumber
    elif styleid:
        params['styleid'] = styleid
    else:
        return jsonify({'products': [], 'error': 'Provide style, partnumber, or styleid'}), 400

    if warehouses:
        params['Warehouses'] = warehouses

    data, err, status = _ss_get('products/', params=params)
    if err:
        return jsonify({'products': [], 'error': err}), status

    products = [_format_product(p) for p in (data or [])]
    return jsonify({'products': products, 'count': len(products)})


@ssactivewear_bp.route('/api/ssactivewear/products/<path:sku>')
def ss_product_by_sku(sku):
    """Get product(s) by SKU, GTIN, or SkuID. Comma-separated OK."""
    warehouses = request.args.get('warehouses')
    params = {}
    if warehouses:
        params['Warehouses'] = warehouses

    data, err, status = _ss_get(f'products/{sku}', params=params)
    if err:
        return jsonify({'products': [], 'error': err}), status

    products = [_format_product(p) for p in (data or [])]
    return jsonify({'products': products, 'count': len(products)})


@ssactivewear_bp.route('/api/ssactivewear/inventory')
def ss_inventory():
    """Check inventory by style/partnumber/styleid."""
    style = request.args.get('style')
    partnumber = request.args.get('partnumber')
    styleid = request.args.get('styleid')
    warehouses = request.args.get('warehouses')

    params = {}
    if style:
        params['style'] = style
    elif partnumber:
        params['partnumber'] = partnumber
    elif styleid:
        params['styleid'] = styleid
    else:
        return jsonify({'inventory': [], 'error': 'Provide style, partnumber, or styleid'}), 400

    if warehouses:
        params['Warehouses'] = warehouses

    data, err, status = _ss_get('inventory/', params=params)
    if err:
        return jsonify({'inventory': [], 'error': err}), status

    return jsonify({'inventory': data or [], 'count': len(data or [])})


@ssactivewear_bp.route('/api/ssactivewear/inventory/<path:sku>')
def ss_inventory_by_sku(sku):
    """Check inventory by SKU/GTIN. Comma-separated OK."""
    warehouses = request.args.get('warehouses')
    params = {}
    if warehouses:
        params['Warehouses'] = warehouses

    data, err, status = _ss_get(f'inventory/{sku}', params=params)
    if err:
        return jsonify({'inventory': [], 'error': err}), status

    return jsonify({'inventory': data or [], 'count': len(data or [])})


@ssactivewear_bp.route('/api/ssactivewear/brands')
def ss_brands():
    """List all brands."""
    data, err, status = _ss_get('brands/')
    if err:
        return jsonify({'brands': [], 'error': err}), status

    brands = [{'id': b.get('brandID'), 'name': b.get('name'),
               'image': _fix_image_url(b.get('image', '')),
               'noeRetailing': b.get('noeRetailing', False)}
              for b in (data or [])]
    return jsonify({'brands': brands, 'count': len(brands)})


@ssactivewear_bp.route('/api/ssactivewear/categories')
def ss_categories():
    """List all categories."""
    data, err, status = _ss_get('categories/')
    if err:
        return jsonify({'categories': [], 'error': err}), status

    categories = [{'id': c.get('categoryID'), 'name': c.get('name')}
                  for c in (data or [])]
    return jsonify({'categories': categories, 'count': len(categories)})


@ssactivewear_bp.route('/api/ssactivewear/transit/<zip_code>')
def ss_transit(zip_code):
    """Get days in transit from each warehouse to a ZIP code."""
    data, err, status = _ss_get(f'daysintransit/{zip_code}')
    if err:
        return jsonify({'transit': [], 'error': err}), status

    return jsonify({'transit': data or []})
