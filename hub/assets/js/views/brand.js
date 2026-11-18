/* BRAND — the section Danielle's mockup did best (app/brand-hub.html).
   Structure follows hers closely: completeness score, logo library with format
   chips, palette with copy-hex, type specimens, voice as sound-like/avoid. */

import { pageHead, tiles, block, rows, pill, btn, note, swatch, progress,
         filterbar, mediaCard, esc, grid, icon, notConnected } from '../ui.js';

export async function overview({ api }) {
  const res = await api.get('brand.kit');
  const b = res.data || {};
  return `
  ${pageHead({ eyebrow: 'Brand', title: 'Your brand',
    sub: 'Everything that makes you look and sound like you — in one place, so every post, page and advert matches.',
    actions: [{ label: 'Upload an asset', kind: 'primary', icon: 'upload' }] })}
  ${block({ title: 'Brand completeness', icon: 'health_and_safety', body: !res.ok && api.isLive
    // res.note is an INTERNAL engineering note ("4 fragmented stores; needs a unifier") meant
    // for this codebase, not the client — it was leaking straight to the screen (host-measured
    // 2026-11-18). Always use a fixed client-facing sentence here instead.
    ? notConnected('Your brand kit', 'Your brand kit is not connected yet.') : `
    ${progress(b.completeness ?? 0, 'Your kit is nearly there', `${b.completeness ?? 0}% complete`)}
    <div class="grid grid--3" style="margin-top:18px">
      ${(b.checklist || []).map(c => `<div class="row" style="padding:12px;border:0;
        background:rgba(255,255,255,.5);border-radius:12px">
        <div class="row__icon" style="background:${c.ok ? 'var(--ok-bg)' : 'var(--warn-bg)'};
          color:${c.ok ? 'var(--ok)' : 'var(--warn)'}">${icon(c.ok ? 'check' : 'priority_high')}</div>
        <div class="row__main"><div class="row__title">${esc(c.label)}</div>
        <div class="row__meta">${esc(c.note)}</div></div></div>`).join('')}
    </div>` })}
  ${grid(`
    ${block({ title: 'Your colours', icon: 'palette', action: { label: 'All', href: '#/brand/colors' },
      body: `<div class="swatches">${(b.colors || []).slice(0, 4).map(swatch).join('')}</div>` })}
    ${block({ title: 'Your logos', icon: 'diamond', action: { label: 'All', href: '#/brand/logos' },
      flush: true, body: rows((b.logos || []).slice(0, 3).map(l => ({
        icon: 'image', title: l.name, meta: l.formats.join(' · '),
        side: btn({ label: 'Download', small: true, icon: 'download' }) }))) })}
  `)}`;
}

export async function logos({ api }) {
  const res = await api.get('brand.kit');
  return `
  ${pageHead({ eyebrow: 'Brand', title: 'Logos & assets',
    sub: 'Download-ready in every format you will be asked for.',
    actions: [{ label: 'Upload', kind: 'primary', icon: 'upload' }] })}
  ${filterbar([{ label: 'All' }, { label: 'Logos' }, { label: 'Documents' }, { label: 'Templates' }])}
  ${block({ flush: true, body: rows((res.data?.logos || []).map(l => ({
    icon: 'diamond', title: l.name, meta: `${l.formats.join(' · ')} · ${l.note || ''}`,
    side: `${btn({ label: 'Download', small: true, icon: 'download' })}
           ${btn({ label: 'Copy link', small: true, icon: 'link' })}` }))) })}
  ${block({ title: 'Documents', icon: 'folder', flush: true, body: rows((res.data?.docs || []).map(d => ({
    icon: d.icon, title: d.name, meta: `${d.kind} · ${d.size}`,
    side: btn({ label: 'Open', small: true }) }))) })}`;
}

export async function colors({ api }) {
  const res = await api.get('brand.kit');
  const b = res.data || {};
  return `
  ${pageHead({ eyebrow: 'Brand', title: 'Colours & type',
    sub: 'Tap any colour to copy it. Hand these to anyone printing or building something for you.' })}
  ${block({ title: 'Your palette', icon: 'palette',
    body: `<div class="swatches">${(b.colors || []).map(swatch).join('')}</div>` })}
  ${block({ title: 'Your typefaces', icon: 'match_case', body: `
    ${(b.fonts || []).map(f => `<div style="margin-bottom:22px">
      <div style="font-size:12px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
        color:var(--outline);margin-bottom:7px">${esc(f.role)}</div>
      <div style="font-family:${esc(f.stack)};font-size:30px;font-weight:${f.weight};
        color:var(--primary);line-height:1.25">${esc(f.specimen)}</div>
      <div style="font-family:${esc(f.stack)};font-size:14px;color:var(--on-surface-variant);
        margin-top:6px">${esc(f.name)} · ${esc(f.note)}</div>
    </div>`).join('')}` })}`;
}

export async function voice({ api }) {
  const res = await api.get('brand.kit');
  const v = res.data?.voice || {};
  return `
  ${pageHead({ eyebrow: 'Brand', title: 'Your voice',
    sub: 'How you sound. Everything we write for you follows this — change it here and the writing changes.' })}
  ${block({ title: 'How you sound', icon: 'record_voice_over', body: `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px">
      ${(v.traits || []).map(t => pill(t, 'info')).join('')}
    </div>
    <div class="grid grid--2">
      <div class="card" style="padding:16px;background:rgba(223,243,232,.5)">
        <div style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
          color:var(--ok);margin-bottom:8px">We sound like</div>
        <p style="margin:0;font-size:15px;color:var(--on-surface-variant)">${esc(v.like || '')}</p></div>
      <div class="card" style="padding:16px;background:rgba(255,218,214,.4)">
        <div style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
          color:var(--bad);margin-bottom:8px">We never sound like</div>
        <p style="margin:0;font-size:15px;color:var(--on-surface-variant)">${esc(v.avoid || '')}</p></div>
    </div>
    <div style="margin-top:16px">${btn({ label: 'Change how we sound', icon: 'edit', small: true })}</div>` })}
  ${block({ title: 'Approved wording', icon: 'format_quote', body:
    (v.messaging || []).map(m => `<div style="margin-bottom:18px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:7px">
        <span style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
          color:var(--outline)">${esc(m.label)}</span>${pill('Approved', 'ok')}</div>
      <p style="margin:0 0 8px;font-size:14.5px;color:var(--on-surface-variant);max-width:74ch">
        ${esc(m.text)}</p>
      ${btn({ label: 'Copy', small: true, icon: 'content_copy' })}
    </div>`).join('') })}`;
}

export async function photos({ api }) {
  const res = await api.get('brand.kit');
  return `
  ${pageHead({ eyebrow: 'Brand', title: 'Photography',
    sub: 'Your own job photos beat stock every time — they are the single easiest thing you can send us.',
    actions: [{ label: 'Upload photos', kind: 'primary', icon: 'add_a_photo' }] })}
  ${note('Snap before-and-after shots on any job and text them to us. We handle the cropping, captions and where they go.')}
  ${block({ title: 'Your photos', icon: 'photo_library', body:
    `<div class="media">${(res.data?.photos || []).map(mediaCard).join('')}</div>` })}`;
}
