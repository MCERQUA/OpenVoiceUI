"""
routes/message_classifier.py — Voice message lane classifier

Classifies incoming user messages into one of three routing lanes BEFORE
they are forwarded to the OpenClaw gateway. Classification is purely
pattern-based (no LLM calls) and must complete in well under 1ms.

Lane semantics
--------------
  context   — (default) adds information; safe to queue alongside current run
  steer     — redirects or cancels the current task; inject at next tool boundary
  fast_lane — independent action that can run in parallel (open page, play music,
               quick lookup); route to sub-agent or handle directly

When the agent is NOT busy every message is classified as "context" so it
flows straight to the main agent without any routing overhead.

Usage
-----
    from routes.message_classifier import classify_message

    lane = classify_message(text, agent_busy=True)
    # lane is one of: "context" | "steer" | "fast_lane"
"""

import re

# ---------------------------------------------------------------------------
# Compiled pattern sets (built once at import time)
# ---------------------------------------------------------------------------

# -- STEER patterns ----------------------------------------------------------
# These indicate the user wants to CHANGE or CANCEL what the agent is doing.
# Checked first because a steer phrase ("no, stop that") must not be mis-routed
# as a fast-lane "stop music" command.
_STEER_PATTERNS = [
    # Direct negation / correction openers
    r'\bno\b',                          # "no, don't do that"
    r'\bnope\b',
    r'\bnaw\b',                         # "naw, I want X instead"
    r'\bnah\b',
    r'\bnuh[\s-]?uh\b',                 # "nuh-uh" / "nuh uh"
    r'\buh[\s-]?uh\b',                  # "uh-uh" (rejection)
    r'\bwait\b',                        # "wait, stop"
    r'\bhold on\b',
    r'\bhold up\b',
    r'\bactually\b',                    # "actually make it red"
    r'\binstead\b',                     # "do it in blue instead"
    r'\bbut\b',                         # "but make the header bigger"
    r'\bscratch that\b',
    r'\bforget that\b',
    r'\bforget it\b',
    r'\bignore that\b',
    r'\bnevermind\b',
    r'\bnever mind\b',
    r'\bnot that\b',
    r'\bdon\'?t do that\b',
    r'\bstop doing that\b',
    r'\bstop that\b',
    r'\bthat\'?s wrong\b',
    r'\bwrong\b',
    r'\bthat\'?s not right\b',
    r'\bnot right\b',
    r'\bthat\'?s not what i\b',
    r'\bthat\'?s not it\b',
    r'\bchange it\b',
    r'\bchange that\b',
    r'\bchange the\b',
    r'\bdo it differently\b',
    r'\bdo it another way\b',
    r'\bdo it a different way\b',
    r'\btry a different\b',
    r'\btry another\b',
    r'\bstart over\b',
    r'\bredo\b',
    r'\bredo that\b',
    r'\bundo\b',
    r'\bundo that\b',
    r'\bback up\b',
    r'\bgo back\b',                     # context-dependent but erring steer-side
    r'\brevert\b',
    r'\bcancel that\b',
    r'\babort that\b',
    r'\bstop working on\b',
    r'\bdon\'?t bother\b',
    r'\bdon\'?t worry about\b',
    r'\bi said\b',                      # "I said blue, not red"
    r'\bi meant\b',                     # "I meant the footer, not the header"
    r'\bi mean\b',
    r'\bwhat i want is\b',
    r'\bwhat i meant was\b',
    r'\bwhat i said was\b',
    r'\bdifferently\b',
    r'\bdifferent approach\b',
    r'\buse a different\b',
    r'\buse another\b',
    # Scope refinement / narrowing — "X only", "just X", "not Y"
    # These are corrections of in-flight work: must steer, not queue
    r'\bonly\s+\w+.*\b(?:not|don\'?t|no)\b',       # "only X, not Y"
    r'\b(?:not|don\'?t include|no)\s+(?:the\s+|any\s+|other\s+|any other\s+)',  # "not the others", "not any other"
    r'\bjust\s+(?:the|that|these|those)\s+\w+',    # "just the X"
    r'\bonly\s+(?:the|that|these|those|from)\s+\w+',  # "only the X", "only from"
    # "X only" at end of sentence / before punctuation / before "not"
    r'\b\w+\s+only\s*(?:[.,!?]|$|\s+not\b)',       # "emails only.", "joshai only not"
    r'\bexclude\s+(?:the|any|other)\b',            # "exclude the others"
    r'\bleave out\s+\w+',                          # "leave out Y"
    r'\bwithout\s+(?:the|any|other)\s+\w+',        # "without the others"
    r'\bfilter\s+(?:out|to|down)\b',               # "filter out Y", "filter to X"
    r'\bnarrow\s+(?:it|down|to)\b',                # "narrow it down"
]

# Pre-compiled steer regex — any match = steer
_STEER_RE = re.compile(
    '|'.join(f'(?:{p})' for p in _STEER_PATTERNS),
    re.IGNORECASE,
)

# -- FAST LANE patterns -------------------------------------------------------
# Independent actions that don't touch the current task.
# Organised as (prefix, object) pairs or standalone commands.

# Navigation / page opening
_FAST_OPEN_RE = re.compile(
    r'\b(?:open|show|go to|switch to|navigate to|take me to|bring up|pull up|load)\b'
    r'.{0,40}'
    r'\b(?:dashboard|crm|kanban|page|calendar|music|canvas|settings|profile|'
    r'map|weather|news|analytics|reports?|leads?|contacts?|tasks?|notes?|files?|'
    r'gallery|images?|photos?|videos?|documents?|seo|pipeline|inbox|email|'
    r'desktop|home|menu|chat|history)\b',
    re.IGNORECASE,
)

# Play / audio control
_FAST_PLAY_RE = re.compile(
    r'\b(?:play|start playing|queue|put on|resume|unpause)\b'
    r'.{0,50}'
    r'\b(?:music|song|track|playlist|something|audio|radio|jazz|rock|pop|'
    r'lofi|lo-?fi|beats?|vibes?|ambient|classical|hip.?hop|chill)\b'
    r'|'
    r'\b(?:play|start|resume) music\b',
    re.IGNORECASE,
)

# Stop / pause — but NOT "stop that / stop doing that" (those are steer)
# Must NOT match if followed by "that" or "doing" or "working"
_FAST_STOP_RE = re.compile(
    r'\b(?:stop|pause|mute|silence|skip|next track|previous track)\b'
    r'(?!\s+(?:that|this|doing|working|it\b))'
    r'.{0,40}'
    r'\b(?:music|song|track|audio|playback|playing|the song|the music)\b'
    r'|'
    r'\b(?:pause music|mute music|stop music|skip this|next track|previous track)\b',
    re.IGNORECASE,
)

# Volume control
_FAST_VOLUME_RE = re.compile(
    r'\b(?:volume|turn it up|turn it down|louder|quieter|softer)'
    r'|'
    r'\bturn (?:the )?(?:volume|music|sound) (?:up|down)\b',
    re.IGNORECASE,
)

# Sleep / wake lifecycle
_FAST_SLEEP_RE = re.compile(
    r'\b(?:go to sleep|goodnight|good night|bye|goodbye|see you|talk later|'
    r'wake up|wake me up|hey wake|i\'?m back|you there|are you there|'
    r'hello|hey there|hi there)\b',
    re.IGNORECASE,
)

# Quick lookups — questions that stand alone
_FAST_LOOKUP_RE = re.compile(
    r'\b(?:what(?:\'?s)? (?:the )?(?:time|date|day|weather|temperature|temp|news))\b'
    r'|'
    r'\b(?:how(?:\'?s)? (?:the )?(?:weather|market|traffic))\b'
    r'|'
    r'\btell me (?:the )?(?:time|date|weather|news)\b'
    r'|'
    r'\b(?:current )?(?:time|date) (?:please|now)?\b',
    re.IGNORECASE,
)

# Hide / close / dismiss
_FAST_CLOSE_RE = re.compile(
    r'\b(?:close|hide|dismiss|minimize|collapse|exit)\b'
    r'.{0,40}'
    r'\b(?:that|this|window|panel|sidebar|menu|modal|popup|canvas|page|tab|'
    r'dashboard|crm|it)\b',
    re.IGNORECASE,
)

# Explicit sub-agent delegation phrases
_FAST_SUBAGENT_RE = re.compile(
    r'\b(?:run (?:that|it) in (?:the )?background|'
    r'run in (?:the )?background|do (?:that|it) in (?:the )?background|'
    r'handle (?:that|it) separately|do (?:that|it) on the side|'
    r'spin (?:that|it) off|queue (?:that|it) up|'
    r'have (?:an agent|the agent|someone) (?:do|handle|check|look up))\b',
    re.IGNORECASE,
)

# All fast-lane checks in priority order
_FAST_LANE_CHECKS = [
    _FAST_OPEN_RE,
    _FAST_PLAY_RE,
    _FAST_STOP_RE,
    _FAST_VOLUME_RE,
    _FAST_SLEEP_RE,
    _FAST_LOOKUP_RE,
    _FAST_CLOSE_RE,
    _FAST_SUBAGENT_RE,
]

# ---------------------------------------------------------------------------
# Steer guard — phrases that look like steer but are NOT
# (e.g. "stop the music" → fast_lane, not steer)
# ---------------------------------------------------------------------------
# If the message begins with a steer word but also matches a strong fast-lane
# pattern, trust the fast-lane classification.

_STEER_ONLY_WORDS = re.compile(
    r'^\s*(?:no|nope|wait|hold on|hold up|actually|instead|but|'
    r'scratch that|forget that|nevermind|never mind|wrong|'
    r'cancel that|undo|revert|start over)\b',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_message(text: str, agent_busy: bool = False) -> str:
    """Classify a user message into a routing lane.

    Returns one of: 'context', 'steer', 'fast_lane'

    When agent_busy is False (agent is idle), always returns 'context' so
    the message goes directly to the main agent with zero overhead.

    Classification is purely pattern-based — no LLM calls, sub-millisecond.
    Conservative: defaults to 'context' when no pattern matches.
    """
    if not agent_busy:
        return 'context'

    if not text or not text.strip():
        return 'context'

    cleaned = text.strip()

    # --- Fast-lane check first (so "stop the music" != steer) ---------------
    for pattern in _FAST_LANE_CHECKS:
        if pattern.search(cleaned):
            # Verify this isn't a steer-opener overriding a fast-lane intent.
            # A message like "no, stop the music" is still fast_lane because
            # the core action is music control.
            # But "no, stop doing that" is steer — caught by steer check below.
            return 'fast_lane'

    # --- Steer check ---------------------------------------------------------
    if _STEER_RE.search(cleaned):
        return 'steer'

    # --- Default: context ----------------------------------------------------
    return 'context'


# ---------------------------------------------------------------------------
# Convenience helpers for callers that want the full result as a dict
# ---------------------------------------------------------------------------

_LANE_META = {
    'context': {
        'queue_mode': 'collect',
        'description': 'Queued alongside current run; agent sees it after current step',
    },
    'steer': {
        'queue_mode': 'steer',
        'description': 'Injected at next tool boundary; remaining tools skipped',
    },
    'fast_lane': {
        'queue_mode': 'parallel',
        'description': 'Route to sub-agent or handle directly in parallel',
    },
}


def classify_message_full(text: str, agent_busy: bool = False) -> dict:
    """Same as classify_message() but returns a dict with lane + metadata."""
    lane = classify_message(text, agent_busy)
    return {
        'lane': lane,
        **_LANE_META[lane],
        'text': text,
        'agent_busy': agent_busy,
    }


# ---------------------------------------------------------------------------
# Self-test (python routes/message_classifier.py)
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    IDLE = False
    BUSY = True

    tests = [
        # (text, agent_busy, expected_lane, description)

        # --- When agent is idle, always context ---
        ('play some music',         IDLE, 'context',   'idle → always context'),
        ('no stop that',            IDLE, 'context',   'idle → always context'),
        ('what time is it',         IDLE, 'context',   'idle → always context'),

        # --- FAST LANE ---
        ('play some music',              BUSY, 'fast_lane', 'music play'),
        ('put on some jazz',             BUSY, 'fast_lane', 'music play variant'),
        ('open the CRM',                 BUSY, 'fast_lane', 'open page'),
        ('show me the dashboard',        BUSY, 'fast_lane', 'show dashboard'),
        ('go to the kanban board',       BUSY, 'fast_lane', 'navigate to page'),
        ('bring up my contacts',         BUSY, 'fast_lane', 'bring up contacts'),
        ('close this panel',             BUSY, 'fast_lane', 'close panel'),
        ('hide that sidebar',            BUSY, 'fast_lane', 'hide sidebar'),
        ('what time is it',              BUSY, 'fast_lane', 'time lookup'),
        ("what's the weather",           BUSY, 'fast_lane', 'weather lookup'),
        ('volume up',                    BUSY, 'fast_lane', 'volume up'),
        ('turn the volume down',         BUSY, 'fast_lane', 'volume down'),
        ('louder please',                BUSY, 'fast_lane', 'louder'),
        ('stop the music',               BUSY, 'fast_lane', 'stop music'),
        ('pause the music',              BUSY, 'fast_lane', 'pause music'),
        ('skip this track',              BUSY, 'fast_lane', 'skip track'),
        ('go to sleep',                  BUSY, 'fast_lane', 'sleep'),
        ('goodnight',                    BUSY, 'fast_lane', 'goodnight'),
        ('wake up',                      BUSY, 'fast_lane', 'wake up'),
        ('switch to the analytics page', BUSY, 'fast_lane', 'switch page'),
        ('pull up my notes',             BUSY, 'fast_lane', 'pull up notes'),
        ('run that in the background',   BUSY, 'fast_lane', 'explicit background'),

        # --- STEER ---
        ('no wait do it differently',    BUSY, 'steer', 'no wait correction'),
        ('actually make it red',         BUSY, 'steer', 'actually redirect'),
        ('scratch that',                 BUSY, 'steer', 'scratch that'),
        ('nevermind',                    BUSY, 'steer', 'nevermind'),
        ("no that's wrong",              BUSY, 'steer', 'that is wrong'),
        ('change it to blue',            BUSY, 'steer', 'change it'),
        ('undo that',                    BUSY, 'steer', 'undo'),
        ('start over',                   BUSY, 'steer', 'start over'),
        ('stop working on that',         BUSY, 'steer', 'stop working on — steer not fast'),
        ("don't do that",                BUSY, 'steer', "don't do that"),
        ('I meant the footer not the header', BUSY, 'steer', 'I meant correction'),
        ('use a different color',        BUSY, 'steer', 'use a different'),
        ('try another approach',         BUSY, 'steer', 'try another'),
        ('hold on',                      BUSY, 'steer', 'hold on'),
        ('forget it',                    BUSY, 'steer', 'forget it'),
        ("that's not what I wanted",     BUSY, 'steer', 'not what i wanted'),
        ('revert to the original',       BUSY, 'steer', 'revert'),
        ('cancel that task',             BUSY, 'steer', 'cancel that'),
        ('instead use the serif font',   BUSY, 'steer', 'instead redirect'),
        ('but make the header bigger',   BUSY, 'steer', 'but redirect'),

        # --- CONTEXT (default) ---
        ('the client name is John',           BUSY, 'context', 'adds info'),
        ('oh and use the blue logo',          BUSY, 'context', 'additive context'),
        ("for the roofing project",           BUSY, 'context', 'qualifier context'),
        ('make it mobile friendly',           BUSY, 'context', 'instruction addition'),
        ('remember to add a contact form',    BUSY, 'context', 'additive reminder'),
        ('the budget is ten thousand dollars', BUSY, 'context', 'info delivery'),
        ('his email is john@example.com',     BUSY, 'context', 'data point'),
        ('the deadline is next Friday',       BUSY, 'context', 'deadline info'),
        ('keep the tone professional',        BUSY, 'context', 'style guidance'),
        ('also add a phone number field',     BUSY, 'context', 'additive also'),
        ('the address is 123 main street',    BUSY, 'context', 'address info'),
    ]

    passed = 0
    failed = 0

    for text, busy, expected, desc in tests:
        result = classify_message(text, busy)
        ok = result == expected
        status = 'PASS' if ok else 'FAIL'
        if not ok:
            failed += 1
            print(f'[{status}] {desc!r}')
            print(f'       text={text!r}  busy={busy}')
            print(f'       expected={expected!r}  got={result!r}')
        else:
            passed += 1
            print(f'[{status}] {desc!r} → {result}')

    print(f'\n{passed} passed, {failed} failed out of {len(tests)} tests')

    # Timing check
    import timeit
    sample = 'actually no wait stop that and open the CRM instead'
    n = 10_000
    elapsed = timeit.timeit(
        lambda: classify_message(sample, agent_busy=True),
        number=n,
    )
    avg_us = (elapsed / n) * 1_000_000
    print(f'\nTiming: {avg_us:.2f} µs per call over {n} iterations  (target: <1000 µs)')
