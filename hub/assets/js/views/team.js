/* TEAM — the employer ↔ employee surface. Roster, chat, shared files.
   Chat composes from the standard vocabulary plus chat-specific classes in
   app.css. Interaction is demo-scoped: messages you send live in memory for
   the session (never storage — see the no-persistence rule) and attachments
   preview locally via FileReader. Real delivery arrives with the team API. */

import { pageHead, block, pill, btn, note, filterbar,
         esc, grid, icon, mediaCard } from '../ui.js';

/* ------------------------------------------------------------------ */
/* In-memory demo state. One object, reset on reload, never persisted.  */
/* ------------------------------------------------------------------ */
const state = { person: null, sent: {}, files: [] };

function threadFor(id, data) {
  return [...(data.threads[id] || []), ...(state.sent[id] || [])];
}

function avatar(p, extra = '') {
  return `<div class="tavatar ${extra}">${esc(p.initials)}</div>`;
}

function attachHtml(a) {
  if (!a) return '';
  if (a.preview) {
    return `<img class="tattach__shot" src="${a.preview}" alt="${esc(a.name)}">
      <div class="tmsg__when" style="margin-top:4px">${esc(a.name)} · ${esc(a.meta || '')}</div>`;
  }
  if (a.kind === 'image') {
    return `<button type="button" class="tattach tattach--img" title="${esc(a.name)}">
      ${icon('image')}<span>${esc(a.name)}</span><em>${esc(a.meta || '')}</em></button>`;
  }
  return `<button type="button" class="tattach tattach--doc" title="${esc(a.name)}">
    ${icon('description')}<span>${esc(a.name)}</span><em>${esc(a.meta || '')}</em>
    <i>${icon('download')}</i></button>`;
}

/* a row with the person's avatar in the icon slot — row() can't do this,
   so it's composed here from the same class vocabulary */
function personRow(p, main, side) {
  return `<div class="row">
    ${avatar(p, 'tavatar--row')}
    <div class="row__main">${main}</div>
    ${side ? `<div class="row__side">${side}</div>` : ''}
  </div>`;
}

function bubble(m) {
  const mine = m.from === 'you';
  return `<div class="tmsg${mine ? ' tmsg--you' : ''}">
    <div class="tmsg__bub">
      ${m.text ? `<div class="tmsg__text">${esc(m.text)}</div>` : ''}
      ${attachHtml(m.attach)}
      <div class="tmsg__when">${esc(m.when)}</div>
    </div>
  </div>`;
}

/* ---- thread switching + composer (demo, in-memory) ---- */
function wireChat(data) {
  const list = document.getElementById('tPeople');
  const head = document.getElementById('tHead');
  const body = document.getElementById('tThread');
  const input = document.getElementById('tInput');
  const picker = document.getElementById('tPicker');
  if (!list || !body) return;

  const drawThread = () => {
    const p = data.roster.find(x => x.id === state.person);
    const msgs = threadFor(state.person, data);
    if (head && p) head.innerHTML = `
      ${avatar(p)}
      <div style="min-width:0">
        <div class="row__title">${esc(p.name)}</div>
        <div class="row__meta">${esc(p.role)} · ${esc(p.status)}</div>
      </div>
      <span style="margin-left:auto">${pill('Demo preview', 'warn')}</span>`;
    if (body) {
      body.innerHTML = msgs.map(bubble).join('') || '';
      body.scrollTop = body.scrollHeight;
    }
    if (list) [...list.querySelectorAll('.tperson')].forEach(el =>
      el.classList.toggle('is-active', el.dataset.id === state.person));
  };

  const send = (text, attach = null) => {
    if (!text && !attach) return;
    const now = new Date();
    const hh = now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    (state.sent[state.person] ||= []).push({ from: 'you', when: `Today ${hh}`, text, attach });
    drawThread();
  };

  list?.addEventListener('click', e => {
    const p = e.target.closest('.tperson');
    if (!p) return;
    state.person = p.dataset.id;
    drawThread();
  });

  document.getElementById('tSend')?.addEventListener('click', () => {
    if (!input) return;
    send(input.value.trim());
    input.value = '';
  });
  input?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input.value.trim()); input.value = ''; }
  });

  /* local preview only — FileReader to a data URL shown in the thread.
     Nothing is uploaded anywhere in demo mode. */
  picker?.addEventListener('change', () => {
    const f = picker.files?.[0];
    if (!f) return;
    const kb = f.size / 1024;
    const size = kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(kb))} KB`;
    const meta = `${f.type.startsWith('image') ? 'Photo' : 'Document'} · ${size}`;
    const attach = { kind: f.type.startsWith('image') ? 'image' : 'doc', name: f.name, meta };
    if (attach.kind === 'image') {
      const r = new FileReader();
      r.onload = () => send('', { ...attach, preview: String(r.result) });
      r.readAsDataURL(f);
    } else {
      send('', attach);
    }
    picker.value = '';
  });

  state.person ||= data.roster[0]?.id;
  drawThread();
}

/* image attachments the owner just picked render as a real thumb via
   attachHtml(a.preview) — see the picker handler in wireChat */

/* ------------------------------------------------------------------ */
/* Views                                                               */
/* ------------------------------------------------------------------ */
export async function overview({ api }) {
  const res = await api.get('team.roster');
  const data = res.ok ? res.data : { roster: [], files: [] };
  const unread = data.roster.reduce((n, p) => n + (p.unread || 0), 0);
  const files = [...data.files, ...state.files];

  return `
  ${pageHead({ eyebrow: 'Team', title: 'Your team',
    sub: 'Message anyone on your crew, share photos and documents, and keep every job conversation in one place.',
    actions: [{ label: 'Open chat', kind: 'primary', icon: 'chat', href: '#/team/chat' }] })}
  ${unread ? note(`${unread} unread message${unread > 1 ? 's' : ''} from your team.`, 'warn') : ''}
  ${block({ title: 'Everyone', icon: 'diversity_3', flush: true, body:
    data.roster.map(p => personRow(p,
      `<div class="row__title">${esc(p.name)}</div>
       <div class="row__meta">${esc(p.role)} · ${esc(p.status)}</div>`,
      `<a class="btn btn--sm btn--ghost" href="#/team/chat">${icon('chat')}Message</a>
       ${p.unread ? pill(String(p.unread), 'bad') : ''}`)).join('')
  })}
  ${grid(`
    ${block({ title: 'Latest from the crew', icon: 'forum', flush: true, body:
      data.roster.slice(0, 3).map(p => {
        const t = threadFor(p.id, data);
        const last = t[t.length - 1];
        return personRow(p,
          `<div class="row__title">${esc(p.name)}</div>
           <div class="row__meta">${esc(last ? (last.text || last.attach?.name || '') : 'No messages yet')}</div>`,
          pill(last?.when?.startsWith('Today') ? 'Today' : 'Older', last?.when?.startsWith('Today') ? 'info' : 'neutral'));
      }).join('') })}
    ${block({ title: 'Shared files', icon: 'folder_shared',
       action: { label: 'All files', href: '#/team/files' }, body: `
      ${files.slice(0, 4).map(f => `
        <div class="tfile">
          ${icon(f.kind === 'image' ? 'image' : 'description')}
          <div style="min-width:0">
            <div class="row__title" style="font-size:14px">${esc(f.name)}</div>
            <div class="row__meta">${esc(f.from)} · ${esc(f.when)}</div>
          </div>
        </div>`).join('')}
    ` })}
  `, 'sidebar')}
  ${note('Team messaging is a preview right now — messages live in this session and don\'t reach real phones until the team system is connected.', 'warn')}`;
}

export async function chat({ api }) {
  const res = await api.get('team.threads');
  const data = res.ok ? res.data : { roster: [], threads: {} };

  const people = data.roster.map(p => {
    const t = threadFor(p.id, data);
    const last = t[t.length - 1];
    return `<button type="button" class="tperson" data-id="${esc(p.id)}">
      ${avatar(p)}
      <span class="tperson__main">
        <span class="tperson__name">${esc(p.name)}</span>
        <span class="tperson__last">${esc(last ? (last.text || last.attach?.name || '') : 'No messages yet')}</span>
      </span>
      ${p.unread ? `<span class="navlink__badge">${p.unread}</span>` : ''}
    </button>`;
  }).join('');

  const html = `
  ${pageHead({ eyebrow: 'Team', title: 'Chat',
    sub: 'Back and forth with your crew — type, or attach a photo or document right into the thread.' })}
  <div class="tchat">
    <div class="card tchat__side">
      <div class="block__head"><span class="block__icon">${icon('diversity_3')}</span>
        <span class="block__title">Your crew</span></div>
      <div id="tPeople" class="tchat__people">${people}</div>
    </div>
    <div class="card tchat__main">
      <div class="tchat__head" id="tHead"></div>
      <div class="tchat__scroll" id="tThread"></div>
      <div class="tcomposer">
        <label class="iconbtn" title="Attach a photo or document">
          ${icon('attach_file')}<input id="tPicker" type="file" accept="image/*,.pdf,.doc,.docx,.txt" hidden>
        </label>
        <textarea id="tInput" rows="1" placeholder="Write to your crew…"></textarea>
        <button type="button" id="tSend" class="btn btn--primary btn--sm">${icon('send')}Send</button>
      </div>
    </div>
  </div>
  ${note('Demo preview — sending works for this session, and attachments show as a local preview. Real delivery to employee phones arrives when the team system is connected.', 'warn')}`;

  /* the view returns a string; wiring happens after the router mounts it —
     setTimeout(0) lands after the router's await continuation sets innerHTML */
  setTimeout(() => wireChat(data), 0);
  return html;
}

export async function files({ api }) {
  const res = await api.get('team.files');
  const data = res.ok ? res.data : { files: [] };
  const all = [...data.files, ...state.files];

  return `
  ${pageHead({ eyebrow: 'Team', title: 'Shared files',
    sub: 'Every photo and document the crew has shared — job photos, sign-offs, receipts, schedules.',
    actions: [{ label: 'Upload', kind: 'primary', icon: 'upload_file' }] })}
  ${filterbar([
    { label: 'Everything', count: all.length },
    { label: 'Photos', icon: 'image', count: all.filter(f => f.kind === 'image').length },
    { label: 'Documents', icon: 'description', count: all.filter(f => f.kind === 'doc').length }
  ])}
  ${block({ title: 'From your crew', icon: 'folder_shared', flush: true, body: `
    <div class="media">
      ${all.map(f => mediaCard({
        icon: f.kind === 'image' ? 'image' : 'description',
        name: f.name,
        meta: `${f.from} · ${f.when} · ${f.meta}`
      })).join('')}
    </div>
  ` })}
  ${note('Files live with the chat when connected — this page collects everything your crew has shared in one place.', '')}`;
}
