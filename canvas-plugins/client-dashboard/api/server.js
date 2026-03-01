/**
 * Client Dashboard API Server
 *
 * Express API for the Client Dashboard canvas plugin.
 * Runs per-client on a unique port (16300+).
 */

import express from 'express';
import cors from 'cors';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const PORT = process.env.PORT || 16300;
const TENANT_ID = process.env.TENANT_ID || 'default';

// Middleware
app.use(cors({
  origin: (origin, callback) => {
    // Allow localhost and same-origin requests
    if (!origin || origin.includes('localhost') || origin.includes('127.0.0.1')) {
      callback(null, true);
    } else {
      callback(null, true); // Allow all for now (canvas proxy handles auth)
    }
  },
  credentials: true
}));
app.use(express.json({ limit: '10mb' }));

// Request logging
app.use((req, res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`);
  next();
});

// Health check
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    tenant: TENANT_ID,
    port: PORT,
    timestamp: new Date().toISOString()
  });
});

// Import routes
import postsRouter from './routes/posts.js';
import approvalsRouter from './routes/approvals.js';
import scheduleRouter from './routes/schedule.js';
import imagesRouter from './routes/images.js';
import contentRouter from './routes/content.js';
import brandsRouter from './routes/brands.js';

// Mount routes
app.use('/posts', postsRouter);
app.use('/approvals', approvalsRouter);
app.use('/schedule', scheduleRouter);
app.use('/images', imagesRouter);
app.use('/content', contentRouter);
app.use('/brands', brandsRouter);

// Error handler
app.use((err, req, res, next) => {
  console.error('Error:', err);
  res.status(500).json({
    error: 'Internal server error',
    message: process.env.NODE_ENV === 'development' ? err.message : undefined
  });
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({ error: 'Not found' });
});

// Start server
app.listen(PORT, () => {
  console.log(`Dashboard API running on port ${PORT}`);
  console.log(`Tenant: ${TENANT_ID}`);
  console.log(`Health: http://localhost:${PORT}/health`);
});
