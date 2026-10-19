# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's **"Report a vulnerability"** button on
the repository's Security tab (private vulnerability reporting). Do not open a public issue or
pull request for a suspected vulnerability.

Include what you found, how to reproduce it, the affected version or commit, and the impact you
expect. We aim to acknowledge reports within three working days and to agree a disclosure date
with you once a fix is ready.

## Supported versions

Hawser is pre-1.0. Security fixes land on `main` and in the next release; there are no
backports to older 0.x releases.

## Scope

In scope: the `hawser-ai` and `hawser-server` packages, the `hawser-synth` workspace package,
the web app, the Docker image and the Compose stack in this repository.

Areas we particularly care about:

- tenant isolation in `hawserd` (one API key reading or changing another tenant's data);
- authentication and API-key handling;
- tool permissions and side-effect approvals (a model or document causing a tool to run that
  the skill, request or policy did not permit);
- input admission (decompression bombs, malformed PDFs or images, oversized inputs);
- prompt-injection paths that change policy, permissions or acceptance (documents and OCR text
  are untrusted data and must never do so);
- webhook signing and artifact access (signed URLs, cross-tenant reads).

Out of scope: vulnerabilities in model providers, in third-party dependencies without a
Hawser-specific exploit path, and denial of service through deliberately expensive but
policy-permitted requests (use budgets and limits).

## Hardening notes for operators

See [docs/operator/configuration.md](docs/operator/configuration.md#secrets). In short: never
expose a server started with `--dev` or `HAWSERD_DEV_API_KEY`; keep provider keys on worker
processes only; terminate TLS in front of `hawserd`.
