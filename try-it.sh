#!/usr/bin/env bash
# A runnable walk through the SHL Diagnostics API. It makes five calls and explains each one.
#
#   SHL_URL=https://api.squarehammerlabs.com SHL_TOKEN=<your token> ./try-it.sh
#
# It needs curl. It uses python3 to make the output readable, but works without it.
set -uo pipefail

: "${SHL_URL:?set SHL_URL to the endpoint that SHL sent you}"
: "${SHL_TOKEN:?set SHL_TOKEN to the token that SHL sent you}"

EXAMPLES="${SHL_EXAMPLES:-https://docs.squarehammerlabs.com/examples}"
AUTH="Authorization: Bearer $SHL_TOKEN"
JSON="content-type: application/json"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
pretty() { python3 -m json.tool 2>/dev/null || cat; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# --- 1. is the service there ----------------------------------------------------------------------
say "1. Is the service available?"
note "GET /health is open. It needs no token."
curl -s "$SHL_URL/health" | pretty

# --- 2. does the token work -----------------------------------------------------------------------
say "2. Does your token work?"
note "A good answer from /health proves nothing about your token. Call a /v1 route to test it."
status=$(code -H "$AUTH" "$SHL_URL/v1/runs?limit=1")
case "$status" in
  200) note "200 — your token works." ;;
  401|403) note "$status — the service refused your token. Check SHL_TOKEN, then write to us."; exit 1 ;;
  *) note "$status — unexpected. Write to bradford@squarehammerlabs.com with this number."; exit 1 ;;
esac

# --- 3. diagnose the minimal example ---------------------------------------------------------------
say "3. Diagnose a failure"
note "Downloading the minimal telemetry example: $EXAMPLES/record.min.json"
record=$(curl -sf "$EXAMPLES/record.min.json") || {
  note "Could not download the example. The documentation site may not be published yet."
  note "Ask us for record.min.json, save it beside this script, and set:"
  note "  SHL_EXAMPLES=file://\$PWD"
  exit 1
}
note "Sending it to POST /v1/diagnose ..."
response=$(curl -s "$SHL_URL/v1/diagnose" -H "$AUTH" -H "$JSON" \
  -d "{\"record\": $record, \"task_spec\": \"Lift the box off the table.\"}")
echo "$response" | pretty

rid=$(printf '%s' "$response" | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("response_id",""))' 2>/dev/null || true)
action=$(printf '%s' "$response" | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("diagnosis",{}).get("action",""))' 2>/dev/null || true)

note ""
note "Read 'action' before you change any code:"
note "  code / replan       — you can correct this in software."
note "  hardware / environment — you cannot. Stop and tell a person."
[ -n "$action" ] && note "This diagnosis says: $action"

# --- 4. what a mistake looks like -------------------------------------------------------------------
say "4. What a mistake looks like"
note "Sending tool_xyz rows with two values instead of three."
note "The service answers 422 and names the field. It does not guess."
curl -s "$SHL_URL/v1/diagnose" -H "$AUTH" -H "$JSON" \
  -d '{"record": {"t": [0.0, 0.02], "tool_xyz": [[0.4, 0.0], [0.4, 0.0]]}}' | pretty

# --- 5. report what happened -------------------------------------------------------------------------
say "5. Report what happened"
if [ -z "$rid" ]; then
  note "No response_id from step 3, so this step is skipped."
else
  note "Sending the response_id and the outcome. Nothing else — the service already has the rest."
  curl -s "$SHL_URL/v1/lessons" -H "$AUTH" -H "$JSON" \
    -d "{\"response_id\": \"$rid\", \"outcome\": \"SOLVED\", \"solved_at\": 1}" | pretty
  note ""
  note "Report every outcome, including THRASH. A fix that did not work is as useful"
  note "as one that did: the next agent is told not to try it."
fi

say "Done."
note "Full reference: https://docs.squarehammerlabs.com"
note "Questions: bradford@squarehammerlabs.com"
