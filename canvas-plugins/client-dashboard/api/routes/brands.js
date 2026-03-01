/**
 * Brands API routes
 */

import express from 'express';
import { query, getTenantId, getTenantBrands } from '../lib/db.js';
import * as oneup from '../lib/oneup.js';

const router = express.Router();

/**
 * GET /brands - List all brands for tenant
 */
router.get('/', async (req, res) => {
  try {
    const brands = await getTenantBrands();
    res.json(brands);
  } catch (error) {
    console.error('Error fetching brands:', error);
    res.status(500).json({ error: 'Failed to fetch brands' });
  }
});

/**
 * GET /brands/:slug - Get brand details
 */
router.get('/:slug', async (req, res) => {
  try {
    const { slug } = req.params;
    const tenantId = getTenantId();

    const brands = await query`
      SELECT *
      FROM brands
      WHERE slug = ${slug} AND tenant_id = ${tenantId}
      LIMIT 1
    `;

    if (brands.length === 0) {
      return res.status(404).json({ error: 'Brand not found' });
    }

    res.json(brands[0]);
  } catch (error) {
    console.error('Error fetching brand:', error);
    res.status(500).json({ error: 'Failed to fetch brand' });
  }
});

/**
 * GET /brands/:slug/stats - Get brand statistics
 */
router.get('/:slug/stats', async (req, res) => {
  try {
    const { slug } = req.params;
    const tenantId = getTenantId();

    // Get brand ID
    const brands = await query`
      SELECT id FROM brands
      WHERE slug = ${slug} AND tenant_id = ${tenantId}
      LIMIT 1
    `;

    if (brands.length === 0) {
      return res.status(404).json({ error: 'Brand not found' });
    }

    const brandId = brands[0].id;

    // Get stats
    const stats = await query`
      SELECT
        COUNT(*) as total_posts,
        COUNT(*) FILTER (WHERE a.status = 'pending') as pending,
        COUNT(*) FILTER (WHERE a.status = 'approved') as approved,
        COUNT(*) FILTER (WHERE a.status = 'rejected') as rejected,
        COUNT(*) FILTER (WHERE a.status = 'published') as published,
        COUNT(*) FILTER (WHERE a.scheduled_for IS NOT NULL) as scheduled
      FROM posts p
      LEFT JOIN approvals a ON p.id = a.post_id
      WHERE p.brand_id = ${brandId}
    `;

    res.json(stats[0] || { total_posts: 0, pending: 0, approved: 0, rejected: 0, published: 0, scheduled: 0 });
  } catch (error) {
    console.error('Error fetching brand stats:', error);
    res.status(500).json({ error: 'Failed to fetch brand stats' });
  }
});

/**
 * GET /brands/:slug/content - Get brand context for AI generation
 */
router.get('/:slug/content', async (req, res) => {
  try {
    const { slug } = req.params;
    const tenantId = getTenantId();

    const brands = await query`
      SELECT b.*, t.primary_color as tenant_color
      FROM brands b
      JOIN tenants t ON b.tenant_id = t.id
      WHERE b.slug = ${slug} AND b.tenant_id = ${tenantId}
      LIMIT 1
    `;

    if (brands.length === 0) {
      return res.status(404).json({ error: 'Brand not found' });
    }

    const brand = brands[0];

    // Build context string
    const context = {
      name: brand.name,
      short_name: brand.short_name,
      color: brand.color || brand.tenant_color,
      website_url: brand.website_url,
      profile: `Brand: ${brand.name}. Website: ${brand.website_url || 'N/A'}`
    };

    res.json(context);
  } catch (error) {
    console.error('Error fetching brand content:', error);
    res.status(500).json({ error: 'Failed to fetch brand content' });
  }
});

/**
 * GET /brands/:slug/accounts - Get OneUp accounts for brand
 */
router.get('/:slug/accounts', async (req, res) => {
  try {
    const { slug } = req.params;
    const tenantId = getTenantId();

    // Get brand's OneUp category
    const brands = await query`
      SELECT oneup_category_id
      FROM brands
      WHERE slug = ${slug} AND tenant_id = ${tenantId}
      LIMIT 1
    `;

    if (brands.length === 0) {
      return res.status(404).json({ error: 'Brand not found' });
    }

    const categoryId = brands[0].oneup_category_id;

    // Get accounts from OneUp
    try {
      const accounts = await oneup.getAccounts();

      // Filter to accounts in this category if specified
      if (categoryId) {
        // OneUp API structure varies, adapt as needed
        res.json(accounts);
      } else {
        res.json(accounts);
      }
    } catch (oneupError) {
      console.error('OneUp error:', oneupError);
      res.json([]); // Return empty if OneUp fails
    }
  } catch (error) {
    console.error('Error fetching accounts:', error);
    res.status(500).json({ error: 'Failed to fetch accounts' });
  }
});

export default router;
