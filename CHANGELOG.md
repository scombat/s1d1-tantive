# Changelog

## Unreleased

- Image references move to 0.7.0 in the README, the guides, the deployment templates and the Space files, so the templates
  pull images with the capture. `CITATION.cff` drops its `version` and `date-released`: the citation names the repository,
  the release page names the version.
- The version is read from the package metadata: `tantive.__version__` comes from `pyproject.toml` through
  `importlib.metadata`, the only place a release sets it.
- The licence parameters are now generic: `Licensed Work` is `s1d1-tantive` without a version, and the `Change Date` is
  four years from the date each version is first publicly distributed (the BSL 1.1 template wording). Licensor, Additional
  Use Grant and Change License are unchanged.
- The `SECURITY.md` contact test is a plain test: the contact is filled, so its `xfail` marker goes.
- Batches answer per item: `POST /v1/systemone/batch`, and `/classify` and `/predict` given a list, answer each valid
  item as before and put `{"error": {"message", "type"}}` in the slot of an item the single route would refuse (blank or
  too long text, shed), instead of failing the whole batch. 200 when at least one item succeeded, 422 when every item
  failed; body-level errors are unchanged. Metrics and `decision` lines count the items that ran. Guide: the Batch
  section of `docs/formats.md`.
- chat-completions refuses `"stream": true` with a 400 in the usual error shape instead of ignoring it: this endpoint
  does not stream, a classification is one response.
- Dependabot keeps the GitHub Actions, the Docker bases and the uv lockfile up to date: weekly, on Monday, one grouped
  pull request per ecosystem for the minor and patch updates, at most five open per ecosystem (`.github/dependabot.yml`).
- Contribution pages: `CONTRIBUTING.md` (issues, the change workflow, the commit style, the licence of contributions),
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), issue forms for bugs and questions with blank issues off and
  vulnerabilities sent to the security advisory, and a pull request template. The README links both pages.

## 0.7.0 — 2026-10-21

- Capture of the classified prompts, off by default: `S1D1_CAPTURE_DIR` writes one JSON line per decision (prompt,
  tier, score, task, confidence, latency, variant) to daily files, with size rotation, sampling, a margin filter for
  the doubtful cases and a retention. One writer thread behind a bounded queue, never the request;
  `tantive_capture_written_total` and `tantive_capture_dropped_total`; `/info` reports `capture`. The text goes to
  these files and nowhere else. Guide: `docs/capture.md`.
- Capture mounts: the Azure template takes `captureShareName`, `captureStorageAccount` and `captureStorageKey` (Azure
  Files on `/capture`); the AWS template takes `CaptureFileSystemId` (EFS on `/capture`). The deploy templates keep
  pulling the 0.6.0 images by default, which have no capture: set `image` or `Image` to a 0.7.0 tag to use it.
- Checksums repinned: the Hub `variant.json` files now carry `sigma`, `max_length`, `score_center` and `score_scale`.

- Railway removed from the deployment targets: its free tier cannot run the small variant, the button needs a
  template created in a console that proved unreliable, and Render covers the free PaaS case. The 0.5.0 files stay
  in the history.
- README: a "What you get" section and the Hub card's hero image; `llms.txt` for assistants; `docs/sizing.md` with
  the sizing calculation written out; `docs/use-cases.md` with six ready-made configurations; a Claude Code skill
  (`.claude/skills/deploy-s1d1-tantive`) that sizes and configures a deployment from those two pages.
- CI: the `release` job creates the GitHub release of a tag from its CHANGELOG section, after provenance.
- AWS Lambda: `deploy/aws/template.lambda.yaml` (container function, Lambda Web Adapter, provisioned concurrency
  to avoid cold starts, invocation permission restricted to a source ARN).
- Terraform: one `main.tf` per target under `deploy/<target>/terraform/` (Azure, AWS ECS and Lambda, Cloud Run,
  Heroku, Render), validated with Terraform 1.16; Hugging Face has no maintained provider.
- Build: certifi 2026.10.19 (a Trivy finding on the first v0.6.0 run).

## 0.6.0 — 2026-10-21

- Decision service for the s1d1 router: System One and OpenAI Decisions for LiteLLM's Auto Router, chat-completions,
  vLLM-style `/classify` and TEI-style `/predict`, on one ONNX int8 model (four variants), with warm-up, load shedding,
  Prometheus metrics and OpenTelemetry.
- Long-prompt policies (`head_tail` by default, `head`, `tail`, `chunks`) with merge strategies and a `pieces` count in
  every response.
- One image per variant for amd64 and arm64, weights checked against committed checksums, built with SLSA 3 provenance
  and signed keyless with cosign (identity recorded in the public Rekor log); `docs/verify.md` shows how to check.
- Deployment files for Azure, AWS, Google Cloud Run, Heroku, Render, Railway and Hugging Face Spaces, all running the
  published image with the same `S1D1_*` variables.
- Documentation (README, getting started, LiteLLM, formats, configuration, verification), `SECURITY.md`, gitleaks and
  Trivy in CI, and `tools/ghcr_cleanup.sh` for maintainers.
- Versions 0.1.0 to 0.5.0 were private pre-releases; their history was squashed on 2026-10-21.

## Before 0.6.0 (private pre-releases)

### 0.6.0 — 2026-10-21 (development notes, as written before the squash)

- Long prompts: `S1D1_LONG_PROMPTS` is now `head_tail` by default (the first 511 and the last 511 useful tokens in one
  window; a prompt of up to 1 022 useful tokens is unchanged under every policy), after measuring the strategies on
  617 labelled prompts longer than the window (`head_tail` about +0.05 QWK on `head` for a single pass). `head` (the
  0.5.0 behaviour), `tail` and `chunks` are available. `chunks` runs consecutive windows (`S1D1_CHUNK_TOKENS` 1 024,
  `S1D1_CHUNK_OVERLAP` 64, `S1D1_CHUNK_MAX` 8) in one batched pass and merges them (`S1D1_CHUNK_SCORE` `max`,
  `S1D1_CHUNK_TASK` `sum_logits`, needs_tools `max`; `mean` and `median` are accepted but discouraged). Pieces are cut on
  token ids; the predicted cost counts every piece. Behaviour change: with the default, a prompt beyond the window
  now sends the model its head and its tail instead of its head only.
- `pieces` (the windows a prompt ran in) in System One `usage` and `routing`, Decisions `usage`, `/classify` `usage`,
  the `X-S1D1-Pieces` header of `/v1/chat/completions` and `/predict`, and the `decision` log line; `/info` lists
  `long_prompts` and the `chunks` settings. `Model.encode` keeps every id of the text (the policy cuts); `OnnxModel.run`
  still keeps the head, as the Hub loader does.
- Images are signed keyless: `cosign sign` and `cosign attest` use the build workflow's OIDC identity (`id-token: write`
  on the `variant-manifest` job, `--yes`, no `COSIGN_*` secret), and the signature is recorded in the public Rekor log.
  `docs/verify.md` shows the keyless `cosign verify` first; `cosign.pub` stays for the images up to 0.5.0.
- `SECURITY.md`: supported versions, private reporting, 7-day acknowledgement, scope. The contact is still the
  placeholder `<security contact>`, to fill before the repository goes public (its test is an expected failure).
- `gitleaks` runs in CI on every push and pull request over the whole history (image pinned by digest,
  `.gitleaks.toml` allow-lists only the public `cosign.pub`); a run on the 74 commits so far found nothing.
- Deployment: `OTEL_EXPORTER_OTLP_ENDPOINT` as an optional variable in every non-Azure target; the Cloud Run README
  says the key is stored as a plain variable and how to move it to Secret Manager; the Space card's `license_link`
  points to a `LICENSE` file uploaded with the Space.

### 0.5.0 — 2026-10-21

- Deployment files for six more targets, each running the published image (`0.4.0-<variant>`) with the same `S1D1_*`
  variables, port 8788 and `/ready` as the readiness path, each with a README (fields, free tier checked on the
  provider's pages on 2026-10-21, the manual equivalent) and shape tests. The README has a "Deploy" row of one
  button per target (a guide's `Facts` section holds its status):
  - Hugging Face Space (`deploy/huggingface`, full variant on CPU Basic): `needs a one-time setup`, once the Space is published
    (steps in `PUBLISH.md`); duplicating a Docker Space needs a paid Hugging Face plan.
  - Railway (`deploy/railway`, small): `needs a one-time setup`, once the template is created in Railway's dashboard; Railway's
    config as code cannot deploy an image, so there is no file, only the fields to enter.
  - Render (`render.yaml`, `deploy/render`, tiny on the Free plan): `needs public repository`.
  - Google Cloud Run (`deploy/gcp`, small): `needs public repository`; the button is reached with `dir=deploy/gcp`
    so the root `app.json` stays Heroku's.
  - AWS (`deploy/aws`, ECS Fargate CloudFormation template, full): `needs a template upload (S3)`; no load balancer.
  - Heroku (`app.json`, `heroku.yml`, `deploy/heroku`, small on a Standard-2X dyno): `needs public repository`.
  Azure (0.4.0) is in the row as `needs public repository`.
- The GHCR packages `s1d1-tantive` and `s1d1-tantive-base` are public as of 2026-10-21 (anonymous pull verified), so
  none of the new targets needs registry credentials (the Azure template of 0.4.0 still asks for them); the
  repository is still private. The manifests keep pointing at the 0.4.0
  images, which exist; the 0.5.0 images appear when the tag is pushed.
- None of the new targets was deployed for real (no accounts or CLIs here); the template files were checked with
  Render's JSON schema, `cfn-lint`, `trivy config` and by running the Heroku and Hugging Face containers locally.
  The service itself is unchanged since 0.1.2.

### 0.4.0 — 2026-10-21

- Deployment example for Azure: `deploy/azure/azuredeploy.json`, one ARM template (`Microsoft.App/containerApps`,
  API version 2026-07-01, the latest stable on the Microsoft Learn reference) that runs an image in an existing
  Container Apps environment, with the GHCR credentials as parameters (`registryPassword` and `apiKey` are
  `securestring`), internal or external ingress chosen explicitly (no default), probes, HTTP scaling and the
  `S1D1_*` settings. `tiers` and `environmentId` have no default either.
- README: a "Deploy to Azure" section with the portal button (it works once the repository is public) and the
  `az deployment group create` command that works now; `deploy/azure/README.md` has the parameter table.
- The template is not validated against Azure (no `az` here, and Trivy has no check for container apps): the first
  real deployment is its test. The service itself is unchanged since 0.1.2.

### 0.3.1 — 2026-10-20

- Build chain: the SBOM attestation step read `sbom-<variant>-<arch>.json`, but `anchore/sbom-action` names the
  file inside its artifact after the artifact itself, without extension; the v0.3.0 run built, scanned, pushed and
  assembled all eight platform images and both manifest lists, then failed at this step (no attestation, no
  provenance). The step now uses the artifact's file name and fails early with a listing when it is missing.

### 0.3.0 — 2026-10-20

- Every image is multi-architecture: linux/amd64 and linux/arm64 under the same tag (a manifest list). The build
  workflow builds each platform separately (arm64 under QEMU), scans and SBOMs each platform image, pushes them by
  digest, then creates the list, signs its digest with cosign, attests both SBOMs on it and generates the SLSA
  provenance for it. The Dockerfile is unchanged: its two pinned bases are manifest-list digests.
- The service itself is unchanged since 0.1.2.

### 0.2.0 — 2026-10-20

Images and supply chain (spec § 6), tranche 2. The service itself is unchanged since 0.1.2.

- One multi-stage `Dockerfile`: a base image with the service (python:3.12-slim and uv pinned by digest, venv only,
  no pip, no uv, no Hub client, user `tantive` uid 10001) and one image per ONNX int8 variant with its weights
  under `/model`. Weights come from the Hub at a commit pinned in `checksums/<variant>.json`, or from a local
  folder; every file is verified by sha256 (LFS) or git blob sha1 before and after the copy, and a mismatch fails
  the build with the file name.
- `python -m tantive.verify DIR CHECKSUMS` (standard library) replays that check anywhere, including inside the
  image; `tools/checksums.py` refreshes the pinned checksums from the Hub.
- `compose.yaml` for a local run; `.hadolint.yaml` (no rule ignored); `.trivyignore` with dated, reasoned
  exceptions enforced by a test.
- `build.yml`: tag-triggered chain, hadolint → base → variants (SBOM, Trivy gate on fixable HIGH/CRITICAL, push,
  cosign key signature and SBOM attestation) → SLSA 3 provenance; `ci.yml` gains actionlint and hadolint.
- Sizes measured locally (arm64, uncompressed as Docker reports them): base 457 MB, small 595 MB, full 1.04 GB,
  after removing huggingface-hub and hf-xet from the venv and using `uvicorn` without its `[standard]` extras
  (uvloop, httptools, websockets, watchfiles are not needed: one worker, no WebSocket, no reload). Trivy on the
  small image: no fixable HIGH/CRITICAL finding.
- Actions in the workflows are pinned to commit SHAs (tag in a comment); the SLSA generator stays on its version
  tag, as its verifier requires.

Known limits: the final image keeps a shell (a distroless or Chainguard base is a later evaluation); amd64 only
in CI; the first real run of the chain is the v0.2.0 tag.

### 0.1.2 — 2026-10-20

Fixes from the user's review of 0.1.1; no change to the wire formats.

- The window, score centre and scale are read from `variant.json` (`max_length`, `score_center`, `score_scale`),
  with the training values as fallback, and shown in `/info`. The Hub's `variant.json` files do not carry them
  yet; they will be added from the research repository together with `sigma`.
- `S1D1_DEFAULT_SIGMA` (float > 0, default 0.10) replaces the hard-coded fallback when `variant.json` has no
  `sigma`; a `sigma` from the weights always wins.
- ONNX Runtime session options written out explicitly (optimisation level, execution mode).
- The `ready` line reports `cpus`, `threads`, `deadline_ms`, `max_queue`, `predicted_ms_1024` and
  `sustainable_rps`; a `capacity_warning` says when a full-window prompt would exceed the deadline.
- README: operating notes on the warm-up, the shedding by deadline and sizing from the `ready` line.
- OTLP exporters take the endpoint from the settings (plus the signal path) unless the standard per-signal
  variable is set; the test suite no longer leaves a provider retrying exports at exit.

### 0.1.1 — 2026-10-20

Fixes from the review of 0.1.0; no change to the wire formats.

- The 2 MiB body limit also holds for chunked bodies (counted on the stream, 413 past the limit).
- One tokenisation per request, on a tokeniser thread off the event loop (`Model.encode` / `run_encoded`).
- The `tier_subset` warning is logged once per distinct option set, not on every request.
- With `min_confidence`, `noul` answers carry `abstention: "unevaluated"` and the threshold, like Laya.
- The task softmax is computed once per decoded output.
- One tracer provider per process, reused by later apps (`tracing_already_configured` when the endpoint differs).
- A chat `tier` enum with fewer than two names answers a clear 400 instead of the S1D1_TIERS message.
- CI runs `uv sync --locked`, so an outdated `uv.lock` fails the build.

### 0.1.0 — 2026-10-19

First service: the ONNX loader (parity with the Hub's `s1d1_router.py` checked on ten prompts), the question
catalogue (tier choice, task choice, score, tool predicate; 422 outside it), calibrated tier probabilities, the
single-thread engine with deadline shedding, and five adapters behind `S1D1_FORMATS`: System One (LiteLLM Auto
Router, with a contract test against LiteLLM's own models), OpenAI Decisions, chat-completions, vLLM `/classify`,
TEI `/predict`. JSON logs without prompt text, OpenTelemetry traces and metrics, Prometheus `/metrics`.

- The chosen tier is the tier of the score by its bounds (the nearest present tier on a subset); the calibrated
  probabilities feed `probabilities` and `confidence` only.
- Metrics are OpenTelemetry instruments defined once; `/metrics` is served by `opentelemetry-exporter-prometheus`
  (same publisher as the SDK already used) so the OTLP export and the Prometheus scrape carry the same five series.

Latency, small int8 variant, M4 Max, one client, HTTP round trip on `/v1/systemone` (50 requests per length;
docs/21 § 5.1 measured the previous raw server at 3.3 / 20.6 / 105.7 ms p50):

| prompt tokens | p50 | p99 |
|---|---|---|
| 8 | 6.7 ms | 9.6 ms |
| 162 | 25.0 ms | 32.4 ms |
| 876 | 110.9 ms | 149.6 ms |

Not in this release (next tranches): images and supply-chain CI, the Container Apps module and LiteLLM recipe,
the `chunks` long-prompt policy, sigma measured per variant on the Hub (the service falls back to 0.10).
