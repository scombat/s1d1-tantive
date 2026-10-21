# Security policy

## Supported versions

Only the latest minor version of s1d1-tantive receives security fixes. Older
images stay available but are not patched: move to the latest tag.

## Reporting a vulnerability

Please do not open a public issue. Report privately, by either of:

- a private security advisory on the GitHub repository (*Security* tab, *Report a vulnerability*,
  https://github.com/scombat/s1d1-tantive/security/advisories/new);
- an email to `<security contact>`.

Give the version or image tag, what you did, what you saw and what you expected. We intend to acknowledge a report
within 7 days and to tell you what we plan to do about it; there is no bug bounty.

## Scope

In scope: the service (`src/tantive`), the Docker images published on GHCR, the build chain in `.github/workflows` and
the deployment templates in `deploy/`.

Out of scope: the behaviour of the model itself (wrong or biased classifications, adversarial prompts). That belongs to
the model card on the Hugging Face Hub. Vulnerabilities in a dependency are welcome when the image ships an affected
version that the Trivy gate did not catch.

## Verifying what you run

Images are signed keyless and carry SLSA provenance and an SBOM: see [docs/verify.md](docs/verify.md).
