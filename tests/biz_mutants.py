"""Negative controls for the biz backend's staff_only / file store / packs core and pack:risk-transfer.

    python3 tests/biz_mutants.py            (inside the OVU image, repo root as cwd)

Each mutant is a deliberately broken copy of the code (one edit). The named tests MUST fail on
it; a mutant the tests do not catch is a test that never proved anything. A control run of the
same tests on an unbroken copy must pass, so a "caught" is not just a broken harness.
Copies live under /tmp/biz-mutants/<name>; the repo itself is never edited.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.environ.get('BIZ_MUTANTS_DIR', '/tmp/biz-mutants')
RT = 'services/biz/packs/risk_transfer.py'
ST = 'services/biz/store.py'
RB = 'routes/biz.py'
SC = 'services/biz/schema.py'
T_CRT = 'tests/test_biz_crt_pack.py::'
T_SO = 'tests/test_biz_staff_only.py::'
T_F = 'tests/test_biz_files.py::'
T_P = 'tests/test_biz_packs.py::'
T_T = 'tests/test_biz_crt_tools.py::'
PR = 'tools/biz/isolation-probe.py'
WA = 'tools/biz/crt-verdict-watch.py'

# (name, [(file, old, new)], [tests that must fail])
MUTANTS = [
    # ── shared backend: staff_only ──
    ('staff-only-collection-off', [(ST, "        if spec.get('staff_only'):\n            raise _err(403, 'staff_only'",
                                    "        if False and spec.get('staff_only'):\n            raise _err(403, 'staff_only'")],
     [T_SO + 'test_agent_cannot_create_a_staff_only_record', T_CRT + 'test_tb2b_agent_cannot_create_a_confirmation_through_the_generic_route',
      T_CRT + 'test_tb2c_agent_cannot_create_a_waiver', T_CRT + 'test_tb2d_agent_cannot_create_a_review']),
    ('staff-only-field-off', [(ST, "        hit = sorted(k for k in keys if (fields.get(k) or {}).get('staff_only'))",
                               "        hit = []")],
     [T_SO + 'test_agent_writes_the_other_fields_but_not_hold', T_CRT + 'test_tb2e_agent_cannot_approve_a_requirement_set',
      T_CRT + 'test_tb2f_agent_cannot_set_or_clear_a_hold']),
    ('staff-only-archive-off', [(ST, "        self._staff_guard(spec, collection, agent=agent, op='archive' if archived else 'unarchive')\n",
                                 "\n")],
     [T_SO + 'test_agent_cannot_edit_archive_or_unarchive_a_staff_only_record',
      T_CRT + 'test_tb2h_agent_cannot_archive_or_unarchive_evidence_of_a_verdict']),
    # ── shared backend: file store ──
    ('file-agent-may-open', [(RB, "    if _agent():\n        raise SchemaError(403, 'owner_only', 'Stored files",
                              "    if False:\n        raise SchemaError(403, 'owner_only', 'Stored files")],
     [T_F + 'test_agent_stores_with_source_but_cannot_open', T_CRT + 'test_tb1_files_are_private']),
    ('file-type-from-claim', [(ST, "        for magic, ctype, ext in FILE_TYPES:\n            if blob.startswith(magic):",
                               "        for magic, ctype, ext in FILE_TYPES:\n            if True:")],
     [T_F + 'test_type_is_decided_from_the_bytes']),
    ('file-store-under-uploads', [(ST, "        self.files_dir = self.db_dir / 'files'",
                                   "        self.files_dir = self.db_dir.parent / 'uploads' / 'crt'")],
     [T_F + 'test_owner_stores_and_reads_back_as_attachment']),
    ('file-tampered-served', [(ST, "        if not path.is_file() or self._sha_of(path) != fid:\n            logger.error",
                               "        if not path.is_file():\n            logger.error")],
     [T_F + 'test_tampered_bytes_are_not_served']),
    ('file-size-uncapped', [(ST, "        if len(blob) > FILE_MAX_BYTES:\n            raise _err(413",
                             "        if False:\n            raise _err(413")],
     [T_F + 'test_size_cap']),
    ('file-id-read-as-card-number', [(SC, "        value = _SHA256_TOKEN_RE.sub(' ', _UUID_TOKEN_RE.sub(' ', value))",
                                      "        value = _UUID_TOKEN_RE.sub(' ', value)")],
     [T_F + 'test_a_file_id_is_never_mistaken_for_a_card_number',
      T_F + 'test_document_pointing_at_a_luhn_looking_file_id_is_accepted']),
    # ── shared backend: packs ──
    ('pack-always-on', [(SC, "        for pname in self.enabled_packs:\n            _packs.merge_into",
                         "        for pname in self.available_packs:\n            _packs.merge_into")],
     [T_P + 'test_a_pack_is_invisible_until_turned_on', T_CRT + 'test_pack_routes_404_until_the_pack_is_on']),
    ('pack-hooks-skip-edit', [(ST, "            self._pack_hooks('edit', collection, after, current, by=by, agent=agent, via_pack=via_pack)",
                               "            pass")],
     [T_P + 'test_check_write_runs_on_every_verb_and_can_refuse',
      T_CRT + 'test_a_used_set_is_frozen_and_revising_makes_a_new_version']),
    ('pack-route-ignores-enable', [(RB, "        if not g.biz_store.pack_enabled(name):\n            raise SchemaError(404, 'pack_not_enabled'",
                                    "        if False:\n            raise SchemaError(404, 'pack_not_enabled'")],
     [T_P + 'test_pack_routes_404_until_the_pack_is_on', T_CRT + 'test_pack_routes_404_until_the_pack_is_on']),
    ('pack-loader-accepts-unprefixed', [('services/biz/packs/__init__.py',
                                         "        if not isinstance(coll, str) or not coll.startswith(prefix + '_') or coll in core_names:",
                                         "        if not isinstance(coll, str) or coll in core_names:")],
     [T_P + 'test_only_rule_abiding_packs_are_offered']),
    # ── pack: confirmed-only verdict (B2) ──
    ('verdict-ignores-version', [(RT, "        if d.get('record_collection') != collection or d.get('record_version') != rec['version']:",
                                  "        if d.get('record_collection') != collection:")],
     [T_CRT + 'test_tb2_agent_edit_after_confirmation_makes_it_stale', T_CRT + 'test_editing_an_approved_set_needs_a_new_approval']),
    ('verdict-ignores-hash', [(RT, "            if not fid or fid != d.get('source_sha256') or not self.i['files_ok'].get(fid):",
                               "            if not fid or not self.i['files_ok'].get(fid):")],
     [T_CRT + 'test_tb2_confirmation_hash_must_match_the_stored_file',
      T_CRT + 'test_tb2_confirmation_written_with_a_wrong_hash_does_not_count']),
    ('verdict-trusts-agent-setup', [(RT, "        if person_ok and not str(rec.get('updated_by') or '').startswith('agent:'):",
                                     "        if person_ok:")],
     [T_CRT + 'test_an_agent_edit_to_the_account_name_needs_staff_before_it_counts']),
    ('verdict-ignores-unconfirmed', [(RT, "    if unconfirmed:\n        state = 'cant_tell'", "    if False:\n        state = 'cant_tell'")],
     [T_CRT + 'test_tb2_agent_written_clean_policy_is_unconfirmed', T_CRT + 'test_draft_set_is_cant_tell']),
    ('verdict-one-confirmer-for-cca', [(RT, "        if not hits or (two and len(people) < 2):", "        if not hits:")],
     [T_CRT + 'test_cca_as_producer_needs_a_second_different_staff_confirmer',
      T_CRT + 'test_producer_named_like_our_agency_counts_as_us_even_unflagged']),
    ('verdict-draft-set-counts', [(RT, "        if not approved:\n            unc('crt_requirement_set', 'status')",
                                   "        if False:\n            unc('crt_requirement_set', 'status')")],
     [T_CRT + 'test_draft_set_is_cant_tell']),
    ('agent-may-confirm', [(RT, "        ctx.require_person('confirm a reading')\n", "\n")],
     [T_CRT + 'test_tb2a_agent_cannot_confirm']),
    ('generic-confirmation-allowed', [(RT, "        if op == 'create' and not via_pack:\n            raise _err(409, 'use_pack_route'",
                                       "        if False:\n            raise _err(409, 'use_pack_route'")],
     [T_CRT + 'test_tb2b_agent_cannot_create_a_confirmation_through_the_generic_route',
      T_CRT + 'test_tb2d_agent_cannot_create_a_review']),
    # ── pack: right party / right project (B3) ──
    ('no-wrong-party', [(RT, "                        if not all(names_match(w, sched) for w in wanted):",
                         "                        if False:")],
     [T_CRT + 'test_tb3a_endorsement_naming_another_gc_is_wrong_party',
      T_CRT + 'test_tb3a_endorsement_missing_the_owner_the_contract_requires_is_wrong_party']),
    ('no-wrong-project', [(RT, "                        if not names_match(pname, e['data'].get('scheduled_projects') or []):",
                           "                        if False:")],
     [T_CRT + 'test_tb3b_endorsement_scheduled_to_another_project_is_wrong_project']),
    ('blanket-without-tick', [(RT, "                        if not cr_ok:\n                            unc('crt_project', 'contract_requires_ai')",
                               "                        if False:\n                            unc('crt_project', 'contract_requires_ai')")],
     [T_CRT + 'test_tb3c_blanket_without_a_confirmed_contract_tick_is_cant_tell']),
    ('blanket-unbacked-ok', [(RT, "                        if cr is not True:\n                            item_codes.append('BLANKET_UNBACKED')",
                              "                        if False:\n                            item_codes.append('BLANKET_UNBACKED')")],
     [T_CRT + 'test_tb3c_blanket_when_the_contract_does_not_require_ai_is_unbacked']),
    ('wrong-insured-off', [(RT, "        if party_name and not names_match(party_name, [pol['data'].get('named_insured')]):",
                            "        if False:")],
     [T_CRT + 'test_certificate_for_another_insured']),
    ('partial-name-match', [(RT, "        if n == w or f' {w} ' in f' {n} ':", "        if n == w or w[:4] in n:")],
     [T_CRT + 'test_certificate_for_another_insured']),
    # ── pack: other variance codes ──
    ('no-not-ai', [(RT, "            if pol['data'].get('addl_insd') is True:", "            if True:")],
     [T_CRT + 'test_certificate_holder_only_is_not_ai']),
    ('holder-any', [(RT, "            elif acct_name and names_match(acct_name, [pol['data'].get('certificate_holder')]):",
                     "            elif True:")],
     [T_CRT + 'test_pl_certificate_holder_must_be_the_account']),
    ('no-tail-check', [(RT, "            elif pol['data'].get('occurrence_or_claims_made') == 'claims_made' and not pol['data'].get('tail'):",
                        "            elif False:")],
     [T_CRT + 'test_pl_claims_made_without_tail']),
    ('limits-always-ok', [(RT, "            ok = isinstance(val, (int, float)) and not isinstance(val, bool) and val >= need",
                           "            ok = True")],
     [T_CRT + 'test_limit_low']),
    ('combined-too-lenient', [(RT, "                    if u_exp and u_exp > today and val + uv >= comb.get('gl_occ_plus_umbrella_min', 0):",
                               "                    if u_exp and u_exp > today:")],
     [T_CRT + 'test_high_severity_combined_gl_plus_umbrella']),
    ('no-exclusions', [(RT, "            if any(it.get('code') in h for h in have):", "            if False:")],
     [T_CRT + 'test_high_severity_xcu_exclusion_on_excavation', T_CRT + 'test_envelope_water_exclusion']),
    ('no-producer-check', [(RT, "            if not pdd.get('producer_name') or not (pdd.get('producer_phone') or pdd.get('producer_email')):",
                            "            if False:")],
     [T_CRT + 'test_producer_contact_incomplete']),
    ('endorsement-doc-optional', [(RT, "                if not conf.doc_ok(e['data'].get('document_id')):\n                    item_codes.append(f'MISSING:{fn}')",
                                   "                if False:\n                    item_codes.append(f'MISSING:{fn}')")],
     [T_CRT + 'test_a_certificate_alone_never_meets_an_endorsement_requirement']),
    ('no-scope-change', [(RT, "        if lr and sca > lr:\n            variances.append('SCOPE_CHANGED')", "        if False:\n            variances.append('SCOPE_CHANGED')")],
     [T_CRT + 'test_scope_change_after_the_last_review']),
    ('no-expiry', [(RT, "            if days <= 0:\n                expired.append", "            if days < -100000:\n                expired.append")],
     [T_CRT + 'test_expired_policy']),
    ('no-completed-ops-lapse', [(RT, "                code = 'COMPLETED_OPS_LAPSED' if lapsed_rule else f'MISSING:{fn}'",
                                 "                code = f'MISSING:{fn}'")],
     [T_CRT + 'test_completed_ops_lapsed_on_a_renewal_after_close_inside_repose']),
    ('waiver-never-expires', [(RT, "        if w['archived_at'] or not exp_w or exp_w <= today or not conf.doc_ok(wd.get('acceptance_document_id')):",
                               "        if w['archived_at'] or not exp_w or not conf.doc_ok(wd.get('acceptance_document_id')):")],
     [T_CRT + 'test_waiver_unexpired_accepts_and_expired_returns_the_variance']),
    ('hold-not-on-stale', [(RT, "    hold_suggested = state in UNMET_STATES or stale", "    hold_suggested = state in UNMET_STATES")],
     [T_CRT + 'test_positive_fully_confirmed_mep_meets_requirements', T_CRT + 'test_review_older_than_30_days_suggests_a_hold']),
    # ── pack: requirement sets, projects, scope ──
    ('used-set-editable', [(RT, "        if op == 'edit' and not via_pack and _used(store, current['id']):", "        if False:")],
     [T_CRT + 'test_a_used_set_is_frozen_and_revising_makes_a_new_version']),
    ('ai-on-wc-allowed', [(RT, "        if is_ai and line == 'wc':", "        if False:")],
     [T_CRT + 'test_requirement_set_traps_are_refused']),
    ('pre-2004-allowed', [(RT, "            if yr is not None and yr < 2004:", "            if False:")],
     [T_CRT + 'test_requirement_set_traps_are_refused']),
    ('ani-allowed', [(RT, "        if _ANI_RE.search(text) or it.get('function') == 'additional_named_insured':", "        if False:")],
     [T_CRT + 'test_requirement_set_traps_are_refused']),
    ('repose-source-optional', [(RT, "        if data.get('repose_years') is not None and not str(data.get('repose_source') or '').strip():",
                                 "        if False:")],
     [T_CRT + 'test_repose_years_need_their_source']),
    ('assign-unlinked-sub', [(RT, "        if not _active_link(store, project['data'].get('crt_account_id'), data.get('party_contact_id')):",
                              "        if False:")],
     [T_CRT + 'test_a_sub_must_be_linked_to_the_account_to_be_assigned']),
    ('uploads-doc-allowed', [(RT, "            raise _err(422, 'file_must_be_private'", "            pass  # raise _err(422, 'file_must_be_private'")],
     [T_CRT + 'test_tb1_pack_documents_cannot_point_at_uploads']),
    ('verdict-reads-other-accounts', [(RT, "        inputs['policies'] = _all(store, 'crt_policy', {'party_contact_id': party_id, 'crt_account_id': account_id})",
                                       "        inputs['policies'] = _all(store, 'crt_policy', {'party_contact_id': party_id})")],
     [T_CRT + 'test_one_sub_two_accounts_each_sees_only_its_own_records']),
    ('party-view-other-accounts', [(RT, "        pols = _all(store, 'crt_policy', {'party_contact_id': pid, 'crt_account_id': account_id})",
                                    "        pols = _all(store, 'crt_policy', {'party_contact_id': pid})")],
     [T_CRT + 'test_one_sub_two_accounts_each_sees_only_its_own_records']),
    ('endorsement-cross-account', [(RT, "        if pol['data'].get('crt_account_id') != data.get('crt_account_id'):", "        if False:")],
     [T_CRT + 'test_one_sub_two_accounts_each_sees_only_its_own_records']),
    ('waiver-doc-anywhere', [(RT, "        _private_doc(store, data.get('acceptance_document_id'), 'acceptance_document_id')", "        pass")],
     [T_CRT + 'test_a_waiver_needs_the_contractors_written_ok_in_the_private_store']),
    ('page-uses-localstorage', [('default-schemas/packs/risk-transfer/pages/cca-crt-book.html', "  load();\n}",
                                 "  try { localStorage.setItem('crt', '1'); } catch (e) {}\n  load();\n}")],
     [T_CRT + 'test_pages_use_no_browser_storage_and_only_the_sdk']),
    # ── isolation probe (P0): it must FAIL when a wall is broken, and the probe itself must not go soft ──
    ('sandboxes-share-one-store', [(RB, "    slot = 'store' if sandbox is None else 'sandbox:' + sandbox\n    store = ext.get(slot)",
                                    "    slot = 'store'\n    store = ext.get(slot)")],
     [T_T + 'test_probe_positive_control_passes']),
    ('staff-only-off-seen-by-probe', [(ST, "        if spec.get('staff_only'):\n            raise _err(403, 'staff_only'",
                                       "        if False and spec.get('staff_only'):\n            raise _err(403, 'staff_only'")],
     [T_T + 'test_probe_positive_control_passes']),
    ('file-meta-cross-store-seen-by-probe', [(RB, "    if not FILE_ID_RE.match(fid or ''):\n        raise SchemaError(404, 'not_found', 'No such file')\n    return jsonify(g.biz_store.file_meta(fid))",
                                              "    return jsonify({'id': fid})")],
     [T_T + 'test_probe_positive_control_passes']),
    ('probe-accepts-cross-reads', [(PR, "            self.expect(f'{tag}: read {coll}', st, {404})",
                                    "            self.expect(f'{tag}: read {coll}', st, {404, 200})"),
                                   (PR, "            self.record(f'{tag}: list {coll}', FAIL if leaked else PASS, f'{len(leaked)} foreign rows')",
                                    "            self.record(f'{tag}: list {coll}', PASS, f'{len(leaked)} foreign rows')")],
     [T_T + 'test_probe_negative_control_planted_foreign_row_fails']),
    ('probe-writes-to-business-stores', [(PR, "        if not body.get('sandbox'):", "        if False:")],
     [T_T + 'test_probe_refuses_to_write_into_a_business_store']),
    ('probe-silence-is-pass', [(PR, "        if not vs or CANNOT in vs:\n            return CANNOT", "        if False:\n            return CANNOT")],
     [T_T + 'test_probe_cannot_tell_on_server_errors']),
    # ── verdict watcher: it must disagree with a forged Meets ──
    ('watch-ignores-names', [(WA, "                elif _named(acct_name, ed.get('scheduled_names')) and _named(pname, ed.get('scheduled_projects')):",
                              "                elif True:")],
     [T_T + 'test_watch_fails_a_forged_meets_on_a_wrong_party_endorsement']),
    ('watch-trusts-agent-confirmations', [(WA, "        if not c or not str(c['data'].get('confirmed_by') or '').startswith(PERSON) \\\n                or not str(c['created_by']).startswith(PERSON):",
                                           "        if not c:")],
     [T_T + 'test_watch_fails_a_meets_that_read_an_agent_confirmation']),
    ('watch-skips-file-hash', [(WA, "                ok = h.hexdigest() == sha", "                ok = True")],
     [T_T + 'test_watch_fails_when_the_endorsement_file_no_longer_matches']),
    ('watch-skips-policy-check', [(WA, "                (cannot if changed else why).append(f'R3 no person-confirmed, file-backed, current {line} policy')",
                                   "                pass")],
     [T_T + 'test_watch_fails_a_forged_meets_with_nothing_confirmed']),
    ('watch-silence-is-pass', [(WA, "    if not rows:\n        return CANNOT, rows", "    if False:\n        return CANNOT, rows")],
     [T_T + 'test_watch_cannot_tell_with_nothing_to_check_or_no_store']),
    ('watch-blind-to-later-change', [(WA, "    def changed_since(rec):\n        return rec['updated_at'] > reviewed_at",
                                      "    def changed_since(rec):\n        return False")],
     [T_T + 'test_watch_cannot_tell_when_an_input_changed_after_the_review']),
    ('wording-compliant', [(RT, "    'meets': 'Meets requirements (documents reviewed)',", "    'meets': 'COMPLIANT',")],
     [T_CRT + 'test_positive_fully_confirmed_mep_meets_requirements']),
]


def _copy(name):
    dest = os.path.join(SCRATCH, name)
    if os.path.exists(dest):
        dest = dest + '-' + str(os.getpid())
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns('.git', '__pycache__', 'runtime', 'node_modules'))
    return dest


def _run(cwd, tests):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-x', *tests],
                       cwd=cwd, env=env, capture_output=True, text=True)
    tail = [ln for ln in p.stdout.splitlines() if ln.strip()][-1:] or ['']
    return p.returncode, tail[0]


def main():
    only = [a for a in sys.argv[1:] if not a.startswith('-')]
    os.makedirs(SCRATCH, exist_ok=True)
    every_test = sorted({t for _, _, tests in MUTANTS for t in tests})
    ctl = _copy('control')
    rc, tail = _run(ctl, every_test)
    print(f'control (unbroken copy, {len(every_test)} tests): rc={rc} {tail}')
    if rc != 0:
        print('CONTROL FAILED: the harness itself is broken, mutants prove nothing')
        return 1
    rows = []
    for name, edits, tests in MUTANTS:
        if only and name not in only:
            continue
        d = _copy(name)
        for f, old, new in edits:
            path = os.path.join(d, f)
            text = open(path, encoding='utf-8').read()
            assert text.count(old) == 1, f'mutant {name}: anchor not unique ({text.count(old)}) in {f}'
            open(path, 'w', encoding='utf-8').write(text.replace(old, new))
        rc, tail = _run(d, tests)
        caught = rc != 0
        rows.append((name, caught, len(tests), tail))
        print(f"{'CAUGHT  ' if caught else 'SURVIVED'} {name:32s} tests={len(tests)} {tail}")
    caught = sum(1 for r in rows if r[1])
    print(f'--- mutants caught {caught}/{len(rows)}')
    return 0 if caught == len(rows) else 1


if __name__ == '__main__':
    sys.exit(main())
