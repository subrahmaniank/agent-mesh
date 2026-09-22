#!/usr/bin/env python3
"""Load the Cedar policy set and entity data into cedar-agent.

Run automatically by the `cedar-loader` compose service, and re-runnable after
editing cedar/ with:

    docker compose up -d --force-recreate cedar-loader && docker compose logs cedar-loader

Standard library only — no pip install, no network fetch at build time.

── Why this is Python and not sh+awk ────────────────────────────────────────
The first version built the JSON with printf and awk. The escaping looked
correct under GNU awk on the host and silently produced invalid JSON under
busybox awk in the curl image, which handles backslashes in gsub replacements
differently. The corruption landed on this line:

    unless { principal.registry_status == "approved" };

whose quotes came through unescaped, so the JSON string terminated early and
cedar-agent answered 400 "malformed syntax". Every policy was rejected.

That failure mode is dangerous in a specific way: Cedar is deny-by-default, so
an empty policy set does not fail open — it denies everything. The platform
looks alive and refuses every request, and the cause is three containers away.
json.dumps removes the entire class of bug.

── Verified against permitio/cedar-agent 0.2.0 ──────────────────────────────
  PUT /v1/data      — the entity array, verbatim.
  PUT /v1/policies  — [{"id", "content"}], with EXACTLY ONE Cedar statement per
                      entry. Submitting the whole set as one entry is rejected
                      at the second statement:
                        "unexpected token `forbid`"
                      Hence one file per policy in cedar/policies/.
  PUT /v1/schema    — optional; loaded only if cedar/schema.json exists.
"""

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

CEDAR_AGENT_URL = os.getenv("CEDAR_AGENT_URL", "http://cedar-agent:8180").rstrip("/")
CEDAR_DIR = pathlib.Path(os.getenv("CEDAR_DIR", "/cedar"))
POLICY_DIR = pathlib.Path(os.getenv("POLICY_DIR", str(CEDAR_DIR / "policies")))
ENTITIES_FILE = pathlib.Path(os.getenv("ENTITIES_FILE", str(CEDAR_DIR / "entities.json")))
SCHEMA_FILE = pathlib.Path(os.getenv("SCHEMA_FILE", str(CEDAR_DIR / "schema.json")))


def put(path: str, payload) -> None:
    """PUT JSON, or exit non-zero with the server's own explanation."""
    request = urllib.request.Request(
        f"{CEDAR_AGENT_URL}{path}",
        method="PUT",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"cedar-loader: PUT {path} -> {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        print(f"cedar-loader: PUT {path} -> {exc.code}\n{detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"cedar-loader: PUT {path} failed: {exc}", file=sys.stderr)
        sys.exit(1)


def wait_for_agent(timeout_s: int = 120) -> None:
    deadline = time.monotonic() + timeout_s
    print(f"cedar-loader: waiting for {CEDAR_AGENT_URL} ...")
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{CEDAR_AGENT_URL}/v1/policies", timeout=5):
                print("cedar-loader: cedar-agent is up")
                return
        except Exception:
            time.sleep(2)
    print(f"cedar-loader: cedar-agent not ready after {timeout_s}s", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    wait_for_agent()

    # Schema first when present: it is what makes cedar-agent reject a typo in
    # an attribute name instead of silently evaluating it as absent.
    if SCHEMA_FILE.exists():
        put("/v1/schema", json.loads(SCHEMA_FILE.read_text()))
    else:
        print(f"cedar-loader: no {SCHEMA_FILE}, skipping schema")

    # Entities before policies: a policy that references entities which do not
    # exist yet decides nothing useful.
    put("/v1/data", json.loads(ENTITIES_FILE.read_text()))

    policy_files = sorted(POLICY_DIR.glob("*.cedar"))
    if not policy_files:
        print(f"cedar-loader: no .cedar files in {POLICY_DIR}", file=sys.stderr)
        sys.exit(1)

    # The filename becomes the policy id, so a denial in the PDP is traceable
    # back to the file that caused it.
    put("/v1/policies", [{"id": f.stem, "content": f.read_text()} for f in policy_files])
    print(f"cedar-loader: loaded {len(policy_files)} policies: "
          + ", ".join(f.stem for f in policy_files))

    # Read back what the PDP actually holds. A load that reports success but
    # stores nothing is the failure this guards against.
    with urllib.request.urlopen(f"{CEDAR_AGENT_URL}/v1/policies", timeout=10) as response:
        stored = json.load(response)
    if len(stored) != len(policy_files):
        print(f"cedar-loader: PDP holds {len(stored)} policies, expected "
              f"{len(policy_files)}", file=sys.stderr)
        sys.exit(1)
    print("cedar-loader: done")


if __name__ == "__main__":
    main()
