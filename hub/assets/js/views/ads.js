/* ADVERTISING — paid ads on Google, Facebook and Instagram. Distinct from
   Get Found, which is organic/SEO visibility, not paid spend. No ad-platform
   API connection exists yet for any of the three. */

import { pageHead, tiles, block, notConnected } from '../ui.js';

export async function overview({ api }) {
  return `
  ${pageHead({ eyebrow: 'Advertising', title: 'Paid ads',
    sub: 'What you’re spending and what it’s bringing in, across Google, Facebook and Instagram.' })}
  ${tiles([
    api.unknownMetric('Ad spend this month', null, { icon: 'payments' }),
    api.unknownMetric('Leads from ads', null, { icon: 'ads_click' }),
    api.unknownMetric('Cost per lead', null, { icon: 'request_quote' }),
    api.unknownMetric('Active campaigns', null, { icon: 'campaign' })
  ])}
  ${block({ title: 'By platform', icon: 'campaign', body:
    notConnected('Ad platforms', 'Connect Google Ads and/or Meta Ads (Facebook & Instagram run through the same account) and a spend/results breakdown for each will appear here.') })}`;
}

export async function google() {
  return `
  ${pageHead({ eyebrow: 'Advertising', title: 'Google',
    sub: 'Google Ads and Google Business Profile promotions.',
    actions: [{ label: 'Connect Google Ads', kind: 'primary', icon: 'link' }] })}
  ${block({ body: notConnected('Google Ads',
    'Once your Google Ads account is connected, campaigns, spend and results will appear here. This is separate from Get Found, which covers your free/organic Google Business Profile presence.') })}`;
}

export async function facebook() {
  return `
  ${pageHead({ eyebrow: 'Advertising', title: 'Facebook',
    sub: 'Facebook ad campaigns, spend and results.',
    actions: [{ label: 'Connect Meta Ads', kind: 'primary', icon: 'link' }] })}
  ${block({ body: notConnected('Facebook Ads',
    'Once your Meta Ads account is connected, Facebook campaigns, spend and results will appear here.') })}`;
}

export async function instagram() {
  return `
  ${pageHead({ eyebrow: 'Advertising', title: 'Instagram',
    sub: 'Instagram ad campaigns, spend and results.',
    actions: [{ label: 'Connect Meta Ads', kind: 'primary', icon: 'link' }] })}
  ${block({ body: notConnected('Instagram Ads',
    'Instagram ads run through the same Meta Ads account as Facebook. Once connected, Instagram-specific campaigns and results will appear here.') })}`;
}
