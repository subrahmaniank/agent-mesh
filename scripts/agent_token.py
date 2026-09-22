#!/usr/bin/env python3
"""Issue the credential an agent presents to agentgateway.

    python3 scripts/agent_token.py init                 # once: make the keypair
    python3 scripts/agent_token.py issue research-assistant
    python3 scripts/agent_token.py issue ops-agent --ttl 86400

The token's `sub` claim becomes the Cedar principal. agentgateway validates the
signature, then copies `jwt.sub` into the agent identity header on its way to
cedar-shim — see `extAuthz.protocol.http.addRequestHeaders` in
agentgateway/config.yaml. That is the whole reason a client cannot forge an
identity: it never supplies one, and a header it sets by hand is not forwarded.

── Why Ed25519, and why openssl ─────────────────────────────────────────────
agentgateway accepts RSA, EC and OKP[Ed25519] JWKS keys, and explicitly rejects
symmetric ones:

    the key "..." uses an unsupported algorithm OctetKey(...)
    (supported: RSA, EC, OKP[Ed25519])

so a shared HMAC secret is not an option. Ed25519 is the one of the three whose
public key needs no parsing — the raw 32 bytes sit at the end of the DER — and
whose signature is used verbatim, with no DER-to-raw conversion. Signing is
delegated to `openssl pkeyutl`, which keeps this script dependency-free: the
Python standard library cannot sign Ed25519.

── This is a development issuer ─────────────────────────────────────────────
In production the JWKS comes from your identity provider and tokens are minted
by whatever already owns agent identity — here, the agent registry's approval
step. Nothing about agentgateway's configuration changes: point `jwks.file` at
the IdP's key set instead. The private key below never leaves auth/, which is
gitignored.
"""

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
AUTH_DIR = ROOT / "auth"
PRIVATE_KEY = AUTH_DIR / "agentmesh-dev.ed25519.pem"
JWKS_FILE = AUTH_DIR / "jwks.json"

ISSUER = "agentmesh-dev"
AUDIENCE = "agentmesh-gateway"
KID = "agentmesh-dev"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def openssl(*args: str, stdin: bytes = b"") -> bytes:
    result = subprocess.run(["openssl", *args], input=stdin, capture_output=True)
    if result.returncode != 0:
        sys.exit(f"openssl {' '.join(args)} failed:\n{result.stderr.decode()}")
    return result.stdout


def cmd_init(force: bool) -> None:
    if PRIVATE_KEY.exists() and not force:
        print(f"{PRIVATE_KEY} already exists (use --force to replace)")
        return
    AUTH_DIR.mkdir(exist_ok=True)
    openssl("genpkey", "-algorithm", "ed25519", "-out", str(PRIVATE_KEY))
    PRIVATE_KEY.chmod(0o600)

    # The DER SubjectPublicKeyInfo for Ed25519 is a fixed 44 bytes whose last 32
    # are the key itself — no ASN.1 parser required.
    der = openssl("pkey", "-in", str(PRIVATE_KEY), "-pubout", "-outform", "DER")
    if len(der) != 44:
        sys.exit(f"unexpected Ed25519 SPKI length {len(der)}, expected 44")

    JWKS_FILE.write_text(json.dumps({"keys": [{
        "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA",
        "use": "sig", "kid": KID, "x": b64url(der[-32:]),
    }]}, indent=2) + "\n")
    print(f"wrote {PRIVATE_KEY} (private, keep) and {JWKS_FILE} (public, mounted "
          f"into agentgateway)")
    print("restart the gateway to pick it up:  docker compose restart agentgateway")


def cmd_issue(agent_id: str, ttl: int) -> None:
    if not PRIVATE_KEY.exists():
        sys.exit(f"no signing key — run: python3 {sys.argv[0]} init")

    now = int(time.time())
    header = {"alg": "EdDSA", "typ": "JWT", "kid": KID}
    payload = {"sub": agent_id, "iss": ISSUER, "aud": AUDIENCE,
               "iat": now, "exp": now + ttl}

    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{b64url(json.dumps(payload, separators=(',', ':')).encode())}"

    # -rawin: Ed25519 signs the message directly, with no separate digest step.
    # It must come from a real file — a pipe fails with "unable to determine
    # file size for oneshot operation", because openssl needs the length up
    # front.
    with tempfile.NamedTemporaryFile(suffix=".jwt-signing-input") as handle:
        handle.write(signing_input.encode())
        handle.flush()
        signature = openssl("pkeyutl", "-sign", "-inkey", str(PRIVATE_KEY),
                            "-rawin", "-in", handle.name)
    print(f"{signing_input}.{b64url(signature)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="generate the dev signing key and JWKS")
    init.add_argument("--force", action="store_true", help="overwrite an existing key")

    issue = sub.add_parser("issue", help="mint a token for an agent id")
    issue.add_argument("agent_id", help="must match an AgentMesh::Agent in cedar/entities.json")
    issue.add_argument("--ttl", type=int, default=3600, help="seconds (default 3600)")

    args = parser.parse_args()
    if args.command == "init":
        cmd_init(args.force)
    else:
        cmd_issue(args.agent_id, args.ttl)


if __name__ == "__main__":
    main()
