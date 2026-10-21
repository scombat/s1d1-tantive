# Maintainers' notes

This page shows how to develop, release and extend s1d1-tantive. Users do not need it: start from the
[README](README.md).

## Develop

```bash
uv sync --all-groups
```

```bash
uv run ruff check . && uv run pytest -m "not slow"
```

Two kinds of test need more: `slow` tests run the real model (`S1D1_TEST_MODEL_DIR=<snapshot of a variant>`, and
`S1D1_TEST_HUB_LOADER=<research repo>/release/s1d1-router` for the parity check with the Hub loader), `docker` tests
build and run the image. CI runs `ruff`, `actionlint`, `hadolint` and `pytest -m "not slow and not docker"`.

Dependency updates arrive from Dependabot (`.github/dependabot.yml`) every Monday: one pull request per ecosystem
(GitHub Actions, the Docker bases, the uv lockfile) groups the minor and patch bumps, majors come one by one, and pins
keep their SHA or digest. A merged update is not a release: the Trivy gate still decides when the next tag builds.

To build an image locally, from the Hub (default) or from a folder `weights/<variant>/` you provide
(`WEIGHTS_SRC=local`); either way every file is verified against `checksums/<variant>.json` and a mismatch fails the
build with the file name:

```bash
docker build --target variant --build-arg VARIANT=small-onnx-int8 -t tantive:small .
```

## Release

A version tag runs the `build` workflow (`.github/workflows/build.yml`):

1. hadolint, then the base image for amd64 (native) and arm64 (QEMU), pushed by digest, then its manifest list;
2. for each variant and platform, on that platform's base: build, SBOM (CycloneDX, run artifact), Trivy gate, push by digest;
3. per variant, the manifest list under the version tag, the keyless cosign signature and the two SBOM attestations on its digest;
4. SLSA level 3 provenance by the `slsa-github-generator` container workflow;
5. the `release` job creates the GitHub release of the tag with the `CHANGELOG.md` section of that version as notes
   (`tools/release_notes.py`; the job fails if the section is missing, and leaves an existing release alone).

A failed step stops the chain for that variant; no image is tagged before both platform images passed their scan.
There is no `latest` tag. To release: set the version in `pyproject.toml` (the only place: `__version__` reads it from
the package metadata), move the image references to the new tag (README, guides, `deploy/`, `CITATION.cff`; the deploy
tests compare them with the package version), add the `CHANGELOG.md` section, then:

```bash
git tag v<version> && git push --tags
```

**Rehearsal without a tag:** *Run workflow* on `build` with the variants to build (default `full` and `small`); it
publishes `0.0.0-dev.<sha>` images.

**Signing is keyless.** Nothing to set up: the `variant-manifest` job has `id-token: write`, and cosign signs with the
workflow's OIDC identity (Fulcio certificate, Rekor log entry), so no secret is involved. A rehearsal run is signed by
`build.yml@refs/heads/<branch>`, which the verification command in `docs/verify.md` rejects on purpose (tags only).
`cosign.pub` stays for the images up to 0.5.0, signed with the former key.

**Scan gate.** Trivy fails the build on HIGH or CRITICAL vulnerabilities that have a fix. An exception goes in
`.trivyignore` as `CVE-XXXX-NNNN  # until YYYY-MM-DD: reason`; a test fails once the date has passed.

## Refresh the pinned weights

After a release on the Hub, repin the four variants (dev tool, needs the network), review the diff of `checksums/`
and commit:

```bash
uv run python tools/checksums.py
```

## Add a format

1. One module in `src/tantive/adapters/` that exposes `router(app) -> APIRouter` and registers it with
   `@register("<name>")`: it only translates request and response shapes, the decisions belong to the core.
2. Import it in `src/tantive/adapters/__init__.py`, and add the name to `FORMATS` in `src/tantive/config.py`
   (that is what `S1D1_FORMATS` accepts).
3. A test next to the others (`tests/test_<name>.py`), and a section in `docs/formats.md`.

## Add a deploy target

1. A folder `deploy/<target>/` with the provider's manifest (some providers insist on a file at the root).
2. Its `README.md`, in the shape of the others: "This page shows how to deploy s1d1-tantive on <target>", what it
   creates, prerequisites and status, the button, the manual command, the variables; plus a `## Facts`
   block (image, RAM, status, what is still to do).
3. A button in the README's Deploy row (the provider's official one, or a shields.io badge that opens the guide
   until the target is ready; the guide says how to swap the link), and the target in `tests/test_deploy_buttons.py`.
4. Shape tests in `tests/test_deploy_<target>.py`.
