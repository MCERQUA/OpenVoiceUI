"""
Interim spoken status for DELEGATED voice turns (pledge 25dfb03c).

The incident (test-dev, 2026-09-16, postmortem
docs/incidents/2026-11-voice-stall-postmortem.md in the ops repo): the agent
handed a research ask to a sub-agent and the user then heard NOTHING for
~9 minutes. The route's old silence-breaker could not cover that turn:

  * it was evaluated ONLY on gateway `heartbeat` events, which the gateway
    emits only after 5 s with no other event. While a sub-agent works, its
    tool events are routed onto the parent's stream continuously, so the
    heartbeat (and with it the status check) can never come;
  * after the agent's own spoken acknowledgement (`text_interim`) the first
    status was pushed out to the 90 s repeat interval.

This module keeps the decision pure and testable. It speaks ONLY facts it
observed on the stream (NO FAKE REPLIES, Mike 2026-10-20):

  * how many background tasks were started / have finished,
  * how many steps (tool calls) the sub-agent(s) have made,
  * what the most recent step is.

It never invents progress, never repeats itself when nothing changed
(nothing new = say nothing), never speaks once the final answer has begun
streaming, and is capped per turn. It does NOT replace the agent's own words:
when the agent acknowledges first, that acknowledgement resets the silence
clock and this stays quiet until the user has heard nothing for a while.
"""

import os

from services.gateways.compat import is_subagent_session_key


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


# First status after this many seconds of silence on a delegated turn.
# Must stay well under 60 s so the user hears something before a minute.
FIRST_AFTER_S = _env_float('VOICE_DELEGATED_STATUS_FIRST_S', 40)
# A further status needs this much silence AND something new to say.
REPEAT_AFTER_S = _env_float('VOICE_DELEGATED_STATUS_REPEAT_S', 60)
# Hard cap per turn — this is a status line, not a commentary track.
MAX_PER_TURN = int(_env_float('VOICE_DELEGATED_STATUS_MAX', 3))

# What a sub-agent step is doing, in words. Unknown tools are described
# generically ("working through a step") rather than guessed at.
STEP_DESCRIPTIONS = {
    'exec': 'running a command',
    'bash': 'running a command',
    'web_search': 'searching the web',
    'web_fetch': 'reading a webpage',
    'browser': 'using the browser',
    'read': 'reading a file',
    'write': 'writing a file',
    'edit': 'editing a file',
    'glob': 'looking for files',
    'grep': 'searching through files',
    'image': 'looking at an image',
}

_NUMBER_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six',
                 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve']


def _num(n):
    return _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)


def describe_step(tool_name):
    if not tool_name:
        return None
    return STEP_DESCRIPTIONS.get(str(tool_name).strip().lower())


class DelegatedStatus:
    """Per-turn tracker. Feed it the stream's events; ask it `due()`."""

    def __init__(self, first_after_s=None, repeat_after_s=None, max_per_turn=None):
        self.first_after_s = FIRST_AFTER_S if first_after_s is None else first_after_s
        self.repeat_after_s = REPEAT_AFTER_S if repeat_after_s is None else repeat_after_s
        self.max_per_turn = MAX_PER_TURN if max_per_turn is None else max_per_turn
        self.spawned = 0          # parent's spawn tool calls / 'spawning' signals
        self.started = 0          # sub-agent lifecycle starts
        self.ended = 0            # sub-agent lifecycle ends
        self.interim_seen = False  # gateway said "spoke, sub-agents pending"
        self.steps = 0            # sub-agent tool starts observed
        self.last_step = None     # most recent sub-agent tool name
        self.answer_streaming = False
        self.spoken = 0
        self._last_spoken_facts = None

    # ── inputs ────────────────────────────────────────────────────────
    def note_action(self, action):
        if not isinstance(action, dict):
            return
        kind = action.get('type')
        phase = action.get('phase')
        if kind == 'subagent':
            if phase == 'spawning':
                self.spawned += 1
            elif phase == 'start':
                self.started += 1
            elif phase == 'end':
                self.ended += 1
            return
        if kind == 'tool' and phase == 'start':
            sk = action.get('sessionKey') or ''
            if sk and is_subagent_session_key(sk):
                self.steps += 1
                self.last_step = action.get('name')
            # The parent's own spawn tool call is NOT counted here: the
            # gateway follows it with a 'subagent'/'spawning' action for the
            # same call, and counting both would double the task count.

    def note_interim(self):
        """The agent's own words were spoken and sub-agents are still
        running. Whatever streams next is a new answer leg."""
        self.interim_seen = True
        self.answer_streaming = False

    def note_answer_text(self):
        """The final answer has started streaming — never talk over it."""
        self.answer_streaming = True

    # ── state ─────────────────────────────────────────────────────────
    @property
    def tasks(self):
        return max(self.spawned, self.started)

    @property
    def delegated(self):
        return self.tasks > 0 or self.interim_seen

    def _facts(self):
        return (self.tasks, self.ended, self.steps)

    def _text(self):
        tasks = self.tasks
        running = max(tasks - self.ended, 0)
        if tasks and running == 0:
            return ('The background work has finished. '
                    'I am putting the answer together now.')
        if tasks > 1:
            parts = [f'{_num(running).capitalize()} of {_num(tasks)} '
                     f'background tasks {"is" if running == 1 else "are"} still running.']
        else:
            parts = ['That is running in the background.']
        if self.steps:
            step = f'{_num(self.steps).capitalize()} {"step" if self.steps == 1 else "steps"} so far'
            desc = describe_step(self.last_step)
            parts.append(f'{step}, most recently {desc}.' if desc else f'{step}.')
        parts.append('You will hear the result when it is done.')
        return ' '.join(parts)

    # ── decision ──────────────────────────────────────────────────────
    def due(self, now, last_audio_time, tts_enabled=True):
        """Return the status sentence to speak now, or None."""
        if not tts_enabled or not self.delegated or self.answer_streaming:
            return None
        if self.spoken >= self.max_per_turn:
            return None
        silence = now - last_audio_time
        if self.spoken == 0:
            if silence < self.first_after_s:
                return None
        else:
            if silence < self.repeat_after_s:
                return None
            if self._facts() == self._last_spoken_facts:
                return None  # nothing new = say nothing
        text = self._text()
        self.spoken += 1
        self._last_spoken_facts = self._facts()
        return text
