#!/usr/bin/env python3
"""crt-verdict-watch.py — independent watcher for pack:risk-transfer verdicts (NEWBORN, advisory only).

Two kinds of Meets claim are judged: every sub-on-project whose LATEST stored review (crt_review) says
Meets (plain, contractor-accepted, or expiring), and, when given (--live-url / --live-json), every LIVE
Meets state the API shows staff. For each it re-derives, from the human-confirmed readings in the
database and the bytes in the private file store, whether those inputs actually support Meets. It is a
different instrument from the engine: it imports nothing from the OVU code, opens business.db READ-ONLY,
re-hashes files itself, and has its own (deliberately narrow) rules. It never changes a verdict, never
writes, never messages anyone.

What a Meets must be backed by (plan S18, design section 4.4 rule 1, claim-check 2026-11-17):
  R1 the requirement set is APPROVED and a PERSON confirmed it (crt_confirmation by clerk:/local:)
  R2 every confirmation the review says it read exists, is not archived and was made by a person
  R3 every required line (limit / ai items) has a policy for this sub, received for THIS account,
     person-confirmed (line, effective, expiration, source document) against a stored file whose bytes
     still hash to the confirmed sha256, that file's document received for THIS account and THIS sub,
     and IN FORCE on the claim date (effective <= date < expiration); read here, independently (rules
     1.3.0, MED ruling): its person-confirmed LIMIT meets the item's minimum (or the combined GL +
     umbrella test, or a waiver of LIMIT_LOW), its person-confirmed NAMED INSURED is this sub (the same
     legal name, entity suffix included: never waivable), and an AI item's AI box is ticked (or waived)
  R3x every required disallowed-exclusion item that applies to the sub's trade CATEGORY (a listed
     category; free text never matches by substring) has a policy whose person-confirmed exclusions are
     classes (not wording), none of them a class the item disallows (or a waiver of EXCLUSION:<code>);
     a blank list counts only when a person confirmed "none"
  R4 every required additional-insured endorsement (ai_ongoing / ai_completed, or names_parties) has an
     endorsement on that line, for this account, whose OWN document is of kind 'endorsement' with that
     kind written or confirmed by a PERSON, for this account and sub, hash-verified, whose file is NOT
     any certificate file of this sub, person-confirmed (including which policy it attaches to), that
     names this account and this project EXACTLY (normalised equality, never containment) (or is
     blanket with the project's contract tick person-confirmed true), whose person-confirmed FORM NUMBER is
     one the item lists (a manuscript form excepted) and whose EDITION reads as a date from 2004 on (a
     pre-2004 one needs a waiver of WRONG_FORM; an empty or unreadable one is never backed). A naming miss (WRONG_PARTY,
     WRONG_PROJECT, BLANKET_UNBACKED) counts only when a person recorded an unexpired waiver backed by
     THIS account's written OK (kind contractor_waiver_ok). A missing document is never waivable.
     Ongoing ops are not required after substantial completion; completed ops not after the statute
     of repose.
  R5 the account, sub, project and assignment rows exist, the sub is linked to the account, and the
     names, the project's account and its dates were each written or confirmed by a person
  (rules 1.4.0) R4 also: CG 20 10 never counts for completed ops; a listed ISO form number is never read as
     manuscript; a manuscript counts for completed ops only with its completed-operations wording
     person-confirmed; the set's own approved form table (form_functions) classifies forms; the item's
     edition must match the endorsement's (month and year), or a waiver of WRONG_EDITION.
     R5/R6 also: a person's write vouches for a field only when the store stamped it as made on the
     platform staff surface (activity diff 'staff_surface'); any other person-write is nobody's word.
  (rules 1.5.0, host rulings E/F/P/C, checked here with this file's own code)
  RE every check runs on every endorsement: an edition or pre-2004 finding never stops the naming checks; each
     naming miss (WRONG_PARTY and WRONG_PROJECT separately) needs its own waiver; the contract's AI party list must
     be person-confirmed against this account's paper; an endorsement on the item nobody confirmed is not backed
  RF the set meets the built-in safety floor: every endorsement item lists forms; a built-in ISO number keeps its
     ISO category (CG 20 10 / 20 33 / 20 38 ongoing only); completed operations lists only CG 20 37 or a form the
     set's own table lists as completed operations
  RP the last archive / unarchive of every record the claim relies on was made by a person on the staff surface
     (activity stamp), or staff confirmed the record again at its current version since
  RC the agency record is live with a person-written name; every policy relied on has producer_is_cca written by
     a person on the staff surface; an agency-produced policy has two different people's confirmations; every
     confirmation counted was made on the staff surface (its activity stamp)
  (rules 1.6.0, host rulings D/M/U/G/W/K/X4, checked here with this file's own code)
  RD the pinned set's approval names the diff it was shown (X4) and records what it was compared against, and that was an APPROVED version up the set's
     own supersedes chain (approved by a person on the staff surface at the recorded version), or the template /
     nothing only when no ancestor was ever approved: an approval compared against a draft is not backed
  RM a limit item's minimum is its min, or its floor when min is missing or null (never $0); an item with neither,
     a min below its floor, or an item key outside the known list is below the floor
  RU the umbrella the combined GL + umbrella test leans on is person-confirmed, in force, and names this sub
  RG an ISO number (CG 20 xx, the floor) is never read as a manuscript; CG 20 26 / CG 20 11 never count as completed
     operations
  RW a waiver counts only for the paper it was bound to: its recorded (function, document id, file hash) must be
     the function and document raising the code now, and that file must still hash to the recorded value
  RK blanket counts only when the endorsement's schedule is empty; a schedule that names anyone is checked
  (rules 1.7.0, gate-4 re-check #7, checked here with this file's own code)
  RA an item's `required` is true or false; anything else stored (null, 0, "") is below the floor; only false skips
  RB a COMPLETED_OPS_LAPSED waiver counts only on the term's paper it was bound to (the in-force policy's certificate,
     hash-verified) and only while that policy carries no completed-ops endorsement at all; a waiver bound to no
     paper never counts (RW)
  RC7 a form number is read however it is written ('CG 20 10 04 13', 'ISO CG 20 10', CG 20 xx inside other text):
     CG 20 10 / 20 26 / 20 11 anywhere in it is never completed operations and never a manuscript; the set's table and
     form lists are read the same way; a number printed with another edition than the edition field is not backed
  (rules 1.8.0, gate-4 re-check #8, checked here with this file's own code)
  RN8 one normalization of form numbers and editions (this file's own copy of the contract, cross-checked with the
     engine's by a test): NFKC, upper case, look-alike letters (Cyrillic, Greek, ...) to Latin, any Unicode digit to
     ASCII, a letter O inside a number read as 0. A form or edition that needed any of that is not backed, and a
     number one character away from a CG 20 xx is never a manuscript
  RE8 an edition written after the number in the item's form list is the item's edition when its edition field is
     empty; two different editions for one item, or an edition field holding a form number, is below the floor
  RG8 an endorsement whose edition field names another form number than its form field is not backed
  R6 each assignment field the verdict reads (project, sub, set, set version, trade, scope change) was
     last written by a person or person-confirmed (per field, read from the activity log: a staff
     hold or note never vouches for a field the agent wrote), names the set the verdict used, and
     pinned that set's current version
If a record the claim read was changed after the claim, the watcher cannot see the old version:
that claim is CANNOT-TELL, never PASS. If the claim is backed by the documents as they were at the
claim, but a document or record it read was archived since, the row is STALE (right when made, not
now; re-review), never a disagreement.

Verdict: PASS (exit 0) every Meets is backed / FAIL (exit 1) at least one Meets is NOT backed — the
watcher disagrees with the stored verdict / CANNOT-TELL (exit 3) unreadable store, nothing to check, a
review it cannot judge, or a STALE review. Output is a report on stdout (--json for machines).

  tools/biz/crt-verdict-watch.py --db-dir /mnt/clients/<tenant>/openvoiceui/db
  tools/biz/crt-verdict-watch.py --db-dir <db dir>/sandboxes/josh --json
  BIZ_WATCH_KEY=... tools/biz/crt-verdict-watch.py --db-dir <db dir>/sandboxes/josh \\
      --live-url https://test-dev.jam-bot.com --sandbox josh                       (also the live states)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import date, datetime

PASS, FAIL, CANNOT, STALE = 'PASS', 'FAIL', 'CANNOT-TELL', 'STALE'
EXIT = {PASS: 0, FAIL: 1, CANNOT: 3}
MEETS_STATES = ('meets', 'meets_accepted', 'expiring_30', 'expiring_15')
PERSON = ('clerk:', 'local:')
ASG_FIELDS = ('crt_project_id', 'party_contact_id', 'requirement_set_id', 'requirement_set_version',
              'trade_category', 'scope_changed_at')
PROJECT_FIELDS = ('crt_account_id', 'name', 'substantial_completion', 'repose_years')
_ABSENT = object()
# Own copies (never imported from the engine): rules 1.4.0, host rulings H1/H2/H3/R3/R4.
_ENTITY = {'incorporated': 'inc', 'inc': 'inc', 'l l c': 'llc', 'llc': 'llc', 'corporation': 'corp', 'corp': 'corp',
           'company': 'co', 'co': 'co', 'limited': 'ltd', 'ltd': 'ltd', 'l p': 'lp', 'lp': 'lp', 'l l p': 'llp',
           'llp': 'llp', 'lllp': 'lllp', 'p l l c': 'pllc', 'pllc': 'pllc', 'p c': 'pc', 'pc': 'pc', 'p a': 'pa',
           'pa': 'pa', 'plc': 'plc', 'limited liability company': 'llc', 'limited liability partnership': 'llp',
           'limited partnership': 'lp', 'professional corporation': 'pc',
           'professional limited liability company': 'pllc'}
_TRADES = {'excavation', 'trenching', 'earthwork', 'grading', 'demolition', 'blasting', 'shoring',
           'underground_utilities', 'pile_driving', 'tunneling', 'structural_steel', 'steel_erection', 'crane_rigging',
           'heavy_civil', 'roofing', 'waterproofing', 'windows_glazing', 'curtain_wall', 'siding_cladding', 'hvac',
           'electrical', 'plumbing', 'fire_protection', 'concrete', 'framing', 'masonry', 'painting', 'drywall',
           'flooring', 'tile', 'landscaping', 'signage', 'architect', 'engineer', 'other'}
_EXCL = {'none', 'xcu', 'explosion', 'collapse', 'underground', 'water_intrusion', 'moisture', 'water_damage_sublimit',
         'insured_vs_insured', 'residential', 'eifs', 'subsidence', 'mold', 'silica', 'pollution', 'action_over',
         'designated_ongoing_operations', 'classification_limitation', 'height_limitation', 'other_reviewed'}
_EXCL_FAMILY = {'xcu': {'xcu', 'explosion', 'collapse', 'underground'},
                'water': {'water_intrusion', 'moisture', 'water_damage_sublimit'}}
_ISO = {'CG2010': 'ai_ongoing', 'CG2033': 'ai_ongoing', 'CG2038': 'ai_ongoing', 'CG2037': 'ai_completed',
        'CG2001': 'pnc', 'CG2503': 'project_aggregate', 'CG2545': 'completed_ops_aggregate',
        'CG2404': 'waiver_of_subrogation'}
_NEVER_COMPLETED = 'CG2010'        # host ruling R3: never completed operations, by any route
_ISO_FLOOR = dict(_ISO)            # host ruling F (rules 1.5.0): a built-in ISO number keeps its category in any set
_NOT_COMPLETED = {'CG2026', 'CG2011'}   # other ISO AI numbers that never cover completed operations
_ISO_AI = re.compile(r'^CG20\d\d$')   # rules 1.6.0 (G): the ISO additional-insured series, never a manuscript
_ITEM_KEYS = {'id', 'kind', 'line', 'required', 'cite', 'limit', 'floor', 'ceiling', 'min', 'function', 'forms',
              'edition', 'names_parties', 'suggested_when', 'code', 'classes', 'combined', 'trade_categories',
              'tail_required', 'when_trade'}   # rules 1.6.0 (M): the keys the approval page names
SURFACE = 'staff_surface'          # activity diff: the fields a person wrote on the platform staff surface (R1)
_MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']


def _norm(s):
    """Legal-name key: NFKC, casefold, punctuation gone, letters kept (any script), the entity suffix KEPT in one
    spelling per kind (Inc / Inc. / Incorporated -> inc; LLC / L.L.C. -> llc). LLC != Inc != Corp."""
    if not isinstance(s, str):
        return ''
    text = unicodedata.normalize('NFKC', s).casefold().replace('&', ' and ')
    words = ''.join(ch if ch.isalnum() else ' ' for ch in text).split()
    merged = []
    for w in words:                        # initials written with dots or spaces: 'l l c' -> one word
        if len(w) == 1 and w.isalpha() and merged and merged[-1][1]:
            merged[-1] = (merged[-1][0] + ' ' + w, True)
        else:
            merged.append((w, len(w) == 1 and w.isalpha()))
    joined = ' '.join(w.replace(' ', '') for w, _ in merged)
    for phrase in sorted((k for k in _ENTITY if ' ' in k and len(k) > 5), key=len, reverse=True):
        joined = re.sub(r'(?<!\S)' + re.escape(phrase) + r'(?!\S)', _ENTITY[phrase], joined)
    return ' '.join(_ENTITY.get(w, w) for w in joined.split())


def _trade(s):
    k = '_'.join(''.join(ch if ch.isalnum() else ' ' for ch in unicodedata.normalize('NFKC', str(s or '')).casefold()).split())
    return k if k in _TRADES else None


def _excl(values):
    """(classes, unmapped) of a policy's exclusions; blank -> ([], [])."""
    if values in (None, ''):
        values = []
    if not isinstance(values, list):
        values = [values]
    cls, bad = set(), []
    for v in values:
        k = '_'.join(''.join(ch if ch.isalnum() else ' ' for ch in unicodedata.normalize('NFKC', str(v)).casefold()).split())
        (cls.add(k) if k in _EXCL else bad.append(v))
    return cls, bad


# RN8 (rules 1.8.0): this file's own copy of the normalization contract (never imported from the engine)
_LOOKALIKE = {
    '\u0410': 'A', '\u0412': 'B', '\u0421': 'C', '\u0415': 'E', '\u041d': 'H', '\u0406': 'I', '\u0408': 'J',
    '\u041a': 'K', '\u041c': 'M', '\u041e': 'O', '\u0420': 'P', '\u0405': 'S', '\u0422': 'T', '\u0425': 'X',
    '\u0423': 'Y', '\u04ae': 'Y', '\u050c': 'G', '\u051a': 'Q', '\u051c': 'W', '\u0417': '3', '\u04c0': 'I',
    '\u04cf': 'I', '\u0400': 'E', '\u0401': 'E', '\u0407': 'I',
    '\u0391': 'A', '\u0392': 'B', '\u0395': 'E', '\u0396': 'Z', '\u0397': 'H', '\u0399': 'I', '\u039a': 'K',
    '\u039c': 'M', '\u039d': 'N', '\u039f': 'O', '\u03a1': 'P', '\u03a4': 'T', '\u03a5': 'Y', '\u03a7': 'X',
    '\u03f9': 'C', '\u037f': 'J', '\u03dc': 'F',
    '\u0555': 'O', '\u054d': 'U', '\u0261': 'G', '\ua7ac': 'G', '\u0262': 'G', '\u1d04': 'C', '\u1d0f': 'O',
    '\u0131': 'I', '\u01c0': 'I', '\u2c9e': 'O', '\u2ca2': 'P', '\u2ca4': 'C',
}
_LATIN = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')


def _fold(s):
    """RN8: NFKC, upper case, a look-alike letter to its Latin letter, any Unicode decimal digit to its ASCII digit."""
    out = ''
    for ch in unicodedata.normalize('NFKC', str(s or '')).upper():
        if ch in _LOOKALIKE:
            out += _LOOKALIKE[ch]
        elif not ch.isascii() and unicodedata.decimal(ch, None) is not None:
            out += str(unicodedata.decimal(ch))
        else:
            out += ch
    return out


def _zero(t):
    """RN8: a letter O inside a number is the digit 0: right after a digit, or starting a number (no letter right
    before it) when a digit follows its run of O's."""
    res = ''
    for i in range(len(t)):
        ch = t[i]
        if ch == 'O':
            before = res[-1:]
            if before and before in '0123456789':
                ch = '0'
            elif not (before and before.isascii() and before.isupper()):
                rest = t[i:].lstrip('O')
                if rest[:1] and rest[:1] in '0123456789':
                    ch = '0'
        res += ch
    return res


def _squash(s):
    return _zero(''.join(ch for ch in _fold(s) if ch in _LATIN))


def _refold(s):
    """RN8: the value is not plain ASCII, or a letter O stands inside a number: not backed as written."""
    if s is None:
        return False
    if not str(s).isascii():
        return True
    k = ''.join(ch for ch in _fold(s) if ch in _LATIN)
    return _zero(k) != k


def _near_iso(s):
    """RN8: a CG 20 xx number, or one character away from one, anywhere in the field: never a manuscript."""
    k = _squash(s)
    for i in range(len(k) - 5):
        miss = sum(1 for a, b in zip(k[i:i + 4], 'CG20') if a != b) + sum(1 for c in k[i + 4:i + 6] if not c.isdigit())
        if miss <= 1:
            return True
    return False


def _form_parts(s):
    """RC7 (rules 1.7.0, own code): (form key, (month, year) of an edition printed after the number or None). Two
    letters + four digits, optionally after 'ISO' and before an edition ('CG 20 10 04 13', 'ISO CG 20 10'), is that
    number; anything else is its letters and digits."""
    k = _squash(s)
    body = k[3:] if k.startswith('ISO') and len(k) > 3 else k
    if len(body) >= 6 and body[:2].isalpha() and body[2:6].isdigit():
        rest = body[6:]
        rest = rest[7:] if rest.startswith('EDITION') else rest[2:] if rest.startswith('ED') else rest
        if not rest:
            return body[:6], None
        if rest.isdigit() and len(rest) in (4, 6):
            my = _edition_my(rest)
            if my:
                return body[:6], my
    return k, None


def _form(s):
    return _form_parts(s)[0]


def _iso_in(s):
    """RC7: every ISO additional-insured number (CG 20 xx or a built-in ISO number) written anywhere in a form field."""
    k, out = _squash(s), set()
    for i in range(len(k) - 5):
        n = k[i:i + 6]
        if n.startswith('CG') and n[2:].isdigit() and (n in _ISO_FLOOR or n in _NOT_COMPLETED or _ISO_AI.match(n)):
            out.add(n)
    return out


def _never_completed(s):
    """RG/RC7: CG 20 10 / 20 26 / 20 11 named anywhere in the form field: never completed operations."""
    bad = {_NEVER_COMPLETED} | _NOT_COMPLETED
    return _form(s) in bad or bool(_iso_in(s) & bad)


def _clash(s, fn):
    """RF read per RC7: why mapping form field s to fn loosens the ISO floor, or None."""
    for n in {_form(s)} | _iso_in(s):
        if n in _ISO_FLOOR and _ISO_FLOOR[n] != fn:
            return f'ISO {_ISO_FLOOR[n]}'
    if fn == 'ai_completed' and _never_completed(s):
        return 'never completed operations'
    return None


def _edition(s):
    """Year of an ISO edition date as printed ('04 13', '11-85', '(11/85)', '1185', 'Nov 1985', 'CG 20 10 11 85')."""
    my = _edition_my(s)
    return my[1] if my else None


def _edition_read(s):
    """RE8/RN8 (own code): (form number printed before the edition or None, (month, year) or None). Folded; any mark
    other than a letter or digit is a gap; a run of letters and a run of digits are separate words."""
    t = _zero(''.join(ch if ch in _LATIN else ' ' for ch in _fold(s)))
    words, cur = [], ''
    for ch in t:
        if cur and (ch == ' ' or ch.isdigit() != cur[-1].isdigit()):
            words.append(cur)
            cur = ''
        if ch != ' ':
            cur += ch
    if cur:
        words.append(cur)
    form = None
    if len(words) >= 2 and len(words[0]) == 2 and words[0].isalpha() and words[1].isdigit():
        num, j = '', 1
        while j < len(words) and words[j].isdigit() and len(num) < 4:
            num += words[j]
            j += 1
        if len(num) >= 4:
            form = words[0] + num[:4]
            words = ([num[4:]] if num[4:] else []) + words[j:]
    mon = yr = None
    if len(words) == 2 and words[0].isalpha() and words[0][:3] in _MON and words[1].isdigit():
        mon, yr = _MON.index(words[0][:3]) + 1, words[1]
    elif len(words) == 2 and words[0].isdigit() and words[1].isdigit():
        mon, yr = (int(words[0]) if len(words[0]) < 3 else 0), words[1]
    elif len(words) == 1 and words[0].isdigit() and 3 <= len(words[0]) <= 6:
        t = words[0]
        if len(t) >= 5 and t[-4:-2] not in ('19', '20'):
            return form, None
        mon, yr = (int(t[:-4]), t[-4:]) if len(t) >= 5 else (int(t[:-2]), t[-2:])
    if mon is None or not 1 <= mon <= 12 or len(yr) not in (2, 4):
        return form, None
    y = int(yr)
    y = (1900 + y if y >= 50 else 2000 + y) if len(yr) == 2 else y
    return form, ((mon, y) if 1950 <= y <= 2100 else None)


def _edition_my(s):
    """(month, year) of an ISO edition date as printed, or None."""
    return _edition_read(s)[1]


def _item_ed(it):
    """RE8 (own code): (the edition the item requires, or None for any; why it cannot be read, or None)."""
    forms = it.get('forms') if isinstance(it.get('forms'), list) else []
    listed = set()
    for f in forms:
        if isinstance(f, str) and _form_parts(f)[1] is not None:
            listed.add(_form_parts(f)[1])
    if it.get('edition') not in (None, ''):
        want = _edition_my(it.get('edition'))
        if want is None:
            return None, 'edition is not a date'
        if any(x != want for x in listed):
            return None, 'the form list carries another edition than the edition field'
        return want, None
    if len(listed) > 1:
        return None, 'the form list carries two editions'
    return (list(listed)[0] if listed else None), None


def _named(wanted, texts):
    """Project naming (whole words): the engine reads WRONG_PROJECT the same way."""
    w = _norm(wanted)
    return bool(w) and any(_norm(t) == w or f' {w} ' in f' {_norm(t)} ' for t in (texts or []))


def _named_exact(wanted, texts):
    """Party naming: the same legal name after normalising, never a longer name containing it."""
    w = _norm(wanted)
    return bool(w) and any(_norm(t) == w for t in (texts or []))


def _day(s):
    try:
        return date.fromisoformat(s[:10]) if isinstance(s, str) else None
    except ValueError:
        return None


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


class Store:
    """business.db opened read-only, plus the private file directory."""

    def __init__(self, db_dir):
        self.db_dir = db_dir
        path = os.path.join(db_dir, 'business.db')
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        self.conn.row_factory = sqlite3.Row
        self.rows = {}
        for r in self.conn.execute('SELECT * FROM records'):
            self.rows[r['id']] = {'id': r['id'], 'collection': r['collection'], 'version': r['version'],
                                  'created_by': r['created_by'], 'updated_by': r['updated_by'],
                                  'updated_at': r['updated_at'], 'archived': bool(r['archived_at']),
                                  'archived_at': r['archived_at'], 'data': json.loads(r['data'])}
        self.events = {}
        for a in self.conn.execute("SELECT record_id, type, by, diff FROM activity "
                                   "WHERE type IN ('created', 'updated') ORDER BY id"):
            self.events.setdefault(a['record_id'], []).append(
                (a['type'], a['by'], json.loads(a['diff']) if a['diff'] else {}))
        self.toggles = {}         # archive / unarchive times per record: each bumped the version, not the data
        self.toggle_ev = {}       # (at, type, by, diff) per record: who archived / unarchived it, and where (ruling P)
        for a in self.conn.execute("SELECT record_id, at, type, by, diff FROM activity "
                                   "WHERE type IN ('archived', 'unarchived') ORDER BY id"):
            self.toggles.setdefault(a['record_id'], []).append(a['at'])
            self.toggle_ev.setdefault(a['record_id'], []).append(
                (a['at'], a['type'], a['by'], json.loads(a['diff']) if a['diff'] else {}))
        self._hash = {}
        self.at = None            # None: as things are now; an ISO time: as they were at that claim

    def view(self, at):
        v = object.__new__(Store)
        v.__dict__.update(self.__dict__)
        v.at = at
        return v

    def _visible(self, r):
        return not r['archived'] or (self.at is not None and (r['archived_at'] or '') > self.at)

    def all(self, coll):
        return [r for r in self.rows.values() if r['collection'] == coll and self._visible(r)]

    def version(self, rec):
        """The record's version at the claim: archiving and unarchiving since then changed no data."""
        if self.at is None:
            return rec['version']
        return rec['version'] - sum(1 for t in self.toggles.get(rec['id'], []) if t > self.at)

    def all_ever(self, coll):
        return [r for r in self.rows.values() if r['collection'] == coll]

    def get(self, coll, rid):
        r = self.rows.get(rid)
        return r if r and r['collection'] == coll and self._visible(r) else None

    def get_any(self, coll, rid):
        """The record whether archived or not (ruling P reads who archived it)."""
        r = self.rows.get(rid)
        return r if r and r['collection'] == coll else None

    def surface_made(self, rec):
        """The record was CREATED by a person on the staff surface (its 'created' activity carries the stamp)."""
        for typ, by, diff in self.events.get(rec['id'], []):
            if typ == 'created':
                return str(by or '').startswith(PERSON) and SURFACE in (diff or {})
        return False

    def toggle_unvouched(self, rec):
        """Ruling P: the last archive / unarchive (as of this view) was not a person on the staff surface, and no
        person confirmed the record on the staff surface at its current version since."""
        evs = [e for e in self.toggle_ev.get(rec['id'], []) if self.at is None or e[0] <= self.at]
        if not evs:
            return False
        _at, _typ, by, diff = evs[-1]
        if str(by or '').startswith(PERSON) and SURFACE in (diff or {}):
            return False
        return not any(c['data'].get('record_id') == rec['id'] and c['data'].get('record_version') == self.version(rec)
                       and str(c['data'].get('confirmed_by') or '').startswith(PERSON) and self.surface_made(c)
                       for c in self.all('crt_confirmation'))

    def wrote(self, rec, field):
        """Who last set or changed this ONE field, read from the activity log; None when the log does
        not explain the field's current value (nobody vouches for it)."""
        last, creator = _ABSENT, None

        def who(by, diff, f):
            # rules 1.4.0 (R1): a person's write vouches for a field only when the store stamped it as made on
            # the platform staff surface; any other (a canvas page, maybe the agent's) is nobody's word
            if str(by or '').startswith(PERSON) and f not in (diff.get(SURFACE) or ()):
                return 'unvouched:' + str(by)
            return by
        for typ, by, diff in self.events.get(rec['id'], []):
            if typ == 'created':
                creator = by if (not str(by or '').startswith(PERSON) or SURFACE in diff) else 'unvouched:' + str(by)
                if field in (diff.get('set') or {}):
                    last = (who(by, diff, field), diff['set'][field])
            elif field in (diff.get('changed') or {}):
                last = (who(by, diff, field), (diff['changed'][field] or [None, None])[-1])
        if last is _ABSENT:
            return creator if field not in rec['data'] else None
        return last[0] if last[1] == rec['data'].get(field) else None

    def file_ok(self, sha):
        """The file is in the private store and its bytes hash to sha (re-hashed here)."""
        if not isinstance(sha, str) or not re.match(r'^[0-9a-f]{64}$', sha):
            return False
        if sha not in self._hash:
            p = os.path.join(self.db_dir, 'files', sha)
            ok = False
            if os.path.isfile(p):
                h = hashlib.sha256()
                with open(p, 'rb') as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b''):
                        h.update(chunk)
                ok = h.hexdigest() == sha
            self._hash[sha] = ok
        return self._hash[sha]

    def doc_sha(self, did):
        d = self.get('document', did) if did else None
        url = (d or {}).get('data', {}).get('file_url') or ''
        return url[len('biz-file:'):] if url.startswith('biz-file:') else None


def _person_field(st, rec, field):
    """THIS field was last written by a person, or a person confirmed it at the record's current version."""
    return str(st.wrote(rec, field) or '').startswith(PERSON) or bool(_person_confs(st, rec, [field], need_file=False))


def _doc_backs(st, did, acct, party=None, kind=None):
    """The document is this account's (and this sub's) paper, of this kind, its identity written or
    confirmed by a person, and its bytes still hash to its id."""
    doc = st.get('document', did) if did else None
    if not doc:
        return False
    d = doc['data']
    if d.get('crt_account_id') != acct['id'] or (party is not None and d.get('contact_id') != party['id']):
        return False
    if kind is not None and d.get('kind') != kind:
        return False
    fields = ['crt_account_id', 'file_url'] + (['contact_id'] if party is not None else []) + \
        (['kind'] if kind is not None else [])
    return all(_person_field(st, doc, f) for f in fields) and st.file_ok(st.doc_sha(did))


def _person_confs(st, rec, fields, need_file=True, acct=None, party=None):
    """Person confirmations of rec at its current version covering every field (file-verified if asked,
    and, when acct is given, read from THIS account's (and sub's) own paper)."""
    out = []
    for c in st.all('crt_confirmation'):
        d = c['data']
        if d.get('record_id') != rec['id'] or d.get('record_version') != st.version(rec):
            continue
        if not str(d.get('confirmed_by') or '').startswith(PERSON) or not str(c['created_by']).startswith(PERSON):
            continue
        if not st.surface_made(c):                 # ruling C (rules 1.5.0): made on the staff surface, by its stamp
            continue
        if not set(fields) <= set(d.get('fields') or []):
            continue
        if need_file:
            sha = st.doc_sha(d.get('source_document_id'))
            if not sha or sha != d.get('source_sha256') or not st.file_ok(sha):
                continue
            if acct is not None and not _doc_backs(st, d.get('source_document_id'), acct, party):
                continue
        out.append(c)
    return out


WAIVABLE_NAME_CODES = ('WRONG_PARTY', 'WRONG_PROJECT', 'BLANKET_UNBACKED')


def _floor(sd):
    """Ruling F (own code): why a set breaks the built-in safety floor; [] when it does not."""
    out = []
    ff = sd.get('form_functions')
    table = {}
    if isinstance(ff, dict):
        for k, v in ff.items():
            if _clash(k, v):                                       # RC7: however the number is written
                out.append(f'table maps {k} to {v}')
            if _refold(k):                                         # RN8
                out.append(f'table key {k!a} is not plain letters and digits')
            table[_form(k)] = v
    for it in sd.get('items') or []:
        if isinstance(it, dict):
            extra = sorted(str(k) for k in it if k not in _ITEM_KEYS)
            if extra:
                out.append(f"item {it.get('id')} carries keys the approval page does not name: {', '.join(extra[:5])}")
            if 'required' in it and not isinstance(it['required'], bool):      # RA (rules 1.7.0)
                out.append(f"item {it.get('id')} required is neither true nor false")
        if isinstance(it, dict) and it.get('kind') == 'limit':
            need, fl = _need(it), it.get('floor')
            if need is None:
                out.append(f"item {it.get('id')} has no minimum and no floor")
            elif _num(it.get('min')) is not None and _num(fl) is not None and it['min'] < fl:
                out.append(f"item {it.get('id')} minimum is below its floor")
            continue
        if not isinstance(it, dict) or it.get('kind') != 'endorsement':
            continue
        fn = it.get('function')
        raw = [f for f in (it.get('forms') or []) if isinstance(f, str) and _form(f)]
        if not raw:
            out.append(f"item {it.get('id')} lists no forms")
        if any(_refold(v) for v in [it.get('edition')] + raw):                     # RN8
            out.append(f"item {it.get('id')} has a form or edition not in plain letters and digits")
        if it.get('edition') and _edition_read(it.get('edition'))[0]:                # RE8 (H8)
            out.append(f"item {it.get('id')} edition field holds a form number")
        if _item_ed(it)[1]:                                                         # RE8 (H5)
            out.append(f"item {it.get('id')}: {_item_ed(it)[1]}")
        for r in raw:
            f = _form(r)
            if _clash(r, fn):                                      # RC7: however the number is written
                out.append(f"item {it.get('id')} ({fn}) lists {r}: {_clash(r, fn)}")
            elif fn == 'ai_completed' and f not in _ISO_FLOOR and table.get(f) != 'ai_completed':
                out.append(f"item {it.get('id')} (completed ops) lists {f}, not a completed-ops form in the table")
    return out


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _need(it):
    """RM: the item's min, or its floor when min is missing or null; None when it has neither."""
    return _num(it.get('min')) if _num(it.get('min')) is not None else _num(it.get('floor'))


def _approval(st, s):
    """The person's staff-surface approval confirmation of set s at its current version, or None."""
    ff = isinstance(s['data'].get('form_functions'), dict) and bool(s['data'].get('form_functions'))
    hits = _person_confs(st, s, ['status', 'items'] + (['form_functions'] if ff else []), need_file=False)
    return hits[-1] if hits else None


def _approved_at(st, sid, version):
    """A person approved set sid on the staff surface at that record version (any time)."""
    for c in st.all_ever('crt_confirmation'):
        d = c['data']
        if d.get('record_id') == sid and d.get('record_version') == version and \
                {'status', 'items'} <= set(d.get('fields') or []) and \
                str(d.get('confirmed_by') or '').startswith(PERSON) and str(c['created_by']).startswith(PERSON) and \
                st.surface_made(c):
            return True
    return False


def _base_ok(st, s):
    """RD (own code): None when the set's approval was compared against an approved version up its own chain (or the
    template / nothing when no ancestor was ever approved); else why not."""
    c = _approval(st, s)
    if c is None:
        return None                                    # R1 already says the set is not approved
    chain, seen, cur = [], {s['id']}, s['data'].get('supersedes')
    while cur and cur not in seen and len(seen) <= 100:
        seen.add(cur)
        prev = st.get_any('crt_requirement_set', cur)
        if prev is None:
            break
        chain.append(prev)
        cur = prev['data'].get('supersedes')
    if not re.match(r'^[0-9a-f]{64}$', str(c['data'].get('approved_diff_sha256') or '')):
        return 'RD the approval names no diff it was shown (X4)'
    ag = c['data'].get('approved_against')
    if not isinstance(ag, dict):
        return 'RD the approval does not record which version it was compared against'
    ever = [p for p in chain if any(_approved_at(st, p['id'], v) for v in range(1, p['version'] + 1))]
    if ag.get('kind') == 'set':
        if ag.get('id') not in {p['id'] for p in chain}:
            return 'RD the approval was compared against a set that is not an earlier version of this one'
        if not _approved_at(st, ag.get('id'), ag.get('record_version')):
            return 'RD the approval was compared against a version nobody approved (a draft)'
        return None
    if ever:
        return 'RD the approval was compared against a template or nothing while an approved earlier version existed'
    if ag.get('kind') == 'template' and ag.get('template_key') != s['data'].get('template_key'):
        return 'RD the approval was compared against another template'
    return None


def _waived(st, asg, code, on, acct, fn=None, did=None):
    """A person-recorded, unexpired waiver of exactly this code, backed by THIS account's written OK; RW (rules
    1.6.0): when the raising paper is given, the waiver must be bound to that function, document and file hash."""
    for w in st.all('crt_waiver'):
        wd = w['data']
        if wd.get('crt_assignment_id') != asg['id'] or wd.get('variance_code') != code:
            continue
        if not str(w['created_by']).startswith(PERSON):
            continue
        if fn is not None:
            sha = st.doc_sha(did) if did else None
            if not did or not sha or not st.file_ok(sha):      # RW/RB (rules 1.7.0): no paper, no waiver
                continue
            if not any(isinstance(b, dict) and b.get('function') == fn and b.get('document_id') == did and
                       b.get('sha256') == sha for b in (wd.get('bound_evidence') or [])):
                continue
        exp = _day(wd.get('expires'))
        doc = st.get('document', wd.get('acceptance_document_id'))
        if exp and on and exp > on and doc and _doc_backs(st, doc['id'], acct, kind='contractor_waiver_ok'):
            return True
    return False


def judge(st, claim):
    """(verdict, [reasons]) for one Meets claim: a stored review, or a live state read from the API.

    claim = {assignment_id, state, on (date), at (ISO time the claim was made), set_id or None,
             confirmation_ids or None, kind: 'review' | 'live'}

    Judged twice: with the records as they were at the claim (anything archived since still counted)
    and as they are now. Backed then but not now is STALE (right when made), never a disagreement.
    """
    then = _judge(st.view(claim['at']), claim)
    if then[0] != PASS:
        return then
    now = _judge(st.view(None), claim)
    if now[0] == PASS:
        return now
    return STALE, ['STALE: backed by the records as they were at the claim; since then: ' + '; '.join(now[1])]


def _judge(st, claim):
    why, cannot = [], []
    on, at = claim['on'], claim['at']
    asg = st.get('crt_assignment', claim['assignment_id'])
    if not asg:
        return FAIL, ['R5 assignment missing']

    def changed_since(rec):
        return rec['updated_at'] > at

    # R6 the assignment (set, version, trade, scope) was written by a person, and points at the set judged
    if changed_since(asg):
        cannot.append('R6 the assignment changed after the claim')
    else:
        agent_fields = [f for f in ASG_FIELDS if not _person_field(st, asg, f)]
        if agent_fields:
            why.append('R6 assignment field(s) not written or confirmed by a person: ' + ', '.join(agent_fields))
        if claim.get('set_id') and claim['set_id'] != asg['data'].get('requirement_set_id'):
            why.append('R6 the verdict used a different requirement set than the assignment names')
    proj = st.get('crt_project', asg['data'].get('crt_project_id'))
    acct = st.get('contact', (proj or {}).get('data', {}).get('crt_account_id'))
    party = st.get('contact', asg['data'].get('party_contact_id'))
    if not (proj and acct and party):
        return FAIL, ['R5 project, account or sub missing']
    if not any(ln['data'].get('crt_account_id') == acct['id'] and ln['data'].get('party_contact_id') == party['id']
               and ln['data'].get('status', 'active') == 'active'
               and all(_person_field(st, ln, f) for f in ('crt_account_id', 'party_contact_id', 'status'))
               for ln in st.all('crt_account_party')):
        why.append('R5 sub not linked to the account by a person')
    for rec, fields, what in ((acct, ('name', 'company_name'), 'account name'),
                              (party, ('name', 'company_name'), 'sub name'),
                              (proj, PROJECT_FIELDS, 'project')):
        bad = [f for f in fields if not _person_field(st, rec, f)]
        if bad:
            (cannot if changed_since(rec) else why).append(
                f'R5 {what} field(s) not written or confirmed by a person: ' + ', '.join(bad))

    s = st.get('crt_requirement_set', claim.get('set_id') or asg['data'].get('requirement_set_id'))
    if not s:
        return FAIL, ['R1 requirement set missing']
    set_ff = s['data'].get('form_functions') if isinstance(s['data'].get('form_functions'), dict) else None
    if s['data'].get('status') != 'APPROVED' or not _person_confs(st, s, ['status'] + (['form_functions'] if set_ff else []),
                                                                  need_file=False):
        (cannot if changed_since(s) else why).append('R1 set not approved by a person')
    if not changed_since(s) and not changed_since(asg) and asg['data'].get('requirement_set_version') != s['version']:
        why.append('R6 the assignment did not pin this version of the set')
    floor = _floor(s['data'])
    if floor:
        why.append('RF the set is below the built-in safety floor: ' + '; '.join(floor[:3]))
    rd = _base_ok(st, s)
    if rd:
        (cannot if changed_since(s) else why).append(rd)
    # RC the agency's own record: live, with a name a person wrote on the staff surface (or confirmed there)
    comps = st.all('company')
    company = comps[0] if comps else None
    cname = (company or {}).get('data', {}).get('name')
    company_ok = bool(company and cname and _person_field(st, company, 'name'))
    if not company_ok:
        why.append('RC the agency record is missing, archived, or its name is not a person\'s staff-surface word')
    for cid in claim.get('confirmation_ids') or []:
        c = st.get('crt_confirmation', cid)
        if not c or not str(c['data'].get('confirmed_by') or '').startswith(PERSON) \
                or not str(c['created_by']).startswith(PERSON):
            why.append('R2 the review read a confirmation that is missing or not made by a person')
            break
    acct_name = acct['data'].get('company_name') or acct['data'].get('name')
    pname = proj['data'].get('name')
    sc = _day(proj['data'].get('substantial_completion'))
    repose = proj['data'].get('repose_years')
    policies = [p for p in st.all('crt_policy') if p['data'].get('party_contact_id') == party['id']
                and p['data'].get('crt_account_id') == acct['id']]
    party_name = party['data'].get('company_name') or party['data'].get('name')

    # RP every record the claim relies on: its last archive / unarchive was a person's, on the staff surface
    relied = [('crt_assignment', asg), ('crt_project', proj), ('contact', acct), ('contact', party),
              ('crt_requirement_set', s)] + ([('company', company)] if company else [])
    every_pol = [p for p in st.all_ever('crt_policy') if p['data'].get('party_contact_id') == party['id']
                 and p['data'].get('crt_account_id') == acct['id']]
    relied += [('crt_policy', p) for p in every_pol]
    relied += [('crt_endorsement', e) for e in st.all_ever('crt_endorsement')
               if e['data'].get('crt_policy_id') in {p['id'] for p in every_pol}]
    relied += [('crt_account_party', ln) for ln in st.all_ever('crt_account_party')
               if ln['data'].get('crt_account_id') == acct['id'] and ln['data'].get('party_contact_id') == party['id']]
    dids = {proj['data'].get('contract_document_id')}
    for coll, r in list(relied):
        if coll == 'crt_policy':
            dids.add(r['data'].get('source_document_id'))
        if coll == 'crt_endorsement':
            dids.add(r['data'].get('document_id'))
    relied += [('document', d) for d in (st.get_any('document', x) for x in dids if x) if d]
    for coll, r in relied:
        if st.toggle_unvouched(r):
            why.append(f'RP a {coll} the claim relies on was archived or unarchived off the staff surface, and no '
                       'person vouched for it again')
    # RE every active link for this pair was written or confirmed by a person (an extra unvouched link is not backed)
    for ln in st.all('crt_account_party'):
        if ln['data'].get('crt_account_id') == acct['id'] and ln['data'].get('party_contact_id') == party['id'] \
                and ln['data'].get('status', 'active') == 'active' and \
                not all(_person_field(st, ln, f) for f in ('crt_account_id', 'party_contact_id', 'status')):
            why.append('RE an active link of this sub to the account was not written or confirmed by a person')
    rc_why = {}

    def agency_people(p):
        """How many different people must have confirmed this policy (ruling C), or None when nobody on staff
        said whether we are its producer."""
        flag = p['data'].get('producer_is_cca')
        if not isinstance(flag, bool) or not str(st.wrote(p, 'producer_is_cca') or '').startswith(PERSON):
            return None
        if not company_ok:
            return 2
        return 2 if (flag or _named(cname, [p['data'].get('producer_name')])) else 1

    def people(p, fields):
        return {c['data'].get('confirmed_by') for c in _person_confs(st, p, fields, acct=acct, party=party)}

    def in_force(line):
        """(person-confirmed, file-backed policies on this line in force on the claim date, any changed since)"""
        out, changed = [], False
        for p in policies:
            if p['data'].get('line') != line:
                continue
            if changed_since(p):
                changed = True
                continue
            eff, exp = _day(p['data'].get('effective')), _day(p['data'].get('expiration'))
            if not (eff and exp and on and eff <= on < exp):
                continue
            need = agency_people(p)
            if need is None:
                rc_why[line] = f'RC nobody on staff said (on the staff surface) whether we produce the {line} policy'
                continue
            if len(people(p, ['line', 'effective', 'expiration', 'source_document_id'])) >= need:
                out.append(p)
            elif need == 2:
                rc_why[line] = f'RC the {line} policy is the agency\'s own: two different people must confirm it'
        return out, changed

    def confirmed(p, field):
        need = agency_people(p) or 2
        return len(people(p, [field])) >= need

    num = _num

    for it in s['data'].get('items') or []:
        kind, line, fn = it.get('kind'), it.get('line'), it.get('function')
        if it.get('required') is False:
            continue
        if kind in ('limit', 'ai'):
            if kind == 'ai' and line == 'pl':
                continue
            pols, changed = in_force(line)
            if not pols:
                (cannot if changed else why).append(f'R3 no person-confirmed, file-backed {line} policy in force')
                if line in rc_why:
                    why.append(rc_why[line])
                continue
            misses = []
            for p in pols:
                pd = p['data']
                miss = None
                # never waivable: the certificate is another company's (host ruling N10, H2 suffix kept)
                if not confirmed(p, 'named_insured') or _norm(party_name) == '' or \
                        _norm(pd.get('named_insured')) != _norm(party_name):
                    miss = f'R3 the named insured on the {line} policy is not this sub (same legal name, entity suffix ' \
                           'included) or not person-confirmed'
                elif kind == 'limit':
                    val, need = num((pd.get('limits') or {}).get(it.get('limit'))), _need(it)   # RM: never $0
                    ok = need is not None and confirmed(p, 'limits') and val is not None and val >= need
                    comb = it.get('combined') if isinstance(it.get('combined'), dict) else {}
                    if not ok and need is not None and comb and line == 'gl' and confirmed(p, 'limits') and \
                            val is not None and num(comb.get('gl_occ_floor')) is not None and \
                            num(comb.get('gl_occ_plus_umbrella_min')) is not None and val >= comb['gl_occ_floor']:
                        for u in in_force('umbrella')[0]:
                            uv = num((u['data'].get('limits') or {}).get('each_occurrence'))
                            # RU: the umbrella the combined test leans on names this sub, person-confirmed
                            if confirmed(u, 'limits') and uv is not None and confirmed(u, 'named_insured') and \
                                    _norm(party_name) != '' and _norm(u['data'].get('named_insured')) == _norm(party_name) \
                                    and val + uv >= comb['gl_occ_plus_umbrella_min']:
                                ok = True
                    if not ok and not _waived(st, asg, f'LIMIT_LOW:{line}', on, acct, it.get('id'),
                                              pd.get('source_document_id')):
                        miss = f'R3 the person-confirmed {line} {it.get("limit")} limit is below {need}'
                elif not (confirmed(p, 'addl_insd') and pd.get('addl_insd') is True) and \
                        not _waived(st, asg, f'NOT_AI:{line}', on, acct, it.get('id'), pd.get('source_document_id')):
                    miss = f'R3 the {line} policy does not show additional insured (person-confirmed)'
                if miss is None:
                    break
                misses.append(miss)
            else:
                why.extend(misses[:1])
        elif kind == 'disallowed_exclusion':
            code = it.get('code')
            if it.get('when_trade') is not None:
                why.append('R3x the set still carries the retired word rule for trades (when_trade)')
                continue
            cats = it.get('trade_categories')
            if cats:
                trade = _trade(asg['data'].get('trade_category'))
                if trade is None:
                    why.append('R3x the trade is not one of the listed trade categories, so nobody can say '
                               f'whether the {code} item applies')
                    continue
                if trade not in cats:
                    continue
            banned = set(it.get('classes') or _EXCL_FAMILY.get(code) or ())
            pols, changed = in_force(line)
            good = False
            for p in pols:
                cls, unmapped = _excl(p['data'].get('exclusions'))
                if not confirmed(p, 'exclusions') or unmapped or not cls or ('none' in cls and len(cls) > 1):
                    continue
                if cls & banned and not _waived(st, asg, f'EXCLUSION:{code}', on, acct, it.get('id'),
                                                p['data'].get('source_document_id')):
                    continue
                good = True
                break
            if not good:
                (cannot if changed and not pols else why).append(
                    f'R3x no {line} policy whose person-confirmed exclusions are classes free of {code} '
                    '("none" confirmed by a person, never a blank list or unmapped wording)')
        elif kind == 'endorsement' and (fn in ('ai_ongoing', 'ai_completed') or it.get('names_parties')):
            if fn == 'ai_ongoing' and sc and on and on > sc:
                continue
            if fn == 'ai_completed' and sc and on and on > sc and isinstance(repose, int) and on >= _add_years(sc, repose):
                continue
            good = changed = False
            # every certificate file of this sub for this account: never the endorsement (host ruling N2)
            cert_shas = {st.doc_sha(d['id']) for d in st.all_ever('document') if d['data'].get('kind') == 'coi_acord25'
                         and d['data'].get('crt_account_id') == acct['id'] and d['data'].get('contact_id') == party['id']}
            cert_shas |= {st.doc_sha(p['data'].get('source_document_id')) for p in policies}
            for e in st.all('crt_endorsement'):
                ed = e['data']
                pol = st.get('crt_policy', ed.get('crt_policy_id'))
                if ed.get('function') != fn or ed.get('crt_account_id') != acct['id'] or not pol \
                        or pol['id'] not in {p['id'] for p in policies} or pol['data'].get('line') != (line or 'gl'):
                    continue
                if changed_since(e) or changed_since(proj):
                    changed = True
                    continue
                doc = st.get('document', ed.get('document_id'))
                # the endorsement's own document: kind endorsement (a person said so), this sub's, not a
                # certificate's file, bytes verified
                if not doc or not _doc_backs(st, doc['id'], acct, party, kind='endorsement') or \
                        doc['id'] == pol['data'].get('source_document_id') or st.doc_sha(doc['id']) in cert_shas:
                    continue
                e_people = {c['data'].get('confirmed_by') for c in _person_confs(
                    st, e, ['function', 'document_id', 'scheduled_names', 'scheduled_projects', 'blanket',
                            'crt_policy_id', 'form', 'edition', 'manuscript'], acct=acct, party=party)}
                if len(e_people) < (agency_people(pol) or 2):        # RC: the agency's own paper needs two people
                    continue
                # the form number: one the item lists, a manuscript excepted. R3 (rules 1.4.0): CG 20 10 is never
                # completed operations by any route; a listed ISO number is never read as manuscript; a manuscript
                # counts for completed ops only with its completed-operations wording person-confirmed.
                fkey, form_my = _form_parts(ed.get('form'))      # RC7: however the number is written
                if fn == 'ai_completed' and _never_completed(ed.get('form')):     # RG
                    continue
                if _refold(ed.get('form')) or _refold(ed.get('edition')):         # RN8: look-alikes are not backed
                    continue
                table = {_form(k): v for k, v in set_ff.items()} if set_ff else {}
                table.update(_ISO_FLOOR)                       # ruling F: an ISO number keeps its ISO category
                iso = fkey in _ISO_FLOOR or fkey in _NOT_COMPLETED or bool(_ISO_AI.match(fkey or '')) or \
                    bool(_iso_in(ed.get('form'))) or _near_iso(ed.get('form'))    # RN8: near an ISO number too
                if ed.get('manuscript') is not True or fkey in table or iso:        # RG: an ISO number is no manuscript
                    listed = {_form(f) for f in it.get('forms') or []}        # ruling F: the item's own list only
                    if fkey not in listed:
                        continue
                elif fn == 'ai_completed' and not (ed.get('completed_ops_wording') is True and
                                                   _person_confs(st, e, ['completed_ops_wording'], acct=acct,
                                                                 party=party)):
                    continue
                year = _edition(ed.get('edition'))
                edoc = ed.get('document_id')
                if year is None or (year < 2004 and not _waived(st, asg, f'WRONG_FORM:{fn}', on, acct, fn, edoc)):
                    continue
                if form_my is not None and form_my != _edition_my(ed.get('edition')):
                    continue                                   # RC7: the number's own edition disagrees with the field
                if _edition_read(ed.get('edition'))[0] not in (None, fkey):
                    continue                                   # RG8: the edition field names another form number
                # the set's edition for the item is a requirement (rules 1.4.0 LOW): same month and year; RE8: an
                # edition written after the number in the item's form list is that requirement
                want_my = _item_ed(it)[0]
                if want_my and want_my != _edition_my(ed.get('edition')) and \
                        not _waived(st, asg, f'WRONG_EDITION:{fn}', on, acct, fn, edoc):
                    continue
                # RE (rules 1.5.0): every naming check runs; each miss needs its OWN waiver
                name_codes = []
                # RK (rules 1.6.0): blanket only when the schedule is empty
                if ed.get('blanket') and not (ed.get('scheduled_names') or ed.get('scheduled_projects')):
                    backed = proj['data'].get('contract_requires_ai') is True and \
                        bool(_person_confs(st, proj, ['contract_requires_ai']))
                    if not backed:
                        name_codes.append('BLANKET_UNBACKED')
                else:
                    if not _person_confs(st, proj, ['contract_ai_parties'], acct=acct):
                        continue                               # the contract's AI list nobody confirmed: not backed
                    if not _named_exact(acct_name, ed.get('scheduled_names')) or \
                            not all(_named_exact(n, ed.get('scheduled_names'))
                                    for n in proj['data'].get('contract_ai_parties') or []):
                        name_codes.append('WRONG_PARTY')
                    if not _named_exact(pname, ed.get('scheduled_projects')):
                        name_codes.append('WRONG_PROJECT')
                if all(_waived(st, asg, c, on, acct, fn, edoc) for c in name_codes):
                    good = True
            if not good and fn == 'ai_completed' and sc and on and on > sc and isinstance(repose, int) and \
                    not isinstance(repose, bool):
                # RB (rules 1.7.0): after substantial completion, a lapsed completed-ops endorsement the contractor
                # accepted in writing counts only for the term (certificate) it was accepted on, and only while that
                # policy carries no completed-ops endorsement at all (otherwise its own checks decide)
                for p in in_force(line or 'gl')[0]:
                    if not any(e['data'].get('function') == fn and e['data'].get('crt_policy_id') == p['id']
                               for e in st.all('crt_endorsement')) and \
                            _waived(st, asg, 'COMPLETED_OPS_LAPSED', on, acct, fn, p['data'].get('source_document_id')):
                        good = True
                        break
            if not good:
                (cannot if changed else why).append(
                    f'R4 no person-confirmed {fn} endorsement document naming this account and project '
                    '(or a contractor-accepted waiver of the naming)')
            else:
                # RE: an endorsement for this coverage on this sub's in-force policy that nobody confirmed means the
                # evidence is not fully checked (rules 1.5.0: Can't tell is never hidden behind another paper)
                live_pols = {p['id'] for p in in_force(line or 'gl')[0]}
                for e in st.all('crt_endorsement'):
                    if e['data'].get('function') == fn and e['data'].get('crt_account_id') == acct['id'] and \
                            e['data'].get('crt_policy_id') in live_pols and not changed_since(e) and \
                            not _person_confs(st, e, ['function', 'document_id', 'crt_policy_id', 'form', 'edition'],
                                              acct=acct, party=party):
                        why.append(f'RE a {fn} endorsement on this sub\'s policy was never confirmed by a person')
                        break
    if why:
        return FAIL, sorted(set(why))
    if cannot:
        return CANNOT, sorted(set(cannot)) + ['(a record changed after the claim; old version not visible)']
    return PASS, []


def fetch_live_claims(url, key, sandbox=None, timeout=20):
    """Live Meets claims from the API (GET /crt_assignment, then /crt/assignment/<id>/state). Read only."""
    import urllib.request
    hdr = {'X-Agent-Key': key, 'Accept': 'application/json'}
    if sandbox:
        hdr['X-Biz-Sandbox'] = sandbox

    def get(path):
        with urllib.request.urlopen(urllib.request.Request(url.rstrip('/') + path, headers=hdr), timeout=timeout) as r:
            return json.loads(r.read())
    out, cursor = [], None
    while True:
        page = get('/api/biz/v1/crt_assignment?limit=500' + (f'&cursor={cursor}' if cursor else ''))
        for a in page['records']:
            ev = get(f'/api/biz/v1/crt/assignment/{a["id"]}/state')
            out.append({'assignment_id': a['id'], 'state': ev['state'], 'as_of': ev['as_of'],
                        'set_id': ev.get('requirement_set_id')})
        cursor = page.get('next_cursor')
        if not cursor:
            return out


def watch(db_dir, live=None):
    """Judge every latest stored Meets review, plus every live Meets claim given (list of state dicts)."""
    try:
        st = Store(db_dir)
    except (OSError, sqlite3.Error) as e:
        return CANNOT, [], f'store unreadable: {type(e).__name__}'
    latest = {}
    for r in st.all('crt_review'):
        aid = r['data'].get('crt_assignment_id')
        key = r['data'].get('reviewed_at') or r['updated_at']
        if aid not in latest or key > (latest[aid]['data'].get('reviewed_at') or latest[aid]['updated_at']):
            latest[aid] = r
    claims = []
    for aid, r in sorted(latest.items(), key=lambda kv: kv[0] or ''):
        if r['data'].get('state') in MEETS_STATES:
            at = r['data'].get('reviewed_at') or r['updated_at']
            claims.append({'kind': 'review', 'review_id': r['id'], 'assignment_id': aid, 'state': r['data']['state'],
                           'on': _day(at), 'at': at, 'set_id': r['data'].get('requirement_set_id'),
                           'confirmation_ids': r['data'].get('confirmation_ids')})
    for c in live or []:
        if c.get('state') in MEETS_STATES:
            claims.append({'kind': 'live', 'assignment_id': c['assignment_id'], 'state': c['state'],
                           'on': _day(c.get('as_of')), 'at': '9999', 'set_id': c.get('set_id'),
                           'confirmation_ids': None})
    rows = []
    for c in claims:
        v, reasons = judge(st, c)
        rows.append({'kind': c['kind'], 'assignment_id': c['assignment_id'], 'review_id': c.get('review_id'),
                     'stored_state': c['state'], 'watch': v, 'reasons': reasons})
    st.conn.close()
    vs = [x['watch'] for x in rows]
    if FAIL in vs:
        return FAIL, rows, f'{vs.count(FAIL)} Meets verdict(s) NOT backed by confirmed inputs'
    if not rows:
        return CANNOT, rows, 'no Meets verdicts to check (silence is not health)'
    if CANNOT in vs or STALE in vs:
        return CANNOT, rows, (f'{vs.count(CANNOT)} claim(s) could not be judged, {vs.count(STALE)} stale review(s) '
                              '(right when made, an input archived since: re-review)')
    return PASS, rows, f'{len(rows)} Meets verdict(s) backed by person-confirmed, file-verified inputs'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Independent watcher for risk-transfer Meets verdicts (advisory)')
    ap.add_argument('--db-dir', required=True, help="the store's db dir (holds business.db and files/)")
    ap.add_argument('--live-url', help='also judge live states: GET them from this server (key from env BIZ_WATCH_KEY)')
    ap.add_argument('--sandbox', help='dev sandbox name for --live-url (X-Biz-Sandbox)')
    ap.add_argument('--live-json', help='also judge live states from this file ([{assignment_id, state, as_of, set_id}])')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    live = None
    if a.live_json:
        live = json.load(open(a.live_json))
    elif a.live_url:
        key = os.environ.get('BIZ_WATCH_KEY', '').strip()
        if not key:
            print('crt-verdict-watch: CANNOT-TELL - no BIZ_WATCH_KEY in env for --live-url')
            return EXIT[CANNOT]
        try:
            live = fetch_live_claims(a.live_url, key, a.sandbox)
        except Exception as e:
            print(f'crt-verdict-watch: CANNOT-TELL - live states unreadable ({type(e).__name__})')
            return EXIT[CANNOT]
    verdict, rows, summary = watch(a.db_dir, live)
    if a.json:
        print(json.dumps({'verdict': verdict, 'summary': summary, 'checked': rows,
                          'advisory': '[probation] NEWBORN watcher: reports only; changes nothing'}, indent=1))
    else:
        for x in rows:
            if x['watch'] != PASS:
                print(f"{'DISAGREE' if x['watch'] == FAIL else x['watch']}: {x['kind']} '{x['stored_state']}' "
                      f"for assignment {x['assignment_id']} - " + '; '.join(x['reasons']))
        print(f'crt-verdict-watch: {verdict} - {summary} [probation, advisory only]')
    return EXIT[verdict]


if __name__ == '__main__':
    sys.exit(main())
