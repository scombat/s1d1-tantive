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
| `S1D1_MAX_INPUT_TOKENS` | empty | tokens passed to the model at most, CLS and SEP included: empty for the window (as before), an integer of 128 or more, or `auto` (below); the rest of a longer text is ignored, never refused |
| `S1D1_DEADLINE_MS`, `S1D1_MAX_QUEUE` | 800, 32 | shed a request (503 + `Retry-After: 1`) when its expected wait exceeds the deadline or the queue is full |
| `S1D1_MAX_BODY_BYTES` | 2 097 152 (2 MiB) | largest request body, 65 536 to 33 554 432; a larger body is answered 413 |
| `S1D1_CACHE_TTL_S`, `S1D1_CACHE_MAX_ENTRIES` | 0, 10 000 | keep the model's outputs for each text in memory for that many seconds, at most that many entries; 0 turns the cache off (below) |
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

Only the part a policy can keep is tokenised. The text is first cut at 8 characters per kept token (the first
8 × 1 022 characters under `head`, the last ones under `tail`, 8 × 511 at each end under `head_tail`, tokenised apart,
and 8 × `S1D1_CHUNK_MAX` × `S1D1_CHUNK_TOKENS` under `chunks`), then cut exactly on tokens as above. Real text runs 1.5
to 3 characters per token, so the kept tokens are those of the whole text, and a 2 MiB text is tokenised in about 1 ms
instead of 300 (Apple M4 Max). A text of more than 8 characters per token (a run of newlines or of `=`) keeps fewer
tokens than the policy allows, and still reports `truncated`. `tantive_tokenize_seconds` on `/metrics` shows the time.

## The input limit

A long text is never refused: the only size error left is the body (`S1D1_MAX_BODY_BYTES`, 413). What the model reads
is bounded by `S1D1_MAX_INPUT_TOKENS`, in tokens passed to the model with CLS and SEP, the unit of the window:

| value | limit | logged at start-up |
|---|---|---|
| empty (default) | the window (1 024), or `S1D1_CHUNK_MAX` pieces under `chunks`: as before | `capacity_warning` if a full window is predicted beyond the deadline, as before |
| an integer, 128 or more | that, capped to what the policy can pass (the window, or `S1D1_CHUNK_MAX` × `S1D1_CHUNK_TOKENS` under `chunks`) | `input_limit_capped` when capped; `capacity_warning` if its predicted cost exceeds `S1D1_DEADLINE_MS` |
| `auto` | the largest multiple of 64, from 128 to that cap, whose predicted cost fits in **half** of `S1D1_DEADLINE_MS`, computed once after warm-up | `capacity_warning` if even 128 does not fit |

With a limit `L`, a longer text keeps `L − 2` useful tokens: the first ones under `head`, the last ones under `tail`,
half and half under `head_tail`. Under `chunks` the limit counts every piece, overlaps and CLS/SEP included: pieces
run while the total stays within `L`, the last one shortened to the room left and not run under 128 tokens. A text
between `L` and the window is now cut too, and reports `truncated`. Half of the deadline lets a request that fills the
limit wait behind another one of the same size and still answer in time, so with `auto` an idle replica never answers
503 for the size of a text: a 503 then means load. `auto` depends on the machine and on the warm-up: use it to find
the value, read `max_input_tokens` in the `ready` line, then set that integer in production so every replica keeps
the same tokens. The effective value is in the `ready` line, `/info` (`max_input_tokens`: `setting`, `effective`,
`source`, `predicted_ms`) and `/metrics` (`tantive_max_input_tokens`, by `source`: `window`, `user` or `auto`).

A larger `S1D1_MAX_BODY_BYTES` costs little CPU (the extraction, the cut and the tokenisation stay bounded), but the
body is held in memory and parsed, several times its size, for each request in flight: keep it well under the memory
of the image (512 MiB for `tiny`).

## The cache

Agents send the same text again and again: a loop re-sends the user's turn with every tool call. With
`S1D1_CACHE_TTL_S` above 0 (300 for agent traffic), the service keeps the model's outputs for each classified text in
memory and answers a repeat without a pass. The cache is off by default.

- **The key** is a SHA-256 of the text as extracted (after LiteLLM's last marker, before the cut, without
  normalisation: two texts that differ by a space are two entries) and of what the outputs depend on: the service
  version, the weights, `S1D1_LONG_PROMPTS`, the input limit in effect and, under `chunks`, its settings.
- **The value** is the model's outputs, never a tier: tiers are computed again on every request, so another
  `S1D1_TIERS`, other options in a question or another LiteLLM schema answer correctly from the same entry. A response
  from the cache is the response a new pass gives, except `latency_ms`.
- **Lifetime.** An entry lives `S1D1_CACHE_TTL_S` seconds from its pass; reading it does not extend it. Beyond
  `S1D1_CACHE_MAX_ENTRIES` the oldest entry leaves first. About 650 bytes per entry: 7 MiB at 10 000 entries.
- **Before the queue.** The cache answers before the tokenisation and the admission, so a stored entry answers 200
  even with the queue full. Identical requests that arrive while the first one's pass runs wait for that pass instead
  of entering the queue, and get its answer (or its error); a client that leaves does not stop the pass for the
  others. A shed request (503) leaves nothing behind: the next identical one tries again.
- **Privacy.** Neither the text nor its digest is kept anywhere else or written to a log, a metric or `/info` (the
  digest of a short prompt can be found back by dictionary); the entries are lost on restart. `S1D1_LOG_PROMPTS=1`
  keeps its effect. A decision answered from the cache is not captured again ([capture.md](capture.md)).

The decision line gains `cache`: `hit` (a stored entry), `shared` (the pass of an identical request in flight) or
`miss` (a new pass), absent when the cache is off. `/info` reports `cache` (`ttl_s`, `max_entries`), and `/metrics`
`tantive_cache_hits_total` (by `source`: `stored` or `in_flight`), `tantive_cache_misses_total` (passes started),
`tantive_cache_evictions_total` (by `reason`: `capacity` or `expired`) and the gauge `tantive_cache_entries`. A decision
answered from the cache still counts in `tantive_decisions_total`, `tantive_request_latency_seconds` and
`tantive_prompt_tokens`, with its own (near zero) latency: to size the passes, subtract the hits.

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
the `classifier_fallback` (the router's default model) straight away, whereas a timeout counts towards the provider's
circuit breaker, which then stops calling the classifier for 30 seconds.

## Size from the `ready` line

The JSON line `{"event": "ready", ...}` printed after warm-up reports:

| field | meaning |
|---|---|
| `cpus`, `threads` | what the cgroup grants, and the ONNX threads in use |
| `fixed_ms`, `per_token_ms` | the fitted cost model |
| `predicted_ms_1024` | the cost of a full-window prompt |
| `max_input_tokens`, `max_input_tokens_source`, `predicted_ms_limit` | the input limit in effect, where it comes from (`window`, `user`, `auto`) and the cost of a text that fills it |
| `max_body_bytes` | the largest request body |
| `cache_ttl_s`, `cache_max_entries` | the cache settings (0: off) |
| `sustainable_rps` | requests per second for a typical 64-token prompt, one replica |

If `predicted_ms_limit` exceeds `S1D1_DEADLINE_MS` (with the default limit: if `predicted_ms_1024` does), a
`capacity_warning` line says so: prompts that fill the limit will be shed even on an idle replica. Set `S1D1_MAX_INPUT_TOKENS=auto` (or a smaller integer), give the replica more
CPU, or raise the deadline. Replicas needed: the expected request rate divided by `sustainable_rps`, with headroom for the
queue.

## Metrics and tracing

`GET /metrics` exposes `tantive_decisions_total` (by tier), `tantive_request_latency_seconds` (queue included),
`tantive_prompt_tokens`, `tantive_tokenize_seconds`, `tantive_truncated_requests_total` (decisions on a text of which
some part was not passed), the gauge `tantive_max_input_tokens`, `tantive_in_flight_requests`, with the cache on the
four `tantive_cache_*` series ([above](#the-cache)), and with capture on `tantive_capture_written_total` and `tantive_capture_dropped_total`, and with sending on `tantive_capture_sent_total`, `tantive_capture_send_failed_total` and `tantive_capture_send_rejected_total` and the gauge `tantive_capture_send_pending_bytes`. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export the same metrics
and traces over OTLP; `/health`, `/ready` and `/metrics` are not traced. Logs are JSON lines on standard output.
