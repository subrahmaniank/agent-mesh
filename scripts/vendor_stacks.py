#!/usr/bin/env python3
"""Bring up the products that publish their own compose files.

    python3 scripts/vendor_stacks.py up            # start everything vendored
    python3 scripts/vendor_stacks.py up langfuse   # just one
    python3 scripts/vendor_stacks.py down
    python3 scripts/vendor_stacks.py status
    python3 scripts/vendor_stacks.py refresh langfuse   # re-pin to latest upstream

Runs on Windows, macOS and Linux: Python plus `docker`, no shell, no bash, no
GNU coreutils. It replaces scripts/up-vendor-stacks.sh, which needed bash 4 for
`${name^^}` and so failed on stock macOS (bash 3.2) as well as on Windows.

── The compose files are vendored, not fetched ──────────────────────────────
`vendor/*.yml` is committed and pinned to an upstream commit, so `up` needs no
network and a fresh clone reproduces exactly what you were running. Fetching at
`main` on every start meant the definition of your infrastructure could change
between two runs, and was invisible to review.

`refresh` is the only command that talks to the network. It re-pins to the
latest upstream commit, shows the diff, and leaves you to commit it — an
update becomes a deliberate act.

Fetching goes through the GitHub **contents API** rather than
raw.githubusercontent.com, because TLS-inspecting corporate proxies routinely
block the raw host while allowing api.github.com.
"""

import argparse
import base64
import datetime
import difflib
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
ENV_FILE = ROOT / ".env"
PINS = VENDOR / "VENDORED.md"

# project: the compose project name, which decides container prefixes.
# override: our own configuration layered on top — tracked, hand-written.
STACKS = {
    "langfuse": {
        "repo": "langfuse/langfuse",
        "path": "docker-compose.yml",
        "project": "langfuse",
        "override": True,
        "note": "traces, tokens and cost",
    },
    "agentcontrol": {
        "repo": "agentcontrol/agent-control",
        "path": "docker-compose.yml",
        "project": "agentcontrol",
        "override": False,
        "note": "step-level controls",
    },
    "agentregistry": {
        "repo": "agentregistry-dev/agentregistry",
        "path": "docker/docker-compose.yml",
        "project": "agentregistry",
        "override": False,
        "note": "catalogue and approvals; needs a VERSION release tag to start",
    },
}


def die(message: str) -> "typing.NoReturn":  # noqa: F821
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def compose_files(stack: str) -> list:
    """The -f arguments for a stack, base first so the override wins."""
    files = [VENDOR / f"{stack}.yml"]
    override = VENDOR / f"{stack}.override.yml"
    if override.exists():
        files.append(override)
    return files


def docker_compose(stack: str, *args: str) -> int:
    if not shutil.which("docker"):
        die("`docker` is not on PATH")
    cmd = ["docker", "compose", "-p", STACKS[stack]["project"]]
    if ENV_FILE.exists():
        # Explicit: compose otherwise looks for .env beside the compose file,
        # which is vendor/, not the repository root.
        cmd += ["--env-file", str(ENV_FILE)]
    for f in compose_files(stack):
        cmd += ["-f", str(f)]
    cmd += list(args)
    return subprocess.run(cmd, cwd=ROOT).returncode


def github_json(url: str):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code in (403, 407):
            hint = ("\n  a 403/407 here is usually a corporate proxy block page rather than"
                    "\n  GitHub refusing you. Fetch the file on an unrestricted machine and"
                    f"\n  drop it in {VENDOR}.")
        die(f"GitHub API returned {exc.code} for {url}{hint}")
    except urllib.error.URLError as exc:
        die(f"could not reach the GitHub API: {exc.reason}")


def latest_commit(repo: str, path: str) -> dict:
    commits = github_json(
        f"https://api.github.com/repos/{repo}/commits?path={path}&per_page=1")
    if not commits:
        die(f"no commits found for {repo}/{path} — has the file moved?")
    return {"sha": commits[0]["sha"], "date": commits[0]["commit"]["committer"]["date"]}


def fetch_at(repo: str, path: str, ref: str) -> str:
    doc = github_json(
        f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}")
    content = base64.b64decode(doc["content"]).decode()
    if content.lstrip()[:20].lower().startswith(("<!doctype", "<html")):
        die("the download returned HTML, not YAML — almost certainly a proxy block page")
    return content


def read_pins() -> dict:
    """Parse VENDORED.md's table back into {stack: {repo, path, commit, date}}."""
    pins = {}
    if not PINS.exists():
        return pins
    for line in PINS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if len(cells) >= 4 and cells[0] not in ("stack", "---"):
            pins[cells[0]] = {"repo": cells[1], "path": cells[2],
                              "commit": cells[3], "date": cells[4] if len(cells) > 4 else ""}
    return pins


def write_pins(pins: dict) -> None:
    rows = "\n".join(
        f"| `{name}` | `{p['repo']}` | `{p['path']}` | `{p['commit']}` | {p['date']} |"
        for name, p in sorted(pins.items()))
    PINS.write_text(f"""# Vendored compose files

These are upstream files, committed here and pinned to a specific commit, so
that `vendor_stacks.py up` needs no network and a fresh clone reproduces exactly
what was running. Do not edit them by hand — local changes belong in the
matching `*.override.yml`, which is ours.

| stack | repo | path | commit | upstream date |
|---|---|---|---|---|
{rows}

Re-pin to the latest upstream commit, review the diff, then commit it:

```
python3 scripts/vendor_stacks.py refresh <stack>
```
""", encoding="utf-8")


def cmd_refresh(stacks: list) -> None:
    pins = read_pins()
    for name in stacks:
        spec = STACKS[name]
        commit = latest_commit(spec["repo"], spec["path"])
        content = fetch_at(spec["repo"], spec["path"], commit["sha"])
        dest = VENDOR / f"{name}.yml"
        old = dest.read_text(encoding="utf-8") if dest.exists() else ""

        if old == content:
            print(f"  {name}: already at the latest content ({commit['sha'][:12]})")
        else:
            diff = list(difflib.unified_diff(
                old.splitlines(), content.splitlines(),
                fromfile=f"{name}.yml (vendored)", tofile=f"{name}.yml ({commit['sha'][:12]})",
                lineterm=""))
            VENDOR.mkdir(exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            print(f"  {name}: updated to {commit['sha'][:12]} ({len(diff)} diff lines)")
            for line in diff[:60]:
                print(f"    {line}")
            if len(diff) > 60:
                print(f"    ... {len(diff) - 60} more lines")

        pins[name] = {"repo": spec["repo"], "path": spec["path"],
                      "commit": commit["sha"], "date": commit["date"][:10]}
    write_pins(pins)
    print(f"\n  pins recorded in {PINS.relative_to(ROOT)} — review the diff and commit it.")


def cmd_up(stacks: list) -> None:
    failed = []
    for name in stacks:
        base = VENDOR / f"{name}.yml"
        if not base.exists():
            print(f"  skipping {name}: {base.relative_to(ROOT)} is missing "
                  f"(run: vendor_stacks.py refresh {name})")
            continue
        print(f"→ starting {name} ({STACKS[name]['note']})")
        # One stack failing must not stop the others — agentregistry, for
        # instance, needs a VERSION release tag that nothing here supplies.
        if docker_compose(name, "up", "-d") != 0:
            failed.append(name)
            print(f"  {name} failed to start — see the error above", file=sys.stderr)
    print()
    cmd_status(stacks)
    if "langfuse" in stacks and "langfuse" not in failed:
        port = env_value("LANGFUSE_PORT", "3300")
        print(f"\n  Langfuse   http://localhost:{port}")
        print(f"  sign in as {env_value('LANGFUSE_INIT_USER_EMAIL', '(LANGFUSE_INIT_USER_EMAIL)')}"
              f" with LANGFUSE_INIT_USER_PASSWORD from .env")


def cmd_down(stacks: list) -> None:
    for name in stacks:
        if (VENDOR / f"{name}.yml").exists():
            docker_compose(name, "down")


def cmd_status(stacks: list) -> None:
    if not shutil.which("docker"):
        die("`docker` is not on PATH")
    for name in stacks:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.project={STACKS[name]['project']}",
             "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True)
        rows = [r for r in result.stdout.splitlines() if r.strip()]
        print(f"  {name}: {len(rows)} container(s) running" if rows else f"  {name}: not running")
        for row in rows:
            container, _, status = row.partition("\t")
            print(f"    {container:34} {status}")


def env_value(key: str, default: str = "") -> str:
    if not ENV_FILE.exists():
        return default
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["up", "down", "status", "refresh"])
    parser.add_argument("stacks", nargs="*", default=[],
                        help=f"one or more of: {', '.join(STACKS)} (default: all)")
    args = parser.parse_args()

    stacks = args.stacks or list(STACKS)
    unknown = [s for s in stacks if s not in STACKS]
    if unknown:
        die(f"unknown stack(s): {', '.join(unknown)}. Known: {', '.join(STACKS)}")

    {"up": cmd_up, "down": cmd_down, "status": cmd_status, "refresh": cmd_refresh}[args.command](stacks)


if __name__ == "__main__":
    main()
