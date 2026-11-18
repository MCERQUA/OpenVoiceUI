"""
pytest fixtures for ai-eyes2-refactor test suite.

ADR-010: pytest + Playwright for backend + E2E testing.

The Flask app (server.py) is a large monolith, so we test modules
in isolation where possible, and use a test client for endpoint tests.
"""

import os
import sys
import pytest

# Ensure project root is on sys.path so imports work
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(scope="session")
def flask_app():
    """Return a configured Flask test app via the create_app() factory."""
    from app import create_app
    test_app, _sock = create_app(config_override={"TESTING": True})
    return test_app


@pytest.fixture(scope="session")
def client(flask_app):
    """Return a Flask test client."""
    return flask_app.test_client()


@pytest.fixture(scope="session")
def health_checker():
    """Return a HealthChecker instance for unit-testing health logic directly."""
    from services.health import HealthChecker
    return HealthChecker()


@pytest.fixture(scope="session")
def config():
    """Return the singleton config for testing config loading."""
    from config.loader import Config
    return Config()


# Gate-4 re-check #5 probes are committed verbatim as tests/test_claimcheck5_probes{,_b}.py. Part b imports its
# helpers from the name the re-checker used (tests.test_recheck5_probes); alias that name, never a second copy.
class _Recheck5Alias:
    _NAME = 'tests.test_recheck5_probes'

    def find_spec(self, name, path=None, target=None):
        if name != self._NAME:
            return None
        import importlib.util
        return importlib.util.spec_from_loader(name, self)

    def create_module(self, spec):
        import importlib
        return importlib.import_module('tests.test_claimcheck5_probes')

    def exec_module(self, module):
        return None


sys.meta_path.append(_Recheck5Alias())


# Review 3 of PR #597: the reviewer's probe files are kept byte-identical (cmp-checked).
# One probe locates the PRE-FIX line `_intel_path.write_text(` in routes/conversation.py by
# text and asserts the guard flags it; the B2 fix removed that line, so on a fixed tree the
# lookup raises StopIteration. It is marked xfail ONLY for that exception AND ONLY while the
# FIXED line is present (review 4 of PR #597): if the fix is reverted or renamed, the
# marker is not applied and the probe runs as a plain test, so a missing fix FAILS instead
# of xfailing. If the by-name line comes back and the guard misses it, the probe fails
# with its own AssertionError either way.
# The guard's coverage of the pattern is asserted by
# tests/test_agent_writable_io_guard.py::test_scanner_flags_the_review3_b2_pattern.
_PREFIX_LINE_PROBES = {
    'test_zz_review3_conversation_image_intel.py::test_guard_does_not_see_the_sidecar_write',
}
_B2_FIXED_LINE = "_img_file.sibling('.image-intel')"


def _b2_fix_present():
    try:
        with open(os.path.join(PROJECT_ROOT, 'routes', 'conversation.py'), encoding='utf-8') as f:
            return _B2_FIXED_LINE in f.read()
    except OSError:
        return False


# ── Verbatim probe records whose FAIL is the intended outcome on this tree ────────────────────────────────────────
# The gate-4 re-check probe files (rounds 2-6, tests/test_claimcheck{2,3,4,5,6}_probes*.py) and the PR #597 review-4
# blind-spot file (tests/test_zz_review4_guard_blindspots.py) are kept BYTE-IDENTICAL to the non-author reviewer's
# files, so a probe whose outcome a fix or a host ruling changed is never edited; its expected outcome is recorded
# here instead. Every entry is xfail(strict=True) with the exact exception the closed hole produces:
#   * a HOLE probe of rounds 4-6 asserts the BAD outcome ("PASSED means the hole is real"); if the hole re-opens the
#     probe passes, strict turns the XPASS into a FAILURE, so these probes are live regression guards on main;
#   * the round 2/3 probes listed assert the safe outcome by a route a later host ruling refuses (the safe outcome
#     holds by refusal); if the refusal is removed the probe passes and strict fails the run;
#   * the review-4 blind spots are guard forms df1bbb7 deferred (none exists in the tree, review-4 grep); when the
#     scanner learns one, its XPASS fails the run and the entry must be removed here.
# Source of each outcome: the PR #595 round comments (re-check #4 rulings in c65dd9d / rules 1.4.0, re-check #5 table
# in rules 1.5.0, re-check #6 in rules 1.6.0 4e06bf8, T1 in PR #597 44fd830, review 4 in df1bbb7). The author's rules
# 1.10.3 full run shows exactly these 43 failing (0 unexplained). A probe that fails with any OTHER exception is not
# covered by its marker and fails the run as before.
_R2 = 'gate-4 re-check #2 probe; ruling F (rules 1.5.0): agent PATCH of an approved set -> 403 set_change_staff_only'
_R3_X6C = 'gate-4 re-check #3 probe; LOW ruling (rules 1.4.0): an agent key reads today only (403 today_staff_only)'
_R3_X7 = ('gate-4 re-check #3 probe; X4 ruling (rules 1.6.0): approval needs the sha256 of the diff the page showed '
          '(422 diff_sha256_required), so the probe cannot approve the stricter set the way it was written')
_R4 = 'gate-4 re-check #4 HOLE closed by rules 1.4.0 (c65dd9d: R1 staff origin, R3, R4, LOW rulings)'
_R4_X7 = 'gate-4 re-check #4 HOLE closed: repose_years below 1 is refused by the schema (validation_failed)'
_R5 = 'gate-4 re-check #5 HOLE closed by rules 1.5.0 (02bbd83: E no short-circuit, F floor + diff, P, C)'
_R5_WATCH = ('gate-4 re-check #5 watcher HOLE closed: the engine no longer reaches Meets, so there is no Meets review '
             'to watch (closure proven on forged reviews in test_biz_crt_claimcheck5.py::test_watch_*)')
_R6 = 'gate-4 re-check #6 HOLE closed by rules 1.6.0 (4e06bf8: D, M, U, G, W, K, X4)'
_R6_T1 = 'gate-4 re-check #6 HOLE T1 closed by PR #597 (terminal runs the sandbox helper, not the app uid)'
_RV4 = ('PR #597 review-4 guard blind spot deferred in df1bbb7 (form not present in the tree); '
        'remove this entry when tests/agent_io_scan.py flags it')

_INTENDED_FAILS = {
    'test_claimcheck2_probes.py::test_n6_agent_edits_items_of_the_approved_pinned_set': ('A', _R2),
    'test_claimcheck3_probes.py::test_x6c_today_query_lets_any_caller_ask_for_a_past_meets': ('A', _R3_X6C),
    'test_claimcheck3_probes.py::test_x7_superseded_set_still_meets_silently': ('A', _R3_X7),
    'test_claimcheck4_probes.py::test_x2_agent_replays_the_staff_jwt_mints_a_surface_token_and_confirms_everything':
        ('A', _R4 + ': a canvas-origin JWT never reaches a staff page'),
    'test_claimcheck4_probes.py::test_x2b_agent_replay_can_also_rewrite_set_up_records_as_a_person':
        ('AttributeError', _R4 + ': no staff page (and no page token) is served to a canvas-origin JWT'),
    'test_claimcheck4_probes.py::test_x3_cg2010_confirmed_manuscript_satisfies_completed_ops': ('A', _R4 + ' (R3)'),
    'test_claimcheck4_probes.py::test_x4_automatic_status_form_confirmed_not_blanket_skips_the_contract_tick':
        ('A', _R4),
    'test_claimcheck4_probes.py::test_x6_item_edition_is_never_compared': ('A', _R4 + ' (LOW: item edition compared)'),
    'test_claimcheck4_probes.py::test_x7_repose_years_zero_or_negative_drops_completed_ops[0]': ('A', _R4_X7),
    'test_claimcheck4_probes.py::test_x7_repose_years_zero_or_negative_drops_completed_ops[-3]': ('A', _R4_X7),
    'test_claimcheck4_probes.py::test_x9b_heavy_civil_with_xcu_exclusion_reads_not_applicable':
        ('A', _R4 + ' (LOW: heavy_civil is XCU)'),
    'test_claimcheck4_probes.py::test_x10_watcher_passes_the_replayed_meets_and_the_manuscript_cg2010':
        ('A', _R4 + ' (R3: the watcher reads the manuscript CG 20 10 as missing)'),
    'test_claimcheck5_probes.py::test_E1_HOLE_another_edition_hides_wrong_project': ('A', _R5),
    'test_claimcheck5_probes.py::test_E2_HOLE_contractor_accepts_the_edition_and_the_wrong_project_reads_meets_accepted':
        ('A', _R5),
    'test_claimcheck5_probes.py::test_E3_HOLE_after_completion_an_unconfirmed_contract_party_list_is_hidden_too':
        ('A', _R5),
    'test_claimcheck5_probes.py::test_F0_requirements_page_never_shows_the_equivalence_table':
        ('A', _R5 + ': the requirements page renders the diff and the table'),
    'test_claimcheck5_probes.py::test_F1_HOLE_agent_revised_table_makes_cg2033_count_for_completed_ops': ('A', _R5),
    'test_claimcheck5_probes.py::test_F2_table_accepts_any_ongoing_iso_form_as_completed_ops':
        ('SchemaError', _R5 + ': every ISO ongoing form -> completed is refused (iso_form_floor)'),
    'test_claimcheck5_probes.py::test_P1_HOLE_staff_archived_paper_is_restored_by_the_agent_and_counts_again[agent_key]':
        ('A', _R5),
    'test_claimcheck5_probes.py::test_P1_HOLE_staff_archived_paper_is_restored_by_the_agent_and_counts_again[canvas_jwt]':
        ('A', _R5),
    'test_claimcheck5_probes.py::test_C1_HOLE_agent_archives_the_company_and_one_person_reaches_meets': ('A', _R5),
    'test_claimcheck5_probes.py::test_C2_HOLE_canvas_jwt_archives_the_company': ('A', _R5),
    'test_claimcheck5_probes_b.py::test_C1_watch': ('A', _R5_WATCH),
    'test_claimcheck5_probes_b.py::test_P1_watch': ('A', _R5_WATCH),
    'test_claimcheck5_probes_b.py::test_E3_watch': ('A', _R5_WATCH),
    'test_claimcheck5_probes_b.py::test_E4_HOLE_pre2004_edition_hides_wrong_project_too': ('A', _R5),
    'test_claimcheck6_probes.py::test_D1_HOLE_two_step_revision_launders_the_diff_and_reaches_plain_meets': ('A', _R6),
    'test_claimcheck6_probes.py::test_D2_HOLE_generic_create_decoy_base_reads_as_the_version_in_use': ('A', _R6),
    'test_claimcheck6_probes.py::test_M1_HOLE_min_null_reads_as_zero_not_the_floor': ('A', _R6),
    'test_claimcheck6_probes.py::test_M2_HOLE_limit_key_swap_is_invisible_on_the_approval_table': ('A', _R6),
    'test_claimcheck6_probes.py::test_U1_HOLE_combined_test_accepts_another_companys_umbrella': ('A', _R6),
    'test_claimcheck6_probes.py::test_G1_HOLE_cg2026_as_manuscript_with_wording_meets_completed_ops': ('A', _R6),
    'test_claimcheck6_probes.py::test_W1_HOLE_ongoing_ops_waiver_silently_covers_a_later_completed_ops_miss':
        ('A', _R6),
    'test_claimcheck6_probes.py::test_K1_HOLE_blanket_flag_skips_a_schedule_that_names_another_gc': ('A', _R6),
    'test_claimcheck6_probes.py::test_X4_approve_without_diff_sha_is_accepted':
        ('A', _R6 + ': approval without the diff sha256 -> 422'),
    'test_claimcheck6_probes_b.py::test_T1_HOLE_agent_key_opens_a_login_shell_in_the_ovu_container': ('A', _R6_T1),
    'test_zz_review4_guard_blindspots.py::test_guard_flags_by_name_io[class_attribute_path]': ('A', _RV4),
    'test_zz_review4_guard_blindspots.py::test_guard_flags_by_name_io[global_assigned_in_function]': ('A', _RV4),
    'test_zz_review4_guard_blindspots.py::test_guard_flags_by_name_io[instance_attribute_across_methods]': ('A', _RV4),
    'test_zz_review4_guard_blindspots.py::test_guard_flags_by_name_io[percent_format_runtime_uploads]': ('A', _RV4),
    'test_zz_review4_guard_blindspots.py::test_guard_flags_by_name_io[str_format_runtime_uploads]': ('A', _RV4),
    'test_zz_review4_guard_blindspots.py::test_guard_flags_by_name_io[tempfile_dir_in_root_subdir]': ('A', _RV4),
    'test_zz_review4_guard_blindspots.py::test_spawn_guard_sees_spawn[exec_string]': ('A', _RV4),
}


def _intended_exc(kind):
    if kind == 'A':
        return AssertionError
    if kind == 'AttributeError':
        return AttributeError
    from services.biz.schema import SchemaError
    return SchemaError


def pytest_collection_modifyitems(config, items):
    fixed = None
    for item in items:
        intended = _INTENDED_FAILS.get(f'{item.path.name}::{item.name}')
        if intended is not None:
            item.add_marker(pytest.mark.xfail(raises=_intended_exc(intended[0]), strict=True, reason=intended[1]))
        if f'{item.path.name}::{item.name}' in _PREFIX_LINE_PROBES:
            if fixed is None:
                fixed = _b2_fix_present()
            if fixed:
                item.add_marker(pytest.mark.xfail(
                    raises=StopIteration, strict=True,
                    reason='probe looks up the pre-fix vulnerable line by text; the B2 fix '
                           f'removed it (fixed line {_B2_FIXED_LINE!r} is present)'))
