# Getting started

This page shows how to run s1d1-tantive, check that it is ready and make a first call on each format.

## Run the image

```bash
docker run --rm -p 8788:8788 ghcr.io/scombat/s1d1-tantive:0.4.0-small-onnx-int8
```

The image carries its weights under `/model`; there is one image per variant (`full`, `compact`, `small`, `tiny`:
the tag ends with `-<variant>-onnx-int8`) and nothing is downloaded at start. Every tag is a multi-arch manifest, so
Docker takes the right platform for the host. By default only three formats are mounted; to mount all of them:

```bash
docker run --rm -p 8788:8788 -e S1D1_FORMATS=systemone,decisions,chat,classify,predict \
  ghcr.io/scombat/s1d1-tantive:0.4.0-small-onnx-int8
```

To harden it, add `--read-only --tmpfs /tmp`; the container already runs as a non-root user. `docker compose up --build`
runs the small variant with every format.

## Run it from source (developers)

```bash
hf download scombat/s1d1-router-6src-small-onnx-int8 --local-dir weights/small-onnx-int8
```

```bash
uv sync && S1D1_MODEL_DIR=weights/small-onnx-int8 uv run python -m tantive
```

`S1D1_MODEL_DIR` holds a variant as downloaded from the Hub (one repository per variant): `variant.json`,
`tokenizer.json`, `tokenizer_config.json`, the `.onnx` file and, for pruned vocabularies, `vocab_map.npy`.

## Wait for `/ready`

```bash
curl localhost:8788/ready
```

It answers `{"ready": true}` with 200 once the model is loaded and warmed up (a few seconds), and 503 before. Point
your platform's readiness probe at it. `/health` answers as soon as the process is up (liveness only).

## Read `/info`

```bash
curl -s localhost:8788/info
```

| field | what it tells you |
|---|---|
| `variant`, `revision`, `onnx_file` | which weights are loaded, and the Hub commit they come from |
| `formats` | the adapters mounted (`S1D1_FORMATS`) |
| `tiers` | the native tier set: each name with its upper bound on the 0 to 1 score |
| `sigma`, `sigma_source` | the width of the tier probabilities, from `variant.json` or the default |
| `max_length`, `long_prompts`, `chunks` | the token window (1 024), the policy for longer prompts and the `chunks` settings |
| `deadline_ms`, `max_queue` | when a request is shed with a 503 |
| `threads`, `inter_op_threads`, `cost` | ONNX Runtime threads and the fitted cost per request and per token |

## First call on each format

```bash
curl -s localhost:8788/v1/systemone -H 'content-type: application/json' -d '{
  "state": "Write a haiku about rain",
  "questions": {"tier": {"type": "choice", "criteria": {"TRIVIAL": "", "LOW": "", "MEDIUM": "", "HIGH": "", "EXPERT": ""}}}}'
```

```bash
curl -s localhost:8788/v1/decisions -H 'content-type: application/json' -d '{
  "input": "Write a haiku about rain",
  "questions": [{"type": "choice", "name": "tier", "choices": [{"value": "TRIVIAL"}, {"value": "LOW"}, {"value": "MEDIUM"}]}]}'
```

```bash
curl -s localhost:8788/v1/chat/completions -H 'content-type: application/json' -d '{
  "messages": [{"role": "user", "content": "Write a haiku about rain"}]}'
```

```bash
curl -s localhost:8788/classify -H 'content-type: application/json' -d '{"input": "Write a haiku about rain"}'
```

```bash
curl -s localhost:8788/predict -H 'content-type: application/json' -d '{"inputs": "Write a haiku about rain"}'
```

A route whose format is not in `S1D1_FORMATS` answers 404. What each answer contains: [formats.md](formats.md).

## Logs

Logs are JSON lines on standard output. `starting` and `ready` (with the fitted cost model) come first; then one
`decision` line per classified text: route, format, tier, score, task, `needs_tools`, tokens, latency. The text itself
is not logged unless `S1D1_LOG_PROMPTS=1`. Metrics are at `/metrics`; see [configuration.md](configuration.md).
