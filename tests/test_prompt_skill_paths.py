"""The voice prompt must not point the agent at a server path only one deployment has.

Both copies of the voice prompt (prompts/voice-system-prompt.md, which is what runs, and the
_VOICE_INSTRUCTIONS fallback in routes/conversation.py) told the agent to run
`python3 /mnt/shared-skills/soundcloud/...` and `.../bandcamp/...`. That folder is a hosted
deployment's mount; on any other install the command fails. The prompts now name the skill
(OpenClaw lists every installed skill with its location) and say what to do without it.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _prompts():
    from routes.conversation import _VOICE_INSTRUCTIONS
    return {
        'prompts/voice-system-prompt.md': (ROOT / 'prompts' / 'voice-system-prompt.md').read_text(),
        'routes/conversation.py:_VOICE_INSTRUCTIONS': _VOICE_INSTRUCTIONS,
    }


@pytest.mark.parametrize('name', list(_prompts()))
def test_prompt_names_no_deployment_skill_path(name):
    text = _prompts()[name]
    assert '/mnt/shared-skills' not in text, name


@pytest.mark.parametrize('name', list(_prompts()))
def test_prompt_still_routes_to_the_soundcloud_and_bandcamp_skills(name):
    text = _prompts()[name]
    for skill in ('soundcloud', 'bandcamp'):
        assert f'`{skill}` skill' in text, (name, skill)
    assert text.count('scripts/find_track.py') >= 2, name
