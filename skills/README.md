# OpenVoiceUI agent skills

OpenClaw skills that teach the agent how to use OpenVoiceUI's own features (canvas pages, custom
faces, music, 3D pages, voice conversation rules). Each folder is one skill with a `SKILL.md`.

The installers put this folder where OpenClaw loads it, as `~/.openclaw/skills/openvoiceui/`:
Docker, Pinokio and the npm CLI mount it read-only; native installs copy it with
`install-skills.js`. Do not add your own skills here; see `docs/SKILLS.md` for where they
go, how updates work, and the rules for contributing a skill to this bundle.
