---
name: deploy-s1d1-tantive
description: Use when the user wants to deploy, configure or size s1d1-tantive, or to use s1d1 with LiteLLM (Auto Router, classifier, tiers) — picks the image variant, deployment target and environment variables, and gives the deploy command.
---

# Deploy s1d1-tantive

Do not restate the docs; read them and apply them.

1. **Ask three questions**, in one message, and wait:
   - Peak traffic: classifications per second (or users, and how often each prompts).
   - Typical prompt length: short chat turns, or long documents and agent context.
   - Where it runs: Azure, AWS, Cloud Run, Heroku, Render, a Hugging Face Space, a Mac, a closed network.
2. **Read** `docs/use-cases.md` (closest case) and `docs/sizing.md` (the calculation). Check LiteLLM's version: before
   1.106 means the `chat` format.
3. **Compute** replicas, RAM and `S1D1_DEADLINE_MS` with the formulas of `docs/sizing.md`; state the inputs you
   assumed. Take at least 3 replicas for anything shared.
4. **Choose** the variant, the target, and the variables from the matching case in `docs/use-cases.md`; the tier
   names in `S1D1_TIERS` must be the router's. Variables are defined in `docs/configuration.md`.
5. **Produce** two things: the environment block in a `bash` fence, and the deploy command or button of the target
   from `deploy/<target>/README.md` (copy it from there, do not invent flags). Use the image tag of the current
   release (see `CHANGELOG.md`), never `latest`. Leave secrets as `<secret>` placeholders.
6. **Say what remains to verify**:
   - `GET /ready` answers 200, and the `ready` log line: `predicted_ms_1024` below `S1D1_DEADLINE_MS`, no
     `capacity_warning`, `sustainable_rps` consistent with the sizing.
   - LiteLLM wiring in `docs/litellm.md`, then `test_routing` on a greeting, an ordinary task and a hard one: three
     different tiers.
   - Optionally the signature and weights, `docs/verify.md`.
