/* INVENTORY — products, stock levels. Zero backing system today; general
   retail/e-commerce/product businesses expect this even though JamBot's
   current client base skews service-business (which has no products at
   all — see api.unknownMetric tiles below staying honest either way). */

import { pageHead, tiles, block, notConnected } from '../ui.js';

export async function overview({ api }) {
  return `
  ${pageHead({ eyebrow: 'Inventory', title: 'What you sell and have on hand',
    sub: 'Products, stock levels and what needs reordering.' })}
  ${tiles([
    api.unknownMetric('Products tracked', null, { icon: 'inventory_2' }),
    api.unknownMetric('Low stock', null, { icon: 'warning' }),
    api.unknownMetric('Inventory value', null, { icon: 'payments' })
  ])}
  ${block({ title: 'Needs reordering', icon: 'shopping_cart', body:
    notConnected('Stock levels', 'Connect a point-of-sale or inventory system and low-stock items will surface here automatically.') })}`;
}

export async function products() {
  return `
  ${pageHead({ eyebrow: 'Inventory', title: 'Products',
    sub: 'Your full catalog — pricing, SKUs, and what’s active.',
    actions: [{ label: 'Add a product', kind: 'primary', icon: 'add' }] })}
  ${block({ body: notConnected('Product catalog',
    'Once connected to your point-of-sale, e-commerce platform, or a manual catalog, every product you sell will be listed here.') })}`;
}

export async function stock() {
  return `
  ${pageHead({ eyebrow: 'Inventory', title: 'Stock alerts',
    sub: 'Anything running low or out, before your customers notice.' })}
  ${block({ body: notConnected('Stock alerts',
    'Once inventory tracking is connected, anything below its reorder point will show up here.') })}`;
}
