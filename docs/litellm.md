# Use it with LiteLLM

This page shows how to make LiteLLM's Auto Router choose a model with s1d1-tantive, through the admin GUI or through
the API. Both routes make the same two registrations: the classifier as a model, then the router that uses it.

You need a running service reachable from LiteLLM ([getting-started.md](getting-started.md)) and LiteLLM 1.106 or
later.

## Which tier names

LiteLLM's complexity router has four tiers, `SIMPLE`, `MEDIUM`, `COMPLEX` and `REASONING`, which the service always
recognises, whatever its `S1D1_TIERS` (the native five by default: `TRIVIAL`, `LOW`, `MEDIUM`, `HIGH`, `EXPERT`).
When the classifier fails or answers a name the router does not know, the request goes to the `classifier_fallback`;
with `default_model`, that is the router's default model. If routing seems stuck on one model, check the names first.

## 1. Register the classifier

**GUI.** *Models* → *Add Model*. Mode: *Decisions*. Provider: `laya`. Model: `multilingual`. API base: the service URL.
Price: 0 for input and output.

**API.**

```bash
export LITELLM=https://litellm.example.internal
export LITELLM_KEY=<litellm admin key>
export S1D1_API_BASE=https://s1d1-tantive.internal.example   # the service, as LiteLLM reaches it
```

```bash
curl -sS "$LITELLM/model/new" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' -d '{
  "model_name": "s1d1-classifier",
  "litellm_params": {"model": "laya/multilingual", "api_base": "'"$S1D1_API_BASE"'"},
  "model_info": {"mode": "decisions", "input_cost_per_token": 0, "output_cost_per_token": 0}
}'
```

If the service has `S1D1_API_KEY` set, add `"api_key": "<service key>"` to `litellm_params`.

## 2. Create the Auto Router

**GUI.** *Models* → *Add Model* → *Auto Router*, type *Complexity router*. Classifier: *OSS classifier*, pointing at
`laya` / `multilingual` and the service URL, timeout 1000 ms. Tiers: one line per tier, with a short description and
the model to use for `SIMPLE`, `MEDIUM`, `COMPLEX` and `REASONING`. Classifier fallback: *Default model*.
Classification mode: `user_turn`. Default model: your medium model.

**API.** Validate the configuration first, then register it with the same body:

```bash
export ROUTER='{
  "classifier_type": "oss_classifier",
  "opensource_classifier_config": {"provider": "laya", "model": "multilingual", "api_base": "'"$S1D1_API_BASE"'", "timeout_ms": 1000},
  "classification_mode": "user_turn",
  "classifier_fallback": "default_model",
  "tiers": {"SIMPLE": "cheap-model", "MEDIUM": "medium-model", "COMPLEX": "strong-model", "REASONING": "strongest-model"}}'
```

```bash
curl -sS "$LITELLM/auto_router/validate_complexity_router_config" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' -d "$ROUTER"
```

```bash
curl -sS "$LITELLM/model/new" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' -d '{
  "model_name": "s1d1-auto",
  "litellm_params": {"model": "auto_router/complexity_router", "complexity_router_default_model": "medium-model", "complexity_router_config": '"$ROUTER"'}}'
```

## 3. Test the routing

**GUI.** Open the router, *Test Routing*, paste a prompt, read the tier and the model chosen.

**API.**

```bash
curl -sS "$LITELLM/auto_router/test_routing" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' \
  -d '{"prompt": "Write a Terraform module for an Azure Container App", "router_name": "s1d1-auto"}'
```

Try a greeting, an ordinary task and a hard one: they should land on three different tiers.

## 4. Watch it

Call the router like any model (`"model": "s1d1-auto"`). On the service, `/metrics` has `tantive_decisions_total` by
tier, and each request leaves a `decision` log line. A 503 from the service is good news for LiteLLM: the request
goes to the `classifier_fallback` (the default model) at once, instead of timing out ([configuration.md](configuration.md#shedding-by-deadline)).

## Notes

- **Why `laya`.** LiteLLM accepts a self-hosted classifier only under a provider name from its allow-list; the name
  carries no behaviour here, only the wire format matters. Use `laya` and `multilingual` as written.
- **LiteLLM before 1.106** has no `oss_classifier`: mount the `chat` format (`S1D1_FORMATS=...,chat`), register the
  service as an OpenAI-compatible model and use `classifier_type: llm` ([formats.md](formats.md#chat-completions)).
