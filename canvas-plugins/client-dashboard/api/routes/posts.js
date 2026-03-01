/**
 * Posts API routes
 */

import express from 'express';
import { query, getTenantId } from '../lib/db.js';

const router = express.Router();

/**
 * GET /posts - List posts with optional filters
 */
router.get('/', async (req, res) => {
  try {
    const { status, brand_id, limit = 50, offset = 0 } = req.query;
    const tenantId = getTenantId();

    let posts;

    if (status) {
      posts = await query`
        SELECT p.*, a.status as approval_status, a.image_status,
               b.name as brand_name, b.slug as brand_slug
        FROM posts p
        LEFT JOIN approvals a ON p.id = a.post_id
        LEFT JOIN brands b ON p.brand_id = b.id
        WHERE b.tenant_id = ${tenantId}
          AND a.status = ${status}
        ORDER BY p.created_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `;
    } else if (brand_id) {
      posts = await query`
        SELECT p.*, a.status as approval_status, a.image_status,
               b.name as brand_name, b.slug as brand_slug
        FROM posts p
        LEFT JOIN approvals a ON p.id = a.post_id
        LEFT JOIN brands b ON p.brand_id = b.id
        WHERE b.tenant_id = ${tenantId}
          AND p.brand_id = ${brand_id}
        ORDER BY p.created_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `;
    } else {
      posts = await query`
        SELECT p.*, a.status as approval_status, a.image_status,
               b.name as brand_name, b.slug as brand_slug
        FROM posts p
        LEFT JOIN approvals a ON p.id = a.post_id
        LEFT JOIN brands b ON p.brand_id = b.id
        WHERE b.tenant_id = ${tenantId}
        ORDER BY p.created_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `;
    }

    res.json(posts);
  } catch (error) {
    console.error('Error fetching posts:', error);
    res.status(500).json({ error: 'Failed to fetch posts' });
  }
});

/**
 * GET /posts/stats - Get post statistics
 */
router.get('/stats', async (req, res) => {
  try {
    const tenantId = getTenantId();

    const stats = await query`
      SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE a.status = 'pending') as pending,
        COUNT(*) FILTER (WHERE a.status = 'approved') as approved,
        COUNT(*) FILTER (WHERE a.status = 'rejected') as rejected,
        COUNT(*) FILTER (WHERE a.status = 'published') as published
      FROM posts p
      JOIN approvals a ON p.id = a.post_id
      JOIN brands b ON p.brand_id = b.id
      WHERE b.tenant_id = ${tenantId}
    `;

    res.json(stats[0] || { total: 0, pending: 0, approved: 0, rejected: 0, published: 0 });
  } catch (error) {
    console.error('Error fetching stats:', error);
    res.status(500).json({ error: 'Failed to fetch stats' });
  }
});

/**
 * GET /posts/:id - Get single post
 */
router.get('/:id', async (req, res) => {
  try {
    const { id } = req.params;
    const tenantId = getTenantId();

    const posts = await query`
      SELECT p.*, a.status as approval_status, a.image_status,
             a.rejection_reason, a.image_rejection_reason,
             a.scheduled_for, a.oneup_post_id,
             b.name as brand_name, b.slug as brand_slug
      FROM posts p
      LEFT JOIN approvals a ON p.id = a.post_id
      LEFT JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${id}
        AND b.tenant_id = ${tenantId}
      LIMIT 1
    `;

    if (posts.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    res.json(posts[0]);
  } catch (error) {
    console.error('Error fetching post:', error);
    res.status(500).json({ error: 'Failed to fetch post' });
  }
});

/**
 * POST /posts - Create new post
 */
router.post('/', async (req, res) => {
  try {
    const { brand_id, title, content, platform, image_url } = req.body;

    if (!brand_id || !content) {
      return res.status(400).json({ error: 'brand_id and content required' });
    }

    // Create post
    const posts = await query`
      INSERT INTO posts (brand_id, title, content, platform, image_url, created_at)
      VALUES (${brand_id}, ${title || ''}, ${content}, ${platform || 'instagram'}, ${image_url || null}, NOW())
      RETURNING *
    `;

    const post = posts[0];

    // Create approval record
    await query`
      INSERT INTO approvals (post_id, status, image_status)
      VALUES (${post.id}, 'pending', 'not_ready')
    `;

    res.status(201).json(post);
  } catch (error) {
    console.error('Error creating post:', error);
    res.status(500).json({ error: 'Failed to create post' });
  }
});

/**
 * PATCH /posts/:id - Update post
 */
router.patch('/:id', async (req, res) => {
  try {
    const { id } = req.params;
    const { title, content, image_url } = req.body;
    const tenantId = getTenantId();

    // Verify ownership
    const existing = await query`
      SELECT p.id FROM posts p
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${id} AND b.tenant_id = ${tenantId}
    `;

    if (existing.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    // Update post
    const posts = await query`
      UPDATE posts
      SET
        title = COALESCE(${title}, title),
        content = COALESCE(${content}, content),
        image_url = COALESCE(${image_url}, image_url)
      WHERE id = ${id}
      RETURNING *
    `;

    res.json(posts[0]);
  } catch (error) {
    console.error('Error updating post:', error);
    res.status(500).json({ error: 'Failed to update post' });
  }
});

/**
 * DELETE /posts/:id - Delete post
 */
router.delete('/:id', async (req, res) => {
  try {
    const { id } = req.params;
    const tenantId = getTenantId();

    // Verify ownership
    const existing = await query`
      SELECT p.id FROM posts p
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${id} AND b.tenant_id = ${tenantId}
    `;

    if (existing.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    // Delete approval first
    await query`DELETE FROM approvals WHERE post_id = ${id}`;

    // Delete post
    await query`DELETE FROM posts WHERE id = ${id}`;

    res.json({ status: 'ok', message: 'Post deleted' });
  } catch (error) {
    console.error('Error deleting post:', error);
    res.status(500).json({ error: 'Failed to delete post' });
  }
});

export default router;
