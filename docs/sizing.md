# Size a deployment

This page shows how to size a deployment: how many replicas, how much memory, which variant and which deadline, with
a calculation you can redo yourself. The service also measures itself at start-up: when you can run one replica, its
`ready` line gives the same numbers ([configuration.md](configuration.md#size-from-the-ready-line)).

## 1. What one pass costs

One request is one pass through the model, and a pass costs about

```
cost_ms = fixed_ms + per_token_ms × tokens
```

Measured on an Apple M4 Max with ONNX Runtime int8, per replica (one inference thread pool, one process):

| variant | fixed_ms | per_token_ms | 8 tokens | 180 tokens | 977 tokens | RAM |
|---|---|---|---|---|---|---|
| `full` | 3 | 0.20 | 5 ms | 33 ms | 204 ms | 2 GiB |
| `small` | 2 | 0.105 | 3 ms | 21 ms | 106 ms | 1 GiB |
| `compact` | not measured | | | | | not measured (plan `full`'s) |
| `tiny` | not measured | | | | | 512 MiB |

The cost is close to linear in tokens (a prompt is cut at 1 024 tokens; under `chunks`, count every piece, see
[configuration.md](configuration.md#prompts-longer-than-the-window)). The two coefficients are rounded fits of the
measured points: use your own `fixed_ms` and `per_token_ms` from the `ready` line when you have them.

**Cloud factor.** A cloud vCPU is slower than an M4 core: one Azure core is worth about a half to a third of one.
Multiply the table by 2 to 3 (plan with 3) for a 1-vCPU replica. Measure on the real platform before you commit.

## 2. Replicas

| step | formula |
|---|---|
| mean tokens per prompt | measure it: `tantive_prompt_tokens` on `/metrics`. Test prompts: p50 62, p90 456 |
| cost of a pass | `fixed_ms + per_token_ms × mean tokens`, times the cloud factor |
| throughput of a replica | `1000 / cost_ms` requests per second (one process does one pass at a time) |
| replicas for the peak | `peak_rps / (throughput × 0.7)`, rounded up; 0.7 leaves room for bursts and the queue |
| replicas to deploy | the larger of that and **3** (rolling updates, one zone down) |

`peak_rps` is the peak rate of *classifier calls*, not of LLM requests. With LiteLLM's `classification_mode:
user_turn` the router classifies the user's turn rather than every message in the conversation, so an agent loop that
sends many calls for one question costs one classification: count user turns, not LLM calls.

## 3. The deadline

`S1D1_DEADLINE_MS` (default 800) is how long a request may wait for its turn before the service answers 503.

- Keep it **below the classifier timeout in LiteLLM** (1000 ms recommended to start), minus the network round-trip:
  a fast 503 sends the request to the `classifier_fallback`, a timeout counts against LiteLLM's circuit breaker.
- Keep it **above `predicted_ms_1024`** (the cost of a full-window prompt, in the `ready` line), or a
  `capacity_warning` says those prompts will always be shed.
- Cloud estimate of `predicted_ms_1024`: `(fixed_ms + per_token_ms × 1024) × 3`: `small` 327 ms, `full` 624 ms.

## 4. Variant, CPU and GPU

| need | choice |
|---|---|
| lowest cost and latency | `small` (about half the cost of `full`) or `tiny` on a small host |
| the largest model, when cost matters less | `full` |
| latency of a single request too high | more vCPUs per replica (`S1D1_THREADS` follows the quota) before more replicas |
| `full` fits neither the deadline nor the budget | step down to `small` |

The images are CPU-only. No GPU image is provided and none was measured: scale out on CPU first.

## Example 1: pilot, 10 users

Ten users on LiteLLM, at most one classification per second at peak, `small` on Azure Container Apps (factor 3),
mean 150 tokens:

| step | value |
|---|---|
| cost | `3 × (2 + 0.105 × 150)` = 53 ms |
| throughput | `1000 / 53` = 19 req/s per replica |
| replicas for the peak | `1 / (19 × 0.7)` → 1 |
| replicas to deploy | **3** (HA minimum), 1 vCPU and 2 GiB each (the smallest Container Apps size that holds 1 GiB) |
| deadline | 800 ms under a 1000 ms LiteLLM timeout |

## Example 2: API at 100 req/s

100 classifications per second at peak, mean 150 tokens, cloud factor 3:

| | `small` | `full` |
|---|---|---|
| cost | 53 ms | `3 × (3 + 0.20 × 150)` = 99 ms |
| throughput | 19 req/s | 10 req/s |
| replicas | `100 / (19 × 0.7)` → **8** | `100 / (10 × 0.7)` → **15** |
| RAM in total | 8 GiB | 30 GiB |

Take `small` unless you need `full`'s model. Then check with the `ready` line of one
replica (`sustainable_rps` is for a 64-token prompt, so it is higher than your mean), and watch
`tantive_request_latency_seconds` and the 503 rate on `/metrics` under real load.
