/* ==========================================================================
   nav.js — THE SINGLE SOURCE OF TRUTH FOR THE MENU.
   The rail, the mobile drawer, the bottom tab bar, the router's valid-route
   table and every breadcrumb all read from this one array. Adding a page here
   makes it appear everywhere, correctly, with no other edit.

   This exists because the Stitch reference had SIX different sidebars where
   10 labels resolved to 7 destinations. That cannot happen by construction here.

   Shape:
     id      route segment  (#/id or #/id/pageId)
     label   what the client sees — plain language, never jargon
     icon    Material Symbols ligature
     badge   optional key into the badge feed (api.badges()) for a count
     pages   [{id,label,view}] — first entry is the section's Overview
     tab     true => also appears in the mobile bottom bar (max 5)
   ========================================================================== */

export const NAV = [
  {
    id: 'home', label: 'Home', icon: 'space_dashboard', tab: true,
    pages: [{ id: '', label: 'Home', view: 'home' }]
  },
  {
    id: 'plan', label: 'Your Plan', icon: 'route', badge: 'yourTurn', tab: true,
    pages: [
      { id: '',         label: 'Overview',    view: 'plan.overview' },
      { id: 'your-turn',label: 'Your Turn',   view: 'plan.yourTurn' },
      { id: 'modules',  label: 'The 12 steps',view: 'plan.modules' },
      { id: 'wins',     label: 'Wins',        view: 'plan.wins' }
    ]
  },
  {
    id: 'found', label: 'Get Found', icon: 'travel_explore',
    pages: [
      { id: '',          label: 'Overview',            view: 'found.overview' },
      { id: 'rankings',  label: 'Rankings',            view: 'found.rankings' },
      { id: 'local',     label: 'Google Business',     view: 'found.local' },
      { id: 'ai',        label: 'AI visibility',       view: 'found.ai' },
      { id: 'backlinks', label: 'Backlinks',           view: 'found.backlinks' },
      { id: 'reports',   label: 'Reports',             view: 'found.reports' }
    ]
  },
  {
    id: 'customers', label: 'Customers', icon: 'groups', badge: 'newLeads', tab: true,
    pages: [
      { id: '',         label: 'Overview',        view: 'customers.overview' },
      { id: 'leads',    label: 'Leads',           view: 'customers.leads' },
      { id: 'contacts', label: 'Contacts',        view: 'customers.contacts' },
      { id: 'pipeline', label: 'Pipeline',        view: 'customers.pipeline' },
      { id: 'jobs',     label: 'Jobs & quotes',   view: 'customers.jobs' }
    ]
  },
  {
    id: 'content', label: 'Content', icon: 'auto_awesome', badge: 'approvals', tab: true,
    pages: [
      { id: '',          label: 'Overview',   view: 'content.overview' },
      { id: 'calendar',  label: 'Calendar',   view: 'content.calendar' },
      { id: 'approvals', label: 'Approvals',  view: 'content.approvals' },
      { id: 'social',    label: 'Social',     view: 'content.social' },
      { id: 'articles',  label: 'Articles',   view: 'content.articles' },
      { id: 'media',     label: 'Media',      view: 'content.media' }
    ]
  },
  {
    id: 'ads', label: 'Advertising', icon: 'campaign',
    pages: [
      { id: '',           label: 'Overview',   view: 'ads.overview' },
      { id: 'google',     label: 'Google',     view: 'ads.google' },
      { id: 'facebook',   label: 'Facebook',   view: 'ads.facebook' },
      { id: 'instagram',  label: 'Instagram',  view: 'ads.instagram' }
    ]
  },
  {
    id: 'finances', label: 'Finances', icon: 'account_balance',
    pages: [
      { id: '',          label: 'Overview',          view: 'finances.overview' },
      { id: 'pnl',       label: 'Revenue & P&L',     view: 'finances.pnl' },
      { id: 'invoices',  label: 'Invoices',          view: 'finances.invoices' },
      { id: 'expenses',  label: 'Expenses',          view: 'finances.expenses' },
      { id: 'vendors',   label: 'Vendors',           view: 'finances.vendors' },
      { id: 'tax',       label: 'Tax & compliance',  view: 'finances.tax' }
    ]
  },
  {
    id: 'payments', label: 'Payments', icon: 'credit_card',
    pages: [
      { id: '',         label: 'Getting paid',  view: 'payments.overview' },
      { id: 'invoices', label: 'Invoices',      view: 'payments.invoices' },
      { id: 'links',    label: 'Payment links', view: 'payments.links' },
      { id: 'payouts',  label: 'Payouts',       view: 'payments.payouts' }
    ]
  },
  {
    id: 'inventory', label: 'Inventory', icon: 'inventory_2',
    pages: [
      { id: '',         label: 'Overview',      view: 'inventory.overview' },
      { id: 'products', label: 'Products',      view: 'inventory.products' },
      { id: 'stock',    label: 'Stock alerts',  view: 'inventory.stock' }
    ]
  },
  {
    id: 'schedule', label: 'Calendar', icon: 'calendar_month',
    pages: [
      { id: '',             label: 'Schedule',      view: 'schedule.overview' },
      { id: 'appointments', label: 'Appointments',  view: 'schedule.appointments' }
    ]
  },
  {
    id: 'team', label: 'Team', icon: 'diversity_3',
    pages: [
      { id: '',      label: 'Your team',    view: 'team.overview' },
      { id: 'chat',  label: 'Chat',         view: 'team.chat' },
      { id: 'files', label: 'Shared files', view: 'team.files' }
    ]
  },
  {
    id: 'website', label: 'Website', icon: 'language',
    pages: [
      { id: '',        label: 'Overview',      view: 'website.overview' },
      { id: 'pages',   label: 'Pages',         view: 'website.pages' },
      { id: 'blog',    label: 'Blog engine',   view: 'website.blog' },
      { id: 'map',     label: 'Topic map',     view: 'website.map' },
      { id: 'forms',   label: 'Forms',         view: 'website.forms' }
    ]
  },
  {
    id: 'brand', label: 'Brand', icon: 'palette',
    pages: [
      { id: '',       label: 'Overview',        view: 'brand.overview' },
      { id: 'logos',  label: 'Logos & assets',  view: 'brand.logos' },
      { id: 'colors', label: 'Colours & type',  view: 'brand.colors' },
      { id: 'voice',  label: 'Voice',           view: 'brand.voice' },
      { id: 'photos', label: 'Photography',     view: 'brand.photos' }
    ]
  },
  {
    id: 'ask', label: 'Ask', icon: 'forum',
    pages: [
      { id: '',         label: 'Ask your agent', view: 'ask.chat' },
      { id: 'requests', label: 'Requests',       view: 'ask.requests' },
      { id: 'history',  label: 'History',        view: 'ask.history' }
    ]
  },
  {
    id: 'support', label: 'Support', icon: 'support_agent',
    pages: [
      { id: '',         label: 'Overview', view: 'support.overview' },
      { id: 'tickets',  label: 'Tickets',  view: 'support.tickets' }
    ]
  },
  {
    id: 'account', label: 'Account', icon: 'settings',
    pages: [
      { id: '',          label: 'Business profile', view: 'account.business' },
      { id: 'platforms', label: 'Connected apps',   view: 'account.platforms' },
      { id: 'autonomy',  label: 'How much we do',   view: 'account.autonomy' },
      { id: 'team',      label: 'Staff logins',     view: 'account.team' },
      { id: 'schedule',  label: 'Staff scheduling', view: 'account.schedule' },
      { id: 'locations', label: 'Locations',        view: 'account.locations' },
      { id: 'billing',   label: 'Billing',          view: 'account.billing' },
      { id: 'help',      label: 'Help',             view: 'account.help' }
    ]
  }
];

/** Every valid route, flattened. The router validates against this. */
export function routes() {
  const out = [];
  for (const s of NAV) {
    for (const p of s.pages) {
      out.push({
        path: p.id ? `${s.id}/${p.id}` : s.id,
        section: s, page: p, view: p.view
      });
    }
  }
  return out;
}

export function resolve(path) {
  const clean = String(path || '').replace(/^#?\/?/, '').replace(/\/$/, '');
  const all = routes();
  return all.find(r => r.path === clean)
      || all.find(r => r.path === clean.split('/')[0])
      || all[0];
}

export const TABS = NAV.filter(s => s.tab).slice(0, 4);
