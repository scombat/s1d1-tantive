# Pick a configuration for your case

This page shows how to configure the service for six common cases: copy the block of the closest one and adjust.
Sizes come from [sizing.md](sizing.md). Images are `ghcr.io/scombat/s1d1-tantive:0.8.1-<variant>-onnx-int8`.

## 1. LiteLLM Enterprise pilot on Azure Container Apps

*About ten users, one classification per second at most, LiteLLM 1.106 or later.*

- Image: `small`. Target: [Azure](../deploy/azure/README.md), 3 replicas of 1 vCPU and 2 GiB.

```bash
S1D1_FORMATS=systemone,decisions,classify
S1D1_TIERS=TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0
S1D1_DEADLINE_MS=800
S1D1_API_KEY=<secret>
OTEL_EXPORTER_OTLP_ENDPOINT=<your collector>
```

Why: `small` is plenty for a pilot, and three replicas survive a rolling update. The deadline sits under the 1000 ms
classifier timeout in LiteLLM, so an overloaded replica answers 503 and the request falls back at once.
Next: [litellm.md](litellm.md).

## 2. Internal API at high throughput

*Around 100 classifications per second at peak, LiteLLM or a decision client in front.*

- Image: `small` (8 replicas at 1 vCPU, see [sizing.md](sizing.md#example-2-api-at-100-reqs)). Target: [AWS](../deploy/aws/README.md) or [Google Cloud Run](../deploy/gcp/README.md) behind a load balancer.

```bash
S1D1_FORMATS=decisions
S1D1_THREADS=1
S1D1_LONG_PROMPTS=head_tail
S1D1_DEADLINE_MS=600
S1D1_MAX_QUEUE=16
S1D1_API_KEY=<secret>
OTEL_EXPORTER_OTLP_ENDPOINT=<your collector>
```

Why: one thread per 1-vCPU replica keeps the cost model honest, and a short deadline and queue shed load early so
the balancer retries on another replica. Mount only the format you use. Next: [configuration.md](configuration.md).

## 3. Homelab on a Mac (arm64), behind llama-swap or alone

*One machine, a handful of users, Apple silicon.*

- Image: `full` (the arm64 image runs natively, no emulation). Target: `docker run` or `compose.yaml`.

```bash
S1D1_FORMATS=decisions,chat,classify
S1D1_THREADS=4
S1D1_DEADLINE_MS=1500
```

Why: a Mac core is two to three times faster than a cloud vCPU, so `full` costs about 35 ms at 150 tokens and one
replica carries the load. `chat` lets llama-swap or any OpenAI-style client address the service as a model
(`s1d1-classifier`); stand-alone, LiteLLM uses `decisions`. Next: [getting-started.md](getting-started.md).

## 4. Free demo on Render or a Hugging Face Space

*A public link to try the routing, no traffic to speak of.*

- Image: `tiny` (512 MiB). Target: [Render](../deploy/render/README.md) or a [Hugging Face Space](../deploy/huggingface/PUBLISH.md) (not published yet).

```bash
S1D1_FORMATS=systemone,decisions,classify
S1D1_LONG_PROMPTS=head
S1D1_DEADLINE_MS=3000
S1D1_MAX_QUEUE=8
S1D1_API_KEY=<secret>
```

Why: free tiers give a fraction of a vCPU, so the deadline is generous and the queue short; a public demo should
carry a key. Cold starts sleep the service: expect the first call after a pause to wait for `/ready`.
Next: [getting-started.md](getting-started.md).

## 5. Closed network, no internet access

*A cluster that pulls only from an internal registry.*

- Image: any variant, mirrored by digest. Target: your own Kubernetes or Docker hosts.

```bash
S1D1_FORMATS=decisions,classify
S1D1_DEADLINE_MS=800
S1D1_LOG_PROMPTS=0
```

Why: the image holds the weights (`/model`) and downloads nothing at start, so it runs offline. Verify on a connected
machine first (signature, provenance, then `python -m tantive.verify` on the weights), copy the image to the internal
registry, and pin it by digest. Next: [verify.md](verify.md).

## 6. LiteLLM older than 1.106

*No `oss_classifier` in your LiteLLM.*

- Image: `small`. Target: any, next to LiteLLM.

```bash
S1D1_FORMATS=chat
S1D1_TIERS=SIMPLE=0.5,MEDIUM=0.6,COMPLEX=0.9,REASONING=1.0
S1D1_DEADLINE_MS=800
```

Why: the `chat` adapter answers like an OpenAI-compatible model, so the router can use `classifier_type: llm` with
the service as the classifier; the tier names must be the ones in your router. Next:
[formats.md](formats.md#chat-completions).

## 7. What routing can save (an illustration, not a measurement)

An extrapolation with assumed numbers, not a measurement. Example policy (the operator picks the models; a class is a
kind of model): `trivial` and `low` go to a cheap model, `medium` to a mid one, `high` to a strong one, `expert` to a
strong one with extended reasoning.

Hypothetical traffic: the only measured figure is that 69 % of prompts are `trivial` or `low`, in the naturally
sampled part of the training data (27,871 prompts; see "Estimated upside" in the
[model card](https://huggingface.co/scombat/s1d1-router)). The rest is assumed: 15 % `medium`, 10 % `high`, 6 % `expert`.
Indicative prices per million tokens, assumed and dated 2026-10-31 (prices change): a cheap model at 1 $/M, a mid
model at 5 $/M, a strong model at 15 $/M, the same tokens per request.

| Class | Share | Price (strong = 100) | Contribution |
|---|---|---|---|
| cheap | 69 % | 6.7 | 4.6 |
| mid | 15 % | 33.3 | 5.0 |
| strong | 10 % | 100 | 10.0 |
| strong, extended reasoning | 6 % | 100 | 6.0 |

All on the strong model is 100; the routed budget is about 26, a saving of about 74 % under these assumptions.

What this ignores: the cheap model's answer quality (not measured); the classifier's own cost, small but not zero
(`small`: 5.8 ms per prompt and 56 MB, M4 Max, batch 1, CPU, from the s1d1-bench profile); output tokens, which often
dominate the bill; the extra tokens of extended reasoning (priced here like the strong model); and real traffic, which
can differ a lot. A tier error toward a lower tier costs answer quality, not money. For the fidelity of the tiers see
[s1d1-bench](https://github.com/scombat/s1d1-bench). Its `base-v1` split is not used as a traffic mix: it is built
by strata of 20 prompts each, so it says nothing about how often each tier occurs.
