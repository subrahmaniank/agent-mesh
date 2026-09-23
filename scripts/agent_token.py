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

── Why Ed25519 ──────────────────────────────────────────────────────────────
agentgateway accepts RSA, EC and OKP[Ed25519] JWKS keys, and explicitly rejects
symmetric ones:

    the key "..." uses an unsupported algorithm OctetKey(...)
    (supported: RSA, EC, OKP[Ed25519])

so a shared HMAC secret is not an option. Ed25519 is the one of the three whose
public key needs no parsing — the raw 32 bytes sit at the end of the DER — and
whose signature is used verbatim, with no DER-to-raw conversion.

── Signing works on any OS ──────────────────────────────────────────────────
Preferred backend is the `cryptography` package, which ships prebuilt wheels for
Windows, macOS and Linux:

    python3 -m pip install -r scripts/requirements.txt

If it is not importable, this falls back to shelling out to the `openssl`
binary, so an existing Unix machine keeps working untouched. The two produce
identical tokens — same key file, same JWKS, same signature algorithm — so you
can switch backends without reissuing anything.

Windows has neither by default, which is why `cryptography` is the documented
path rather than openssl.

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
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
AUTH_DIR = ROOT / "auth"
PRIVATE_KEY = AUTH_DIR / "agentmesh-dev.ed25519.pem"
JWKS_FILE = AUTH_DIR / "jwks.json"
ENV_FILE = ROOT / ".env"

ISSUER = "agentmesh-dev"
AUDIENCE = "agentmesh-gateway"
KID = "agentmesh-dev"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    BACKEND = "cryptography"
except ImportError:  # pragma: no cover - depends on the machine, not the code
    BACKEND = "openssl" if shutil.which("openssl") else None


def _require_backend() -> None:
    if BACKEND is None:
        sys.exit(
            "no Ed25519 backend available.\n"
            "  Install the dependency (works on Windows, macOS and Linux):\n"
            "    python3 -m pip install -r scripts/requirements.txt\n"
            "  or make the `openssl` binary available on PATH."
        )


def openssl(*args: str) -> bytes:
    result = subprocess.run(["openssl", *args], capture_output=True)
    if result.returncode != 0:
        sys.exit(f"openssl {' '.join(args)} failed:\n{result.stderr.decode()}")
    return result.stdout


def generate_key(destination: pathlib.Path) -> None:
    """Write a new Ed25519 private key in PEM form."""
    if BACKEND == "cryptography":
        pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        destination.write_bytes(pem)
    else:
        openssl("genpkey", "-algorithm", "ed25519", "-out", str(destination))


def public_key_bytes(source: pathlib.Path) -> bytes:
    """The raw 32-byte Ed25519 public key, for the JWKS `x` parameter."""
    if BACKEND == "cryptography":
        private = serialization.load_pem_private_key(source.read_bytes(), password=None)
        return private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    # The DER SubjectPublicKeyInfo for Ed25519 is a fixed 44 bytes whose last 32
    # are the key itself — no ASN.1 parser required.
    der = openssl("pkey", "-in", str(source), "-pubout", "-outform", "DER")
    if len(der) != 44:
        sys.exit(f"unexpected Ed25519 SPKI length {len(der)}, expected 44")
    return der[-32:]


def sign(source: pathlib.Path, message: bytes) -> bytes:
    if BACKEND == "cryptography":
        private = serialization.load_pem_private_key(source.read_bytes(), password=None)
        return private.sign(message)
    # -rawin: Ed25519 signs the message directly, with no separate digest step.
    # It must come from a real file — a pipe fails with "unable to determine
    # file size for oneshot operation", because openssl needs the length up
    # front.
    with tempfile.NamedTemporaryFile(suffix=".jwt-signing-input", delete=False) as handle:
        handle.write(message)
        temp = handle.name
    try:
        return openssl("pkeyutl", "-sign", "-inkey", str(source), "-rawin", "-in", temp)
    finally:
        os.unlink(temp)


def cmd_init(force: bool) -> None:
    _require_backend()
    if PRIVATE_KEY.exists() and not force:
        print(f"{PRIVATE_KEY} already exists (use --force to replace)")
        return
    AUTH_DIR.mkdir(exist_ok=True)
    generate_key(PRIVATE_KEY)
    try:
        PRIVATE_KEY.chmod(0o600)
    except (NotImplementedError, PermissionError):
        # Windows has no POSIX mode bits; the file inherits directory ACLs.
        pass

    JWKS_FILE.write_text(json.dumps({"keys": [{
        "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA",
        "use": "sig", "kid": KID, "x": b64url(public_key_bytes(PRIVATE_KEY)),
    }]}, indent=2) + "\n")
    print(f"wrote {PRIVATE_KEY} (private, keep) and {JWKS_FILE} (public, mounted "
          f"into agentgateway)")
    print(f"backend: {BACKEND}")
    print("restart the gateway to pick it up:  docker compose restart agentgateway")


def cmd_issue(agent_id: str, ttl: int, write_env: bool) -> None:
    _require_backend()
    if not PRIVATE_KEY.exists():
        sys.exit(f"no signing key — run: python3 {sys.argv[0]} init")

    now = int(time.time())
    header = {"alg": "EdDSA", "typ": "JWT", "kid": KID}
    payload = {"sub": agent_id, "iss": ISSUER, "aud": AUDIENCE,
               "iat": now, "exp": now + ttl}

    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    token = f"{signing_input}.{b64url(sign(PRIVATE_KEY, signing_input.encode()))}"

    if write_env:
        # Saves a shell substitution, which is the one step that differs between
        # POSIX shells and PowerShell.
        set_env_value("GATEWAY_TOKEN", token)
        print(f"GATEWAY_TOKEN written to {ENV_FILE} (sub={agent_id}, expires in {ttl}s)")
    else:
        print(token)


def set_env_value(key: str, value: str) -> None:
    """Insert or replace `key=value` in .env, leaving everything else alone."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="generate the dev signing key and JWKS")
    init.add_argument("--force", action="store_true", help="overwrite an existing key")

    issue = sub.add_parser("issue", help="mint a token for an agent id")
    issue.add_argument("agent_id", help="must match an AgentMesh::Agent in cedar/entities.json")
    issue.add_argument("--ttl", type=int, default=3600, help="seconds (default 3600)")
    issue.add_argument("--write-env", action="store_true",
                       help="write it to .env as GATEWAY_TOKEN instead of printing it")

    args = parser.parse_args()
    if args.command == "init":
        cmd_init(args.force)
    else:
        cmd_issue(args.agent_id, args.ttl, args.write_env)


if __name__ == "__main__":
    main()
