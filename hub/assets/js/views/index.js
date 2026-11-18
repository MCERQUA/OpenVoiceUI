/* View registry. nav.js names a view; this maps the name to a renderer.
   A renderer is async ({api, section, page}) => HTML string. */

import home from './home.js';
import * as plan from './plan.js';
import * as found from './found.js';
import * as customers from './customers.js';
import * as content from './content.js';
import * as website from './website.js';
import * as brand from './brand.js';
import * as ask from './ask.js';
import * as account from './account.js';
import * as ads from './ads.js';
import * as finances from './finances.js';
import * as inventory from './inventory.js';
import * as schedule from './schedule.js';
import * as support from './support.js';
import * as team from './team.js';
import * as payments from './payments.js';

export const VIEWS = {
  'home': home,

  'plan.overview': plan.overview,
  'plan.yourTurn': plan.yourTurn,
  'plan.modules':  plan.modules,
  'plan.wins':     plan.wins,

  'found.overview':  found.overview,
  'found.rankings':  found.rankings,
  'found.local':     found.local,
  'found.ai':        found.ai,
  'found.backlinks': found.backlinks,
  'found.reports':   found.reports,

  'customers.overview': customers.overview,
  'customers.leads':    customers.leads,
  'customers.contacts': customers.contacts,
  'customers.pipeline': customers.pipeline,
  'customers.jobs':     customers.jobs,

  'content.overview':  content.overview,
  'content.calendar':  content.calendar,
  'content.approvals': content.approvals,
  'content.social':    content.social,
  'content.articles':  content.articles,
  'content.media':     content.media,

  'website.overview': website.overview,
  'website.pages':    website.pages,
  'website.blog':     website.blog,
  'website.map':      website.map,
  'website.forms':    website.forms,

  'brand.overview': brand.overview,
  'brand.logos':    brand.logos,
  'brand.colors':   brand.colors,
  'brand.voice':    brand.voice,
  'brand.photos':   brand.photos,

  'ask.chat':     ask.chat,
  'ask.requests': ask.requests,
  'ask.history':  ask.history,

  'account.business':  account.business,
  'account.platforms': account.platforms,
  'account.autonomy':  account.autonomy,
  'account.team':      account.team,
  'account.schedule':  account.schedule,
  'account.locations': account.locations,
  'account.billing':   account.billing,
  'account.help':      account.help,

  'ads.overview':  ads.overview,
  'ads.google':    ads.google,
  'ads.facebook':  ads.facebook,
  'ads.instagram': ads.instagram,

  'finances.overview': finances.overview,
  'finances.pnl':      finances.pnl,
  'finances.invoices': finances.invoices,
  'finances.expenses': finances.expenses,
  'finances.vendors':  finances.vendors,
  'finances.tax':      finances.tax,

  'inventory.overview': inventory.overview,
  'inventory.products': inventory.products,
  'inventory.stock':    inventory.stock,

  'schedule.overview':     schedule.overview,
  'schedule.appointments': schedule.appointments,

  'support.overview': support.overview,
  'support.tickets':  support.tickets,

  'team.overview': team.overview,
  'team.chat':     team.chat,
  'team.files':    team.files,

  'payments.overview': payments.overview,
  'payments.invoices': payments.invoices,
  'payments.links':    payments.links,
  'payments.payouts':  payments.payouts
};
