/**
 * Images API routes - AI image generation
 */

import express from 'express';
import { query, getTenantId } from '../lib/db.js';
import { generateImagePrompt } from '../lib/ai.js';

const router = express.Router();

const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_REPO = process.env.GITHUB_REPO || 'username/social-images';
const GITHUB_BRANCH = process.env.GITHUB_BRANCH || 'main';

/**
 * POST /images/generate - Generate AI image for a post
 */
router.post('/generate', async (req, res) => {
  try {
    const { postId } = req.body;
    const tenantId = getTenantId();

    if (!postId) {
      return res.status(400).json({ error: 'postId required' });
    }

    // Get post with brand info
    const posts = await query`
      SELECT p.*, b.name as brand_name, b.color as brand_color
      FROM posts p
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${postId} AND b.tenant_id = ${tenantId}
    `;

    if (posts.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    const post = posts[0];

    // Generate image prompt
    const brandContext = {
      name: post.brand_name,
      colors: post.brand_color
    };

    const imagePrompt = await generateImagePrompt(post.content, brandContext);

    // For now, return the prompt - actual image generation would use Gemini's image API
    // In production, this would:
    // 1. Call Gemini's image generation API
    // 2. Save the image
    // 3. Commit to GitHub
    // 4. Return the deployed URL

    // Update post with generated image info
    const timestamp = Date.now();
    const filename = `post-${postId}-${timestamp}.png`;
    const imageUrl = `https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}/images/${filename}`;

    await query`
      UPDATE posts
      SET
        image_filename = ${filename},
        image_original_filename = ${filename}
      WHERE id = ${postId}
    `;

    // Update approval image status
    await query`
      UPDATE approvals
      SET image_status = 'pending'
      WHERE post_id = ${postId}
    `;

    res.json({
      status: 'generating',
      postId,
      imagePrompt,
      estimatedTime: 30,
      // In production, this would be the actual URL after generation
      imageUrl: imageUrl
    });
  } catch (error) {
    console.error('Error generating image:', error);
    res.status(500).json({ error: 'Failed to generate image: ' + error.message });
  }
});

/**
 * GET /images/verify/:postId - Check if image generation is complete
 */
router.get('/verify/:postId', async (req, res) => {
  try {
    const { postId } = req.params;
    const tenantId = getTenantId();

    const posts = await query`
      SELECT p.image_filename, p.image_url, p.image_generated_at
      FROM posts p
      JOIN brands b ON p.brand_id = b.id
      WHERE p.id = ${postId} AND b.tenant_id = ${tenantId}
    `;

    if (posts.length === 0) {
      return res.status(404).json({ error: 'Post not found' });
    }

    const post = posts[0];

    res.json({
      status: post.image_generated_at ? 'complete' : 'generating',
      imageFilename: post.image_filename,
      imageUrl: post.image_url
    });
  } catch (error) {
    console.error('Error verifying image:', error);
    res.status(500).json({ error: 'Failed to verify image' });
  }
});

/**
 * POST /images/update - Update image for a post
 */
router.post('/update', async (req, res) => {
  try {
    const { postId, imageUrl } = req.body;
    const tenantId = getTenantId();

    if (!postId || !imageUrl) {
      return res.status(400).json({ error: 'postId and imageUrl required' });
    }

    // Update post
    await query`
      UPDATE posts
      SET
        image_url = ${imageUrl},
        image_generated_at = NOW()
      WHERE id = ${postId}
    `;

    res.json({ status: 'ok', imageUrl });
  } catch (error) {
    console.error('Error updating image:', error);
    res.status(500).json({ error: 'Failed to update image' });
  }
});

export default router;
