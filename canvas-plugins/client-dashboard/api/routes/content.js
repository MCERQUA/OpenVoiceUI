/**
 * Content API routes - Website content management
 */

import express from 'express';
import { promises as fs } from 'fs';
import path from 'path';
import { query, getTenantId } from '../lib/db.js';
import { optimizeTitle } from '../lib/ai.js';

const router = express.Router();

const WEBSITES_DIR = process.env.WEBSITES_DIR || '/mnt/HC_Volume_104748920/websites';

/**
 * GET /content/topical-map - Get topical map for a website
 */
router.get('/topical-map', async (req, res) => {
  try {
    const { domain } = req.query;

    if (!domain) {
      return res.status(400).json({ error: 'domain required' });
    }

    const topicalMapPath = path.join(WEBSITES_DIR, domain, 'AI', 'knowledge', 'topical-map.json');

    try {
      const content = await fs.readFile(topicalMapPath, 'utf-8');
      const data = JSON.parse(content);
      res.json(data);
    } catch (fileError) {
      // Return empty structure if file doesn't exist
      res.json({
        pillars: [],
        lastUpdated: null,
        error: 'Topical map not found'
      });
    }
  } catch (error) {
    console.error('Error reading topical map:', error);
    res.status(500).json({ error: 'Failed to read topical map' });
  }
});

/**
 * GET /content/stats - Get content statistics
 */
router.get('/stats', async (req, res) => {
  try {
    const tenantId = getTenantId();

    // This would normally query the article queue
    // For now, return placeholder stats
    res.json({
      articles: 0,
      planned: 0,
      researching: 0,
      drafting: 0,
      published: 0
    });
  } catch (error) {
    console.error('Error fetching content stats:', error);
    res.status(500).json({ error: 'Failed to fetch content stats' });
  }
});

/**
 * GET /content/article-queue - Get article queue
 */
router.get('/article-queue', async (req, res) => {
  try {
    const { domain } = req.query;

    if (!domain) {
      return res.status(400).json({ error: 'domain required' });
    }

    const queuePath = path.join(WEBSITES_DIR, domain, 'AI', 'knowledge', 'article-queue.json');

    try {
      const content = await fs.readFile(queuePath, 'utf-8');
      const data = JSON.parse(content);
      res.json(data);
    } catch (fileError) {
      res.json({
        articles: [],
        lastUpdated: null
      });
    }
  } catch (error) {
    console.error('Error reading article queue:', error);
    res.status(500).json({ error: 'Failed to read article queue' });
  }
});

/**
 * GET /content/profile - Get client profile
 */
router.get('/profile', async (req, res) => {
  try {
    const { domain } = req.query;

    if (!domain) {
      return res.status(400).json({ error: 'domain required' });
    }

    const profilePath = path.join(WEBSITES_DIR, domain, 'AI', 'CLIENT-PROFILE.md');

    try {
      const content = await fs.readFile(profilePath, 'utf-8');
      res.json({
        content,
        format: 'markdown'
      });
    } catch (fileError) {
      res.json({
        content: null,
        error: 'Profile not found'
      });
    }
  } catch (error) {
    console.error('Error reading profile:', error);
    res.status(500).json({ error: 'Failed to read profile' });
  }
});

/**
 * POST /content/optimize-title - Optimize a title for SEO
 */
router.post('/optimize-title', async (req, res) => {
  try {
    const { title, keywords } = req.body;

    if (!title) {
      return res.status(400).json({ error: 'title required' });
    }

    const optimizedTitle = await optimizeTitle(title, keywords || []);

    res.json({
      original: title,
      optimized: optimizedTitle
    });
  } catch (error) {
    console.error('Error optimizing title:', error);
    res.status(500).json({ error: 'Failed to optimize title' });
  }
});

/**
 * GET /content/library - Get content library (images, videos)
 */
router.get('/library', async (req, res) => {
  try {
    const { brand_id } = req.query;
    const tenantId = getTenantId();

    // Query posts with images
    const posts = await query`
      SELECT DISTINCT ON (p.image_filename)
        p.image_filename,
        p.image_url,
        p.title,
        p.created_at
      FROM posts p
      JOIN brands b ON p.brand_id = b.id
      WHERE b.tenant_id = ${tenantId}
        AND p.image_url IS NOT NULL
        ${brand_id ? query`AND p.brand_id = ${brand_id}` : query``}
      ORDER BY p.image_filename, p.created_at DESC
      LIMIT 100
    `;

    res.json(posts);
  } catch (error) {
    console.error('Error fetching library:', error);
    res.status(500).json({ error: 'Failed to fetch library' });
  }
});

export default router;
