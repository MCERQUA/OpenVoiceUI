/**
 * Database helper using Neon serverless
 */

import { neon } from '@neondatabase/serverless';

const sql = neon(process.env.DATABASE_URL);
const TENANT_ID = process.env.TENANT_ID || 'default';

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
  return sql(strings, ...values);
}

/**
 * Get tenant's brands
 */
export async function getTenantBrands() {
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
  const tenants = await sql`
    SELECT id, subdomain, name, email, logo_url, primary_color
    FROM tenants
    WHERE subdomain = ${subdomainOrId} OR id = ${subdomainOrId}
    LIMIT 1
  `;
  return tenants[0] || null;
}

export default { getDb, getTenantId, query, getTenantBrands, getTenant };
