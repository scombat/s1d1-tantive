# Request and response formats

This page shows how to call each format s1d1-tantive speaks: a minimal request, its answer, and how the answer maps to
the model's outputs. `S1D1_FORMATS` mounts them (default `systemone,decisions,classify`; also `chat`, `predict`).

## The questions the service answers

System One and Decisions send *questions* about a text. The catalogue is fixed:

| question | how to write it | answer |
|---|---|---|
| tier | a `choice` whose options are a tier set: the native set, the LiteLLM set, or a subset of one | the tier of the score, with a probability per option |
| score | a `score` over 2 to 10 ordered levels | a continuous position on the levels, with probabilities |
| task | a `choice` whose options are task names (19, listed in the 422 message) | the most likely task, with probabilities |
| tools | a predicate named `needs_tools`, `tools` or `tool_use`, or instructions that mention a tool | the probability that the text needs a tool |
| anything else | | **422**, with the catalogue in the message: the service never invents an answer |

The tier is the tier of the score by its bounds (the nearest present tier on a subset). The probabilities are a Gaussian
of width sigma centred on the score, cut by the tier intervals; the confidence is 1 minus their normalised entropy.

## System One

For: LiteLLM's Auto Router (`oss_classifier`, provider `laya`) and any System One client. `POST /v1/systemone`.

```bash
curl -s localhost:8788/v1/systemone -H 'content-type: application/json' -d '{
  "state": "Write a Terraform module",
  "questions": {"tier": {"type": "choice", "criteria": {"TRIVIAL": "", "LOW": "", "MEDIUM": "", "HIGH": "", "EXPERT": ""}}}}'
```

```json
{"answers": {"tier": {"type": "choice", "choice": "MEDIUM", "probabilities": {"TRIVIAL": 0.0, "LOW": 0.3, "MEDIUM": 0.6, ...}, "confidence": 0.6}},
 "usage": {"input_tokens": 7, "output_tokens": 0, "pieces": 1}, "routing": {"latency_ms": 4.5, "complexity_score": 0.485, "truncated": false, "pieces": 1}}
```

`pieces` counts the windows the prompt ran in (more than 1 only under `S1D1_LONG_PROMPTS=chunks`); `truncated` says some
text was in no window. `state` is a string, a list or an object (flattened). A `score` takes `criteria` as a list of
levels, a predicate is `{"type": "noul"}` and answers `{"noul": 0.001}`. `min_confidence` adds the abstention fields.

## OpenAI Decisions

For: clients written against the OpenAI Decisions format, without LiteLLM. `POST /v1/decisions`.

```bash
curl -s localhost:8788/v1/decisions -H 'content-type: application/json' -d '{
  "input": "Prove that there are infinitely many primes",
  "questions": [{"type": "choice", "name": "tier", "choices": [{"value": "TRIVIAL"}, {"value": "LOW"}, {"value": "MEDIUM"}]},
                {"type": "predicate", "name": "needs_tools"}]}'
```

```json
{"answers": [{"type": "choice", "name": "tier", "choice": "MEDIUM", "probabilities": [{"value": "TRIVIAL", "probability": 0.0}, ...], "confidence": 0.6},
             {"type": "predicate", "name": "needs_tools", "probability": 0.001}]}
```

Answers come back in question order; `usage` also carries `pieces`, the windows the input ran in. `input` is a string or a list of messages. A `score` question uses `levels`
(`[{"label": "low"}, ...]`). Up to 128 questions.

## chat-completions

For: LiteLLM before 1.106 (`classifier_type: llm`) and quick tests. Add `chat` to `S1D1_FORMATS`.
`POST /v1/chat/completions` (also `/chat/completions`); `GET /v1/models` lists `s1d1-classifier`.

```bash
curl -s localhost:8788/v1/chat/completions -H 'content-type: application/json' -d '{
  "messages": [{"role": "user", "content": "Write a Terraform module for an Azure Container App"}]}'
```

```json
{"choices": [{"message": {"role": "assistant", "content": "{\"tier\": \"MEDIUM\"}"}, "finish_reason": "stop"}], ...}
```

The last user message is classified. The tier set is the one named by the `tier` enum of `response_format`'s JSON
schema, else the native set. The response also carries `X-S1D1-Score`, `X-S1D1-Task`, `X-S1D1-Needs-Tools` and
`X-S1D1-Pieces` and `X-S1D1-Latency-Ms` headers.

## vLLM `/classify`

For: tools that expect a vLLM classification endpoint. `POST /classify` with `{"input": "text"}` or a list (up to 32).

```bash
curl -s localhost:8788/classify -H 'content-type: application/json' -d '{"input": "hi"}'
```

```json
{"data": [{"index": 0, "label": "TRIVIAL", "probs": [0.01, 0.0002, ..., 0.0042, 0.001], "num_classes": 21}]}
```

`usage.pieces` sums the windows the inputs ran in. `label` is the native tier. `probs` holds 21 decoded values: index 0 the complexity score, 1 to 19 the task
probabilities (in the order of the 422 message), 20 the `needs_tools` probability.

## TEI `/predict`

For: tools that expect a Text Embeddings Inference classifier. Add `predict` to `S1D1_FORMATS`.
`POST /predict` with `{"inputs": "text", "raw_scores": false}`; a list of inputs gives a list of lists.

```bash
curl -s localhost:8788/predict -H 'content-type: application/json' -d '{"inputs": "Tell me a joke"}'
```

```json
[{"score": 0.9998, "label": "writing"}, {"score": 0.156, "label": "complexity"}, {"score": 0.001, "label": "needs_tools"}, ...]
```

21 entries sorted by score: `complexity`, the 19 tasks and `needs_tools`. `raw_scores: true` gives the raw outputs
(complexity in tier units, task logits, the `needs_tools` logit). `X-S1D1-Pieces` gives the windows the inputs ran in.

## Batch

`POST /v1/systemone/batch` (`states`, answered as `{"results": [...]}`), `/classify` and `/predict` given a list answer
per item: a valid item as on its own, a refused one (blank or too long text, shed) as `{"error": {...}}` in its slot,
with its `index` in `/classify`. 200 when an item succeeded, 422 when all failed. Limits: 32 items, 50 000 characters
each, body 2 MiB; invalid JSON, more items, a bigger body or a question outside the catalogue refuse the whole request.

```json
{"results": [{"answers": {"tier": {"type": "choice", "choice": "LOW", ...}}, ...}, {"error": {"message": "no text to classify", "type": "invalid_request_error"}}]}
```

## Limits and error codes

Body 2 MiB, text 50 000 characters, 32 inputs per batch route, 64 questions (128 for Decisions).

| code | meaning |
|---|---|
| 400 | invalid body |
| 401 | `S1D1_API_KEY` is set and the request carries no matching `Authorization: Bearer` or `x-api-key` |
| 404 | the format is not mounted |
| 413 | body or text too large |
| 422 | question outside the catalogue; every item of a batch failed |
| 503 | shed: the service is overloaded; retry (`Retry-After: 1`) |
