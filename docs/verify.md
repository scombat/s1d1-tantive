# Verify an image

This page shows how to check that a s1d1-tantive image is the one this repository built, from the weights up. Each
check proves one thing; run the ones you need. The examples use the `small` variant: replace the tag with yours
(`<version>-<variant>-onnx-int8`).

## 1. The signature

From 0.6.0 images are signed keyless: there is no key to trust, the signature is bound to the identity of this
repository's `build` workflow (a short-lived Sigstore certificate) and recorded in the public Rekor log.

```bash
cosign verify ghcr.io/scombat/s1d1-tantive:0.8.1-small-onnx-int8 \
  --certificate-identity-regexp '^https://github.com/scombat/s1d1-tantive/\.github/workflows/build\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

A pass proves the image was signed by the `build` workflow of this repository, run for a version tag. The signature is
on the manifest list, so one check covers both platforms (amd64 and arm64). The SBOM attestations verify the same way
with `cosign verify-attestation --type cyclonedx` and the same two identity flags.

### Images before keyless (0.5.0 and older)

Versions up to 0.5.0 were signed with the project's private key; `cosign.pub` at the root of this repository verifies
them, and only them:

```bash
cosign verify --key cosign.pub ghcr.io/scombat/s1d1-tantive:0.5.0-small-onnx-int8
```

## 2. The build provenance

```bash
slsa-verifier verify-image ghcr.io/scombat/s1d1-tantive@sha256:<digest> \
  --source-uri github.com/scombat/s1d1-tantive --source-tag v0.8.1
```

A pass proves the image was built by this repository's `build` workflow at that tag, with SLSA level 3 provenance
generated outside the build's own control. Use the list digest (`docker buildx imagetools inspect <image>` prints it).

## 3. The weights

```bash
docker run --rm --entrypoint python ghcr.io/scombat/s1d1-tantive:0.8.1-small-onnx-int8 \
  -m tantive.verify /model /model/checksums.json
```

A pass proves every file under `/model` matches `checksums.json`: the Hub repository, the commit, the sha256 of each
large file and the git blob sha1 of each small one. The build ran this check before and after copying the weights;
`tantive.verify` uses the standard library only, so you can replay it anywhere. The pinned values for each variant are
in this repository's `checksums/` folder.

## 4. The labels and `/info`

```bash
docker inspect --format '{{json .Config.Labels}}' ghcr.io/scombat/s1d1-tantive:0.8.1-small-onnx-int8
```

The labels `io.s1d1.model.repo`, `io.s1d1.model.revision` and `io.s1d1.model.sha256` say which Hub files the image
carries; the running service reports the same revision in `/info` (`revision`).

## The software bill of materials

Each platform image has a CycloneDX SBOM, attached to the image as a keyless cosign attestation and kept as a run artifact
(`sbom-<variant>-<arch>`) of the `build` workflow. Every build also passes a Trivy scan for fixable HIGH and CRITICAL
vulnerabilities before it is tagged.

## Use a private registry

Pull with a read-only token (`docker login ghcr.io -u <user> --password-stdin`), or copy the image into your own
registry (`az acr import --source ghcr.io/scombat/s1d1-tantive:0.8.1-small-onnx-int8 ...`) and replay check 3 there.
