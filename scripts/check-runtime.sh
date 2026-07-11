#!/usr/bin/env bash
# Print which runtime-related env vars are set (values redacted).
# Safe for logs — does not print secrets.
#
# Always exits 0 so CI / boot scripts can call it without keys present:
#   bash scripts/check-runtime.sh
# Missing XAI_API_KEY is reported as "unset" — not a hard failure.

set -u

vars=(
  XAI_API_KEY
  GROQ_API_KEY
  ELEVENLABS_API_KEY
  ELEVENLABS_VOICE_ID
  FAL_KEY
  HUME_API_KEY
  HUME_SECRET_KEY
  RESEMBLE_API_KEY
  RESEMBLE_VOICE_UUID
  DEEPGRAM_API_KEY
  OPENAI_API_KEY
  OPENROUTER_API_KEY
  GATEWAY_SESSION_KEY
  SUPERTONIC_API_URL
  STT_API_URL
  STT_API_KEY
  USE_GROQ
  USE_GROQ_TTS
)

echo "OpenVoiceUI runtime env presence ($(date -u +%Y-%m-%dT%H:%MZ))"
echo "cwd: $(pwd)"
echo "---"
any=0
for v in "${vars[@]}"; do
  if [ -n "${!v-}" ]; then
    printf 'SET   %s\n' "$v"
    any=1
  else
    printf 'unset %s\n' "$v"
  fi
done
echo "---"
if [ "$any" -eq 0 ]; then
  echo "No listed credentials present in this shell."
else
  echo "At least one listed env var is set (values not shown)."
fi
exit 0
