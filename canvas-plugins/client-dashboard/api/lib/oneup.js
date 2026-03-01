/**
 * OneUp social media platform integration
 */

const ONEUP_API_KEY = process.env.ONEUP_API_KEY;
const ONEUP_BASE_URL = 'https://api.oneupapp.io';

/**
 * Make authenticated request to OneUp API
 */
async function oneupRequest(endpoint, options = {}) {
  const response = await fetch(`${ONEUP_BASE_URL}${endpoint}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${ONEUP_API_KEY}`,
      'Content-Type': 'application/json',
      ...options.headers
    }
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`OneUp API error: ${response.status} - ${error}`);
  }

  return response.json();
}

/**
 * Get connected social accounts
 */
export async function getAccounts() {
  return oneupRequest('/accounts');
}

/**
 * Get categories for account
 */
export async function getCategories(accountId) {
  return oneupRequest(`/accounts/${accountId}/categories`);
}

/**
 * Create a post
 */
export async function createPost(data) {
  const { text, mediaUrls, scheduledAt, accountId, categoryId } = data;

  const payload = {
    text,
    account_id: accountId,
    category_id: categoryId,
    media: mediaUrls || [],
  };

  if (scheduledAt) {
    payload.scheduled_at = scheduledAt;
  }

  return oneupRequest('/posts', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

/**
 * Get scheduled posts
 */
export async function getScheduledPosts(accountId, options = {}) {
  const params = new URLSearchParams({
    status: 'scheduled',
    ...(accountId && { account_id: accountId }),
    ...options
  });

  return oneupRequest(`/posts?${params}`);
}

/**
 * Delete a scheduled post
 */
export async function deletePost(postId) {
  return oneupRequest(`/posts/${postId}`, {
    method: 'DELETE'
  });
}

/**
 * Publish immediately
 */
export async function publishNow(postId) {
  return oneupRequest(`/posts/${postId}/publish`, {
    method: 'POST'
  });
}

export default { getAccounts, getCategories, createPost, getScheduledPosts, deletePost, publishNow };
