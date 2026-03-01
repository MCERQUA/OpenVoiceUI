/**
 * Approvals API routes
 */

import express from 'express';
import { query, getTenantId } from '../lib/db.js';

const router = express.Router();

/**
 * POST /approvals - Approve or reject a post
 */
router.post('/', async (req, res) => {
  try {
    const { postId, status, rejection_reason } = req.body;
    const tenantId = getTenantId();

    if (!postId || !status) {
      return res.status(400).json({ error: 'postId and status required' });
    }

    if (!['approved', 'rejected'].includes(status)) {
      return res.status(400).json({ error: 'status must be approved or rejected' });
    }

    // Verify ownership
    const existing = await query`
      SELECT p.id, a.id as approval_id
      FROM posts p
      JOIN approvals a ON p.id = a.post_id
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${postId} AND b.tenant_id = ${tenantId}
    `;

    if (existing.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    // Update approval
    const approvals = await query`
      UPDATE approvals
      SET
        status = ${status},
        rejection_reason = ${status === 'rejected' ? rejection_reason : null},
        reviewed_at = NOW()
      WHERE post_id = ${postId}
      RETURNING *
    `;

    res.json(approvals[0]);
  } catch (error) {
    console.error('Error updating approval:', error);
    res.status(500).json({ error: 'Failed to update approval' });
  }
});

/**
 * POST /approvals/image - Approve or reject an image
 */
router.post('/image', async (req, res) => {
  try {
    const { postId, status, rejection_reason } = req.body;
    const tenantId = getTenantId();

    if (!postId || !status) {
      return res.status(400).json({ error: 'postId and status required' });
    }

    if (!['approved', 'rejected'].includes(status)) {
      return res.status(400).json({ error: 'status must be approved or rejected' });
    }

    // Verify ownership
    const existing = await query`
      SELECT p.id, a.id as approval_id
      FROM posts p
      JOIN approvals a ON p.id = a.post_id
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${postId} AND b.tenant_id = ${tenantId}
    `;

    if (existing.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    // Update image approval
    const approvals = await query`
      UPDATE approvals
      SET
        image_status = ${status},
        image_rejection_reason = ${status === 'rejected' ? rejection_reason : null},
        image_reviewed_at = NOW()
      WHERE post_id = ${postId}
      RETURNING *
    `;

    res.json(approvals[0]);
  } catch (error) {
    console.error('Error updating image approval:', error);
    res.status(500).json({ error: 'Failed to update image approval' });
  }
});

/**
 * GET /approvals/pending - Get all pending approvals
 */
router.get('/pending', async (req, res) => {
  try {
    const tenantId = getTenantId();
    const { limit = 20 } = req.query;

    const posts = await query`
      SELECT p.*, a.status as approval_status, a.image_status,
             b.name as brand_name, b.slug as brand_slug
      FROM posts p
      JOIN approvals a ON p.id = a.post_id
      JOIN brands b ON p.brand_id = b.id
      WHERE b.tenant_id = ${tenantId}
        AND (a.status = 'pending' OR a.image_status = 'pending')
      ORDER BY p.created_at ASC
      LIMIT ${limit}
    `;

    res.json(posts);
  } catch (error) {
    console.error('Error fetching pending approvals:', error);
    res.status(500).json({ error: 'Failed to fetch pending approvals' });
  }
});

export default router;
