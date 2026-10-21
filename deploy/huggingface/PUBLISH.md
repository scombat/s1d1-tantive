# Deploy s1d1-tantive on Hugging Face Spaces

This page shows how to deploy s1d1-tantive on Hugging Face as a Docker Space named `scombat/s1d1-tantive`. The Space
card (`README.md`) and the `Dockerfile` in this folder are the whole Space: a single `FROM` the published `full`
image, which is public, so the Space builds without credentials.

**The README button opens this page** until the Space exists. Once it is published, replace the link of the
Hugging Face button in the root README (`deploy/huggingface/PUBLISH.md`) with
`https://huggingface.co/spaces/scombat/s1d1-tantive?duplicate=true`.

**Prerequisite (checked 2026-10-21 on the Hub docs, "Spaces Overview" and "Docker Spaces"):** creating or
duplicating a Docker Space needs a paid plan on the target account or organisation (PRO for a personal account,
Team or Enterprise for an organisation). The hardware it then runs on, CPU Basic (2 vCPU, 16 GB), is free. The
"Duplicate this Space" button therefore works for someone with such a plan.

## Facts

- Image: `ghcr.io/scombat/s1d1-tantive:0.6.0-full-onnx-int8`
- RAM: 16384 MiB (CPU Basic, free, checked 2026-10-21)
- Status: needs a one-time setup
- Still to do (you): publish the Space below (the duplicate button is a 404 until it exists); duplicating a Docker
  Space needs a paid plan.

## Deploy by hand (publish the Space)

```bash
hf auth login
# the secret is set at creation, before the first upload, so the public Space never runs without a key
hf repos create scombat/s1d1-tantive --type space --space-sdk docker --public --secrets S1D1_API_KEY=<key>
hf upload scombat/s1d1-tantive deploy/huggingface/README.md README.md --type space
hf upload scombat/s1d1-tantive deploy/huggingface/Dockerfile Dockerfile --type space
hf upload scombat/s1d1-tantive LICENSE LICENSE --type space
```

The key is set before the first upload on purpose: the Space is public, and the service checks a key only when
`S1D1_API_KEY` is set. Each push rebuilds and restarts the Space; the build is a single `FROM`, so it takes about as long as pulling the
1 GB image.

## Variables to set

- Variables (public, copied to duplicates): `S1D1_TIERS`, `S1D1_FORMATS`, `S1D1_DEADLINE_MS`: they override the
  defaults in the Dockerfile.
- Optional variable: `OTEL_EXPORTER_OTLP_ENDPOINT`, an OpenTelemetry collector for traces and metrics; not set = no
  export.
- **Secret** (not copied to duplicates): `S1D1_API_KEY`. A public Space answers anyone who finds its URL unless the
  key is set; the service checks a key only when `S1D1_API_KEY` is set.
- The Space sleeps when unused (free hardware), then wakes on the next request; the model loads in a few seconds.

## Upgrading

Edit the tag in the `Dockerfile` (`0.6.0-full-onnx-int8` → the new release) and upload it again.

## Button

The README button's final link is `https://huggingface.co/spaces/scombat/s1d1-tantive?duplicate=true`, the form used by the
official badges (`huggingface/badges`). No Hub documentation page describes the `duplicate=true` parameter, so the
same page also works by hand: three dots → "Duplicate this Space".
