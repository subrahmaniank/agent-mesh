#!/bin/sh
# Load the Cedar policy set and entity data into cedar-agent.
#
# Run automatically by the `cedar-loader` compose service, and re-runnable
# after editing cedar/ with:
#
#     docker compose up cedar-loader
#
# Pure sh + awk: the curl image has neither python nor jq.
#
# ⚠️ NOT YET RUN against cedar-agent. Endpoints come from its README
# (PUT /v1/data, PUT /v1/policies); the policy request shape is the most likely
# thing to need adjusting. Every response is printed and a failure exits
# non-zero, so a mismatch surfaces loudly instead of leaving an empty policy
# set — which, with Cedar's deny-by-default, would block everything rather than
# allow it.

set -eu

CEDAR_AGENT_URL="${CEDAR_AGENT_URL:-http://cedar-agent:8180}"
POLICY_FILE="${POLICY_FILE:-/cedar/policies.cedar}"
ENTITIES_FILE="${ENTITIES_FILE:-/cedar/entities.json}"

echo "cedar-loader: waiting for ${CEDAR_AGENT_URL} ..."
i=0
until curl -fsS "${CEDAR_AGENT_URL}/v1/policies" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "cedar-loader: cedar-agent did not become ready in 120s" >&2
    exit 1
  fi
  sleep 2
done
echo "cedar-loader: cedar-agent is up"

# --- entities -----------------------------------------------------------------
# Loaded first: a policy referencing entities that do not exist yet decides
# nothing useful. The file is sent verbatim.
echo "cedar-loader: loading entities from ${ENTITIES_FILE}"
curl -fsS -X PUT "${CEDAR_AGENT_URL}/v1/data" \
  -H 'Content-Type: application/json' \
  --data-binary "@${ENTITIES_FILE}" \
  -w 'cedar-loader: entities HTTP %{http_code}\n'

# --- policies -----------------------------------------------------------------
# The whole file is submitted as one named policy document. awk escapes
# backslashes and quotes, then folds newlines into \n so the Cedar source
# survives as a single JSON string.
echo "cedar-loader: loading policies from ${POLICY_FILE}"
{
  printf '[{"id":"agentmesh-base","content":"'
  awk '{ gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); printf "%s\\n", $0 }' "${POLICY_FILE}"
  printf '"}]'
} > /tmp/policies.json

curl -fsS -X PUT "${CEDAR_AGENT_URL}/v1/policies" \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/policies.json \
  -w 'cedar-loader: policies HTTP %{http_code}\n'

echo "cedar-loader: loaded. Current policy set:"
curl -fsS "${CEDAR_AGENT_URL}/v1/policies" || true
echo
echo "cedar-loader: done"
