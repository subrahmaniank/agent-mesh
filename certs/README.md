# Corporate root CAs

Drop any TLS-inspecting proxy's root certificate here as `*.crt` (PEM content,
`.crt` extension — `update-ca-certificates` ignores `.pem`).

Images built from this repo copy this directory into their trust store before
running `pip install`. Without it, a machine behind Zscaler, Netskope, Palo Alto
or similar fails the build with:

```
SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] ...'))
ERROR: Could not find a version that satisfies the requirement temporalio
```

That is the proxy presenting its own certificate for `pypi.org`; the container's
trust store has never seen the issuer. Adding the CA is the correct fix —
`--trusted-host` would "work" by switching verification off, which hides a real
MITM just as effectively as it hides the corporate one.

To populate it on Linux:

```bash
cp /etc/pki/ca-trust/source/anchors/*.pem certs/   # RHEL/Fedora/WSL
rename .pem .crt certs/*.pem                       # or cp with the new name
# Debian/Ubuntu: /usr/local/share/ca-certificates/*.crt
# macOS: security find-certificate -a -p -c '<issuer>' /Library/Keychains/System.keychain
```

Certificates are **not committed** (see `.gitignore`) — they are specific to your
network, and a teammate on a different one needs their own. The directory itself
is tracked so the `COPY` never fails on a machine that needs no CA at all.
