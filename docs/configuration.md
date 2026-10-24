# Configure and size the service

This page shows how to configure s1d1-tantive with environment variables and how to size a deployment from what the
service measures about itself. Every variable is optional; an invalid value stops the start-up with a message that
names it.

## Variables

| variable | default | meaning |
|---|---|---|
| `S1D1_FORMATS` | `systemone,decisions,classify` | adapters to mount, among `systemone`, `decisions`, `chat`, `classify`, `predict` |
| `S1D1_MODEL_DIR` | `/model` | the weights folder (the images ship it) |
| `S1D1_THREADS`, `S1D1_INTER_OP_THREADS` | container CPUs (cgroup v2), 1 | ONNX Runtime threads |
| `S1D1_TIERS` | `TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0` | the native tier set, `NAME=upper bound,...` |
| `S1D1_DEFAULT_SIGMA` | 0.10 | width of the tier probabilities when `variant.json` carries no `sigma` (`/info` says which applies) |
| `S1D1_LONG_PROMPTS` | `head_tail` | what to run when a prompt exceeds the 1 024-token window: `head`, `tail`, `head_tail` or `chunks` (below) |
| `S1D1_CHUNK_TOKENS` | 1 024 | `chunks` only: tokens per piece, 128 to 1 024, special tokens included |
| `S1D1_CHUNK_OVERLAP` | 64 | `chunks` only: useful tokens shared by two consecutive pieces, below the piece size |
| `S1D1_CHUNK_MAX` | 8 | `chunks` only: most pieces per prompt, 1 to 32; the remainder is dropped and `truncated` is true |
| `S1D1_CHUNK_SCORE` | `max` | `chunks` only: how the pieces' complexity merges, `max`, `first`, `length_weighted`, `mean` or `median` |
| `S1D1_CHUNK_TASK` | `sum_logits` | `chunks` only: how the pieces' task merges, `sum_logits` (the summed logits) or `vote` (majority, ties to the larger logit) |
| `S1D1_DEADLINE_MS`, `S1D1_MAX_QUEUE` | 800, 32 | shed a request (503 + `Retry-After: 1`) when its expected wait exceeds the deadline or the queue is full |
| `S1D1_API_KEY` | none | when set, adapter routes need `Authorization: Bearer <key>` (or `x-api-key`) |
| `S1D1_LOG_PROMPTS` | 0 | 1 to write the classified text in the decision log line |
| `S1D1_CAPTURE_DIR`, `S1D1_CAPTURE_SAMPLE`, `S1D1_CAPTURE_MIN_MARGIN`, `S1D1_CAPTURE_MAX_MB`, `S1D1_CAPTURE_RETENTION_DAYS`, `S1D1_CAPTURE_QUEUE` | none, 1.0, empty, 100, empty, 10000 | keep the classified prompts in JSONL files, off unless the folder is set ([capture.md](capture.md)) |
| `S1D1_CAPTURE_SEND`, `S1D1_CAPTURE_URL`, `S1D1_CAPTURE_TOKEN`, `S1D1_CAPTURE_BATCH`, `S1D1_CAPTURE_SEND_SECONDS`, `S1D1_TERMS_VERSION` | 0, `https://s1d1.qoo.gy/ingest`, none, 100, 30, `v1` | opt-in: post the captured lines to the community ingestion; needs `S1D1_CAPTURE_DIR` ([capture.md](capture.md#send-to-the-community-ingestion)) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | none | enables OTLP traces and metrics (the standard `OTEL_*` variables apply) |
| `S1D1_HOST`, `S1D1_PORT`, `S1D1_ROOT_PATH` | `0.0.0.0`, 8788, empty | network; with `S1D1_ROOT_PATH=/api` the service answers `/api/v1/systemone` and `/v1/systemone` alike (so it works behind a proxy that keeps the prefix or one that strips it), `/ready`, `/health` and `/metrics` included; `/apix/...` is not the prefix |

Service routes, always on and never behind the key: `GET /health`, `GET /ready`, `GET /metrics` (Prometheus) and
`GET /info`. A public deployment should set `S1D1_API_KEY`.

## Prompts longer than the window

The model reads at most 1 024 tokens (CLS and SEP included, so 1 022 useful ones). A prompt up to that size is one
piece under every policy. Beyond it, `S1D1_LONG_PROMPTS` chooses: `head` keeps the first window, `tail` the last,
`head_tail` (the default) the first 511 and the last 511 useful tokens in one window, and `chunks` runs consecutive
windows of `S1D1_CHUNK_TOKENS` that overlap by `S1D1_CHUNK_OVERLAP`, up to `S1D1_CHUNK_MAX` of them. Pieces are cut on
token boundaries, CLS and SEP are put back around each, and the pieces of one prompt go through the model in a single
batched pass. `chunks` merges the pieces' outputs: the largest complexity (`max`), the summed task logits and the
largest `needs_tools` logit; `mean` and `median` are accepted for `S1D1_CHUNK_SCORE` but measured worse, so avoid them.
On 617 labelled prompts longer than the window, `head_tail` scored about 0.05 QWK above `head` for a single pass,
while `chunks` costs about 2.3 passes per long prompt and was no better than `head_tail`. Every piece counts in the predicted cost (the fixed cost per piece plus the
per-token cost), so a long prompt under `chunks` is shed sooner. Responses report `pieces` (see [formats.md](formats.md))
and `/info` the policy and the `chunks` settings.

## Tier names

`S1D1_TIERS` sets the names and upper bounds of the native tiers (a score up to a bound belongs to that tier; the
last tier takes everything above). LiteLLM's set, `SIMPLE=0.5,MEDIUM=0.6,COMPLEX=0.9,REASONING=1.0`, is always
recognised too, so a router can use either. The names in a LiteLLM router must match one of the two sets
([litellm.md](litellm.md)).

## Warm-up

At start-up the service classifies three synthetic texts of about 16, 256 and 1 024 tokens (one untimed pass, then
two timed ones each) and fits a linear cost, `fixed_ms + per_token_ms × tokens`. `/ready` answers 200 only once this
is done, a few seconds on the full variant: point the readiness and startup probes at it, never the liveness probe.
The fit measures what one pass costs on *this* container with the threads it really has, which is what the
scheduler needs to predict waits.

## Shedding by deadline

Inference runs on one worker thread, so a request waits behind the passes already accepted. Before admitting a
request the service predicts its wait; when that exceeds `S1D1_DEADLINE_MS`, or `S1D1_MAX_QUEUE` requests already
wait, it answers 503 with `Retry-After: 1` at once. For LiteLLM this is good news: a fast 503 sends the request to
the `fallback_tier` straight away, whereas a timeout counts towards the provider's circuit breaker, which then stops
calling the classifier for 30 seconds.

## Size from the `ready` line

The JSON line `{"event": "ready", ...}` printed after warm-up reports:

| field | meaning |
|---|---|
| `cpus`, `threads` | what the cgroup grants, and the ONNX threads in use |
| `fixed_ms`, `per_token_ms` | the fitted cost model |
| `predicted_ms_1024` | the cost of a full-window prompt |
| `sustainable_rps` | requests per second for a typical 64-token prompt, one replica |

If `predicted_ms_1024` exceeds `S1D1_DEADLINE_MS`, a `capacity_warning` line says so: full-window prompts will be
shed even on an idle replica. Give the replica more CPU, raise the deadline, or accept that the longest prompts go to
the fallback tier. Replicas needed: the expected request rate divided by `sustainable_rps`, with headroom for the
queue.

## Metrics and tracing

`GET /metrics` exposes `tantive_decisions_total` (by tier), `tantive_request_latency_seconds` (queue included),
`tantive_prompt_tokens` `tantive_in_flight_requests`, and with capture on `tantive_capture_written_total` and `tantive_capture_dropped_total`, and with sending on `tantive_capture_sent_total`, `tantive_capture_send_failed_total` and `tantive_capture_send_rejected_total` and the gauge `tantive_capture_send_pending_bytes`. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export the same metrics
and traces over OTLP; `/health`, `/ready` and `/metrics` are not traced. Logs are JSON lines on standard output.
