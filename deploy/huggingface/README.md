---
title: s1d1-tantive
emoji: 🚦
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 8788
suggested_hardware: cpu-basic
short_description: Prompt-complexity decision service for LLM routers
license: other
license_name: bsl-1.1
license_link: LICENSE
---

# s1d1-tantive

The s1d1 router as a decision service: it reads a prompt and answers with a complexity tier, in the formats of
System One, Decisions, vLLM `/classify` and (optionally) chat-completions, so that LiteLLM or any gateway can pick a
model from it. The weights are the `full` ONNX int8 variant, baked into the image.

This Space runs the published image `ghcr.io/scombat/s1d1-tantive:<version>-full-onnx-int8` on the free CPU Basic
hardware (2 vCPU, 16 GB). Open `/info` for the loaded variant and `/docs` for the routes.

Settings are Space **variables** (and one **secret**): `S1D1_TIERS`, `S1D1_FORMATS`, `S1D1_DEADLINE_MS`, and
`S1D1_API_KEY` as a secret if the Space is public and you do not want strangers using it. `OTEL_EXPORTER_OTLP_ENDPOINT` is an optional variable. Source, tests and the
other deployment targets: <https://github.com/scombat/s1d1-tantive>.

License: Business Source License 1.1 (BSL): non-commercial use is free, production use needs an agreement with the
licensor, and each version becomes Apache-2.0 four years after it is first published. See the LICENSE file of this Space.
