# Deploy s1d1-tantive on Railway

This page shows how to deploy s1d1-tantive on Railway from a **template** that you create once in its dashboard.
There is no manifest file to commit: Railway's *config as code* (`railway.json` / `railway.toml`) has no image field,
so this page records the exact fields to enter. Checked on docs.railway.com on 2026-10-21.

**The README button opens this page** until the template exists. Once it is created, replace the link of the
Railway button in the root README (`deploy/railway/README.md`) with `https://railway.com/deploy/<template-code>`,
the code being the one on the template's page.

## Facts

- Image: `ghcr.io/scombat/s1d1-tantive:0.6.0-small-onnx-int8`
- RAM: 1024 MiB (Trial plan, the most a service gets on it; checked 2026-10-21)
- Status: needs a one-time setup
- Still to do (you): the template is not created yet, so the button below has no id; create it once (below) and
  put its code in the link of the Railway button in the root README.

The images are public (an anonymous pull works), so the template needs no registry credentials.

## Free tier and variant

Railway's plans page (2026-10-21): the **Free** plan is $0 with $1 of credit a month and caps a service at 0.5 GB of
RAM and 1 vCPU; the **Trial** gives a one-time $5 and caps a service at 1 GB and 2 vCPU; **Hobby** is $5 a month and
allows up to 48 GB. The template runs the **small** variant, which needs 1 GiB, so it runs on Trial or Hobby, not on
Free. On Free only `tiny` (0.5 GB) is possible, and it uses about 450 MiB at idle, which leaves little room: use
Trial or Hobby.

## Variables to set (create the template, once)

In the Railway dashboard: workspace settings → Templates → **New Template** → **Add New** → **Docker Image**.

| field | value |
|---|---|
| Image | `ghcr.io/scombat/s1d1-tantive:0.6.0-small-onnx-int8` |
| Variable `PORT` | `8788` (Railway routes public traffic to `PORT`; the service listens on 8788 by default) |
| Variable `S1D1_TIERS` | `TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0` (must match the tier names your gateway sends) |
| Variable `S1D1_FORMATS` | `systemone,decisions,classify` |
| Variable `S1D1_DEADLINE_MS` | `800` |
| Variable `OTEL_EXPORTER_OTLP_ENDPOINT` | optional: an OpenTelemetry collector URL for traces and metrics; leave the value empty (or do not add the variable) for no export |
| Variable `S1D1_API_KEY` | `${{secret(32)}}`: Railway generates a random key per deployment, so a public domain is never open |
| Settings → Healthcheck path | `/ready` (the liveness path `/health` exists too, but Railway has one check and traffic must wait for warm-up) |
| Settings → Healthcheck timeout | 300 s (Railway's default) is enough for the model to load |
| Settings → Restart policy | On failure |
| Networking | enable a public domain only if the gateway is outside Railway; inside a project use the private name |

Then **Create Template** and copy the template URL from the Templates page.

## Deploy with the button

```markdown
[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/<template-code>)
```

`railway.com/button.svg` is the image used by Railway's own templates (it answers 200 on 2026-10-21; the official
docs pages above do not print the snippet). Template pages have the form `railway.com/deploy/<code>`;
`railway.com/template/<code>` redirects to it. Replace `<template-code>` once the template exists; until then the
README button opens this page instead.

## Deploy by hand

New project → Deploy → Docker Image → the image above, then set the same variables and the healthcheck path in the
service settings.
