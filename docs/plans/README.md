# Design notes and plans

Working design documents for features that are planned, in progress or partly shipped. They are
kept in the repo for contributors and are **not** part of the published documentation site
(`website/docusaurus.config.ts` excludes `docs/plans/`). User-facing documentation lives in the
other folders under `docs/`.

| Document | Topic | Status (2026-10-16) |
|---|---|---|
| [3d-avatar-face-plugin.md](3d-avatar-face-plugin.md) | 3D avatar face plugin (TalkingHead.js + VRM) | Plan |
| [camera-capture-build-plan.md](camera-capture-build-plan.md) | Camera capture and job albums | Partly shipped (camera capture and job albums landed in 2026.9.7) |
| [desktop-refactor/PLAN.md](desktop-refactor/PLAN.md), [IMPLEMENTATION.md](desktop-refactor/IMPLEMENTATION.md) | Desktop canvas refactor | Phases tracked in #234, #235, #237 are closed; desktop search/sort/shortcuts remain open in #236 |
| [hermes-agent-gateway-catalog-contract.patch](hermes-agent-gateway-catalog-contract.patch) | Hermes gateway plugin catalog contract | Patch kept for reference |
| [voice-latency-deferred-fixes.md](voice-latency-deferred-fixes.md) | Voice latency findings deferred for telemetry | Open |

Moved here from the former root `dev-tasks/` folder and `docs/camera-capture-build-plan.md`
during the 2026-10-16 organization pass. The multi-agent framework design
(`future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md`) still lives at its old path because source
comments cite it; see [../REPO-ORGANIZATION.md](../REPO-ORGANIZATION.md).
