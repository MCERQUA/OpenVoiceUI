/**
 * Schedule API routes
 */

import express from 'express';
import { query, getTenantId } from '../lib/db.js';
import * as oneup from '../lib/oneup.js';

const router = express.Router();

/**
 * GET /schedule - Get scheduled posts
 */
router.get('/', async (req, res) => {
  try {
    const tenantId = getTenantId();
    const { brand_id, from, to } = req.query;

    let posts;

    if (brand_id) {
      posts = await query`
        SELECT p.*, a.scheduled_for, a.scheduled_status,
               b.name as brand_name, b.slug as brand_slug
        FROM posts p
        JOIN approvals a ON p.id = a.post_id
        JOIN brands b ON p.brand_id = b.id
        WHERE b.tenant_id = ${tenantId}
          AND p.brand_id = ${brand_id}
          AND a.scheduled_for IS NOT NULL
        ORDER BY a.scheduled_for ASC
      `;
    } else {
      posts = await query`
        SELECT p.*, a.scheduled_for, a.scheduled_status,
               b.name as brand_name, b.slug as brand_slug
        FROM posts p
        JOIN approvals a ON p.id = a.post_id
        JOIN brands b ON p.brand_id = b.id
        WHERE b.tenant_id = ${tenantId}
          AND a.scheduled_for IS NOT NULL
        ORDER BY a.scheduled_for ASC
      `;
    }

    res.json(posts);
  } catch (error) {
    console.error('Error fetching schedule:', error);
    res.status(500).json({ error: 'Failed to fetch schedule' });
  }
});

/**
 * GET /schedule/stats - Get schedule statistics
 */
router.get('/stats', async (req, res) => {
  try {
    const tenantId = getTenantId();

    const stats = await query`
      SELECT
        COUNT(*) FILTER (WHERE a.scheduled_for IS NOT NULL AND a.scheduled_for > NOW()) as scheduled,
        COUNT(*) FILTER (WHERE a.scheduled_status = 'published') as published,
        COUNT(*) FILTER (WHERE a.scheduled_status = 'failed') as failed
      FROM posts p
      JOIN approvals a ON p.id = a.post_id
      JOIN brands b ON p.brand_id = b.id
      WHERE b.tenant_id = ${tenantId}
    `;

    res.json(stats[0] || { scheduled: 0, published: 0, failed: 0 });
  } catch (error) {
    console.error('Error fetching schedule stats:', error);
    res.status(500).json({ error: 'Failed to fetch schedule stats' });
  }
});

/**
 * POST /schedule - Schedule a post
 */
router.post('/', async (req, res) => {
  try {
    const { postId, scheduledFor, platforms } = req.body;
    const tenantId = getTenantId();

    if (!postId || !scheduledFor) {
      return res.status(400).json({ error: 'postId and scheduledFor required' });
    }

    // Verify ownership
    const existing = await query`
      SELECT p.id, p.brand_id, a.status
      FROM posts p
      JOIN approvals a ON p.id = a.post_id
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${postId} AND b.tenant_id = ${tenantId}
    `;

    if (existing.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    if (existing[0].status !== 'approved') {
      return res.status(400).json({ error: 'Post must be approved before scheduling' });
    }

    // Update schedule
    const approvals = await query`
      UPDATE approvals
      SET
        scheduled_for = ${scheduledFor},
        scheduled_status = 'scheduled',
        target_platforms = ${platforms || ['instagram']}
      WHERE post_id = ${postId}
      RETURNING *
    `;

    res.json(approvals[0]);
  } catch (error) {
    console.error('Error scheduling post:', error);
    res.status(500).json({ error: 'Failed to schedule post' });
  }
});

/**
 * DELETE /schedule/:postId - Unschedule a post
 */
router.delete('/:postId', async (req, res) => {
  try {
    const { postId } = req.params;
    const tenantId = getTenantId();

    // Verify ownership
    const existing = await query`
      SELECT p.id FROM posts p
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${postId} AND b.tenant_id = ${tenantId}
    `;

    if (existing.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    // Clear schedule
    await query`
      UPDATE approvals
      SET
        scheduled_for = NULL,
        scheduled_status = NULL
      WHERE post_id = ${postId}
    `;

    res.json({ status: 'ok', message: 'Post unscheduled' });
  } catch (error) {
    console.error('Error unscheduling post:', error);
    res.status(500).json({ error: 'Failed to unschedule post' });
  }
});

/**
 * POST /schedule/publish - Publish a scheduled post now
 */
router.post('/publish', async (req, res) => {
  try {
    const { postId } = req.body;
    const tenantId = getTenantId();

    // Get post with brand info
    const posts = await query`
      SELECT p.*, b.oneup_category_id
      FROM posts p
      JOIN approvals a ON p.id = a.post_id
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${postId}
        AND b.tenant_id = ${tenantId}
        AND a.status = 'approved'
    `;

    if (posts.length === 0) {
      return res.status(404).json({ error: 'Post not found or not approved' });
    }

    const post = posts[0];

    // Publish to OneUp
    const result = await oneup.createPost({
      text: post.content,
      mediaUrls: post.image_url ? [post.image_url] : [],
      accountId: post.oneup_category_id,
    });

    // Update status
    await query`
      UPDATE approvals
      SET
        scheduled_status = 'published',
        oneup_post_id = ${result.id || null},
        published_at = NOW()
      WHERE post_id = ${postId}
    `;

    res.json({ status: 'ok', oneup_id: result.id });
  } catch (error) {
    console.error('Error publishing post:', error);
    res.status(500).json({ error: 'Failed to publish post: ' + error.message });
  }
});

export default router;
