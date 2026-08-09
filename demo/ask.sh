#!/usr/bin/env bash
# Ask the live demo a question as one of the seeded orgs.
#
#     ./demo/ask.sh acme   "how long do refunds take?"
#     ./demo/ask.sh globex "how long do refunds take?"
#
# curl does the network and python3 only parses stdin, so this needs no packages
# and no working CA bundle inside python (the python.org macOS build ships
# without one). Used by demo.tape to record the README gif, and useful on its
# own: the same question asked by two tenants gives two different answers.
set -euo pipefail

BASE="${KD_BASE:-https://knowledge-desk.fly.dev}"
PASSWORD="demo-password-123"   # published demo credential, not a secret
ORG="${1:?usage: ask.sh <acme|globex> <question>}"
QUESTION="${2:?usage: ask.sh <acme|globex> <question>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

json_string() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

token=$(curl -fsS -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"owner@$ORG.test\",\"password\":\"$PASSWORD\",\"org_slug\":\"$ORG\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

curl -fsS -N -X POST "$BASE/ask" \
  -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' \
  -d "{\"question\": $(json_string "$QUESTION")}" \
  | ORG="$ORG" QUESTION="$QUESTION" python3 "$HERE/render.py"
