/**
 * Client Dashboard - Shared JavaScript
 */

// API base URL (proxied through OpenVoiceUI)
const API_BASE = '/dashboard-api';

/**
 * Fetch wrapper with error handling
 */
async function api(path, options = {}) {
  const url = `${API_BASE}${path}`;

  try {
    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      },
      ...options
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: 'Request failed' }));
      throw new Error(error.error || `HTTP ${response.status}`);
    }

    return response.json();
  } catch (error) {
    console.error('API Error:', error);
    throw error;
  }
}

/**
 * Navigate to another canvas page
 */
function openPage(name) {
  // Post message to parent (canvas display)
  if (window.parent !== window) {
    window.parent.postMessage({
      type: 'canvas-navigate',
      page: name
    }, '*');
  } else {
    // Direct navigation if not in iframe
    window.location.href = `/pages/${name}.html`;
  }
}

/**
 * Format date for display
 */
function formatDate(dateStr) {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit'
  });
}

/**
 * Format relative time
 */
function timeAgo(dateStr) {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const now = new Date();
  const seconds = Math.floor((now - date) / 1000);

  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return formatDate(dateStr);
}

/**
 * Show toast notification
 */
function showToast(message, type = 'info') {
  // Create toast element
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  toast.style.cssText = `
    position: fixed;
    bottom: 20px;
    right: 20px;
    padding: 12px 20px;
    border-radius: 8px;
    background: ${type === 'success' ? '#22c55e' : type === 'error' ? '#ef4444' : '#3b82f6'};
    color: white;
    font-size: 0.875rem;
    z-index: 1000;
    animation: slideIn 0.3s ease;
  `;

  document.body.appendChild(toast);

  // Remove after 3 seconds
  setTimeout(() => {
    toast.style.animation = 'slideOut 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

/**
 * Show/hide loading state
 */
function showLoading(container) {
  container.innerHTML = `
    <div class="loading">
      <div class="spinner"></div>
      Loading...
    </div>
  `;
}

/**
 * Show empty state
 */
function showEmpty(container, message, actionText, actionCallback) {
  let html = `<div class="empty-state"><p>${message}</p>`;
  if (actionText && actionCallback) {
    html += `<button class="btn-secondary" onclick="${actionCallback}">${actionText}</button>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

/**
 * Get status badge HTML
 */
function getStatusBadge(status) {
  const statusClass = `status-${status || 'pending'}`;
  return `<span class="status-badge ${statusClass}">${status || 'pending'}</span>`;
}

/**
 * Debounce function
 */
function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

// Add animation styles
const style = document.createElement('style');
style.textContent = `
  @keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }
  @keyframes slideOut {
    from { transform: translateX(0); opacity: 1; }
    to { transform: translateX(100%); opacity: 0; }
  }
`;
document.head.appendChild(style);

// Export for use in pages
window.dashboard = {
  api,
  openPage,
  formatDate,
  timeAgo,
  showToast,
  showLoading,
  showEmpty,
  getStatusBadge,
  debounce
};
