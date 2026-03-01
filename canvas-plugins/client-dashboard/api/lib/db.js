/**
 * Database helper using Neon serverless
 * Falls back to mock mode if DATABASE_URL not set
 */

import { neon } from '@neondatabase/serverless';

const TENANT_ID = process.env.TENANT_ID || 'default';
const HAS_DB = !!process.env.DATABASE_URL;

// Only create neon connection if DATABASE_URL is set
const sql = HAS_DB ? neon(process.env.DATABASE_URL) : null;

/**
 * Check if database is available
 */
export function hasDatabase() {
  return HAS_DB;
}

/**
 * Get tenant-scoped database instance
 */
export function getDb() {
  return sql;
}

/**
 * Get current tenant ID
 */
export function getTenantId() {
  return TENANT_ID;
}

/**
 * Execute a query with tenant scoping
 */
export async function query(strings, ...values) {
  if (!HAS_DB) {
    console.warn('Database not configured - returning empty result');
    return [];
  }
  return sql(strings, ...values);
}

/**
 * Get tenant's brands
 */
export async function getTenantBrands() {
  if (!HAS_DB) {
    return [{ id: 1, slug: 'demo', name: 'Demo Brand', short_name: 'Demo', color: '#4a9eff', website_url: 'https://example.com' }];
  }
  return sql`
    SELECT id, slug, name, short_name, color, website_url
    FROM brands
    WHERE tenant_id = ${TENANT_ID}
    ORDER BY name
  `;
}

/**
 * Get tenant by subdomain or ID
 */
export async function getTenant(subdomainOrId) {
  if (!HAS_DB) {
    return { id: 'demo', subdomain: 'demo', name: 'Demo Tenant', email: 'demo@example.com' };
  }
  const tenants = await sql`
    SELECT id, subdomain, name, email, logo_url, primary_color
    FROM tenants
    WHERE subdomain = ${subdomainOrId} OR id = ${subdomainOrId}
    LIMIT 1
  `;
  return tenants[0] || null;
}

export default { hasDatabase, getDb, getTenantId, query, getTenantBrands, getTenant };
