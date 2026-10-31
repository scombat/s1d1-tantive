# Use it with LiteLLM

This page shows how to make LiteLLM's Auto Router choose a model with s1d1-tantive. The service is registered as an
ordinary model (`hosted_vllm/<name>`) and the router points at it by `deployment_name`. The recipe is given as a
`config.yaml`, then as the same two registrations through the API.

You need a running service reachable from LiteLLM ([getting-started.md](getting-started.md)) and LiteLLM 1.106 or
later. The recipe below was tested with LiteLLM 1.106.6.

## Which tier names

LiteLLM's complexity router has four tiers, `SIMPLE`, `MEDIUM`, `COMPLEX` and `REASONING`, which the service always
recognises, whatever its `S1D1_TIERS` (the native five by default: `TRIVIAL`, `LOW`, `MEDIUM`, `HIGH`, `EXPERT`).
The model names on the right of `tiers` must exist in `model_list`; a tier whose model is missing counts as a failure.
When the classifier fails or answers a name the router does not know, the request goes to the `classifier_fallback`;
with `default_model`, that is the router's default model. If routing seems stuck on one model, check the names first.

## The recipe

Tested with LiteLLM 1.106.6. `api_base` is the service root, without `/v1`: LiteLLM appends `/v1/systemone`.

```yaml
model_list:
  # ... your four models: cheap, medium, strong, reasoning
  - model_name: s1d1-classifier
    litellm_params:
      model: hosted_vllm/s1d1-small    # the name after the slash is sent and ignored by the service
      api_base: http://localhost:8788
    model_info:
      mode: decisions
  - model_name: s1d1-auto
    litellm_params:
      model: auto_router/complexity_router
      complexity_router_default_model: medium
      complexity_router_config:
        classifier_type: oss_classifier
        opensource_classifier_config:
          deployment_name: s1d1-classifier
          timeout_ms: 1000
        classifier_fallback: default_model
        tiers: {SIMPLE: cheap, MEDIUM: medium, COMPLEX: strong, REASONING: reasoning}
```

`model_info.mode: decisions` is the documented form; 1.106.6 routes the same without it. Do not put `api_key` or
`api_base` next to `deployment_name`: LiteLLM refuses them, the deployment carries them.

## 1. Register the classifier

**GUI.** Not exercised with this recipe. If *Add Model* offers mode *Decisions* with a `hosted_vllm` provider, fill the
same fields as the YAML above; otherwise use the fallback at the end of this page.

**API** (not exercised: the recipe above is what was run).

```bash
export LITELLM=https://litellm.example.internal
export LITELLM_KEY=<litellm admin key>
export S1D1_API_BASE=https://s1d1-tantive.internal.example   # the service, as LiteLLM reaches it
```

```bash
curl -sS "$LITELLM/model/new" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' -d '{
  "model_name": "s1d1-classifier",
  "litellm_params": {"model": "hosted_vllm/s1d1-small", "api_base": "'"$S1D1_API_BASE"'"},
  "model_info": {"mode": "decisions", "input_cost_per_token": 0, "output_cost_per_token": 0}
}'
```

If the service has `S1D1_API_KEY` set, add `"api_key": "<service key>"` to `litellm_params` (not exercised).

## 2. Create the Auto Router

**GUI.** Not exercised with this recipe. The router form needs the same values as the YAML: classifier *OSS classifier*
pointing at the `s1d1-classifier` deployment, timeout 1000 ms, one line per tier, classifier fallback *Default model*,
default model your medium model.

**API.** Validate the configuration first, then register it:

```bash
export ROUTER='{
  "classifier_type": "oss_classifier",
  "opensource_classifier_config": {"deployment_name": "s1d1-classifier", "timeout_ms": 1000},
  "classification_mode": "user_turn",
  "classifier_fallback": "default_model",
  "tiers": {"SIMPLE": "cheap-model", "MEDIUM": "medium-model", "COMPLEX": "strong-model", "REASONING": "strongest-model"}}'
```

```bash
curl -sS "$LITELLM/auto_router/validate_complexity_router_config" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' \
  -d '{"complexity_router_config": '"$ROUTER"'}'
```

```bash
curl -sS "$LITELLM/model/new" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' -d '{
  "model_name": "s1d1-auto",
  "litellm_params": {"model": "auto_router/complexity_router", "complexity_router_default_model": "medium-model", "complexity_router_config": '"$ROUTER"'}}'
```

## 3. Test the routing

**GUI.** Open the router, *Test Routing*, paste a prompt, read the tier and the model chosen (not exercised).

**API.** In 1.106.6 the body carries the configuration too, not only the prompt: with `prompt` and `router_name`
alone the call answers 422.

```bash
curl -sS "$LITELLM/auto_router/test_routing" -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' \
  -d '{"prompt": "Write a Terraform module for an Azure Container App", "router_name": "s1d1-auto", "default_model": "medium-model", "complexity_router_config": '"$ROUTER"'}'
```

Try a greeting, an ordinary task and a hard one: they should land on three different tiers.

## 4. Watch it

Call the router like any model (`"model": "s1d1-auto"`). The response header `x-litellm-complexity-router-cause` is
`jev_classifier` whenever any classifier answered, so it does not show that tantive was the one. Two things do: on the
service, `/metrics` has `tantive_decisions_total` by tier (it goes up by one per routed request), and each request
leaves a `decision` log line; in LiteLLM, `test_routing` answers with `classifier_model`, which names the service's
variant (`hosted_vllm/s1d1-router-...`).

A 503 from the service is good news for LiteLLM: the request goes to the `classifier_fallback` (the default model) at
once, instead of timing out ([configuration.md](configuration.md#shedding-by-deadline)); this was not provoked in the
test. The two failures that were: a service that exceeds `timeout_ms` opens a circuit breaker for 30 seconds, during
which every request goes to the default model without calling the service (the first one pays the whole timeout); a
stopped service is retried on each request and falls back in tens of milliseconds, with no breaker.

## Notes

- **On the wire.** LiteLLM prefixes `state` with `Classify this message:` and sends the four tier criteria with their
  descriptions. The service accepts the request and ignores the descriptions: the tier comes from the model.
- **Fallback: `provider: laya`.** If the admin GUI does not offer a deployment for the classifier, or the route above
  fails on your 1.106 version (only 1.106.6 was run; `deployment_name` is in the code of 1.106.0 and 1.106.4), name a
  provider instead. LiteLLM's `laya` provider accepts only `english`, `multilingual` and `typed-decisions` as model
  names, hence `multilingual` here, which the service does not read. On 1.106.6 it gave the same tiers and models on
  five prompts, on the same `POST /v1/systemone`.

  ```yaml
        opensource_classifier_config:
          provider: laya
          model: multilingual
          api_base: http://localhost:8788
          timeout_ms: 1000
  ```

  In the GUI: *Add Model*, mode *Decisions*, provider `laya`, model `multilingual`, API base the service URL, price 0;
  in the router, *OSS classifier* with the same provider, model and URL. Through the API, register
  `"model": "laya/multilingual"` instead of `hosted_vllm/s1d1-small`, and give the router the block above instead of
  `deployment_name`.
- **Not verified.** The GUI path for either recipe, `/model/new`, LiteLLM 1.106.1 to 1.106.3 and 1.106.5,
  `classifier_fallback: heuristic`, and the 503 under load.
- **LiteLLM before 1.106** has no `oss_classifier`: mount the `chat` format (`S1D1_FORMATS=...,chat`), register the
  service as an OpenAI-compatible model and use `classifier_type: llm` ([formats.md](formats.md#chat-completions)).
