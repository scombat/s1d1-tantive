# Deploy s1d1-tantive on Azure Container Apps

This page shows how to deploy s1d1-tantive on Azure Container Apps with one ARM template, `azuredeploy.json`. It
creates a single `Microsoft.App/containerApps` resource in a Container Apps environment that already exists: no
environment, registry, network or log workspace.

## What it creates

- the image `ghcr.io/scombat/s1d1-tantive:<version>-<variant>`, pulled from GHCR with the credentials you give (the
  images are public, but the template still asks for credentials; the password is kept as a container app secret);
- ingress on port 8788, **internal or external as you choose** (no default), TLS terminated by the environment;
- probes: liveness `/health`, readiness `/ready`, startup `/ready` (up to 60 s for the model to load);
- scale from `minReplicas` to `maxReplicas` on concurrent HTTP requests per replica;
- the service settings as `S1D1_*` variables; `S1D1_API_KEY` exists only when `apiKey` is not empty;
- a system-assigned managed identity (not used by the service today).

## Facts

- Image: `ghcr.io/scombat/s1d1-tantive:0.3.1-full-onnx-int8`
- RAM: 4096 MiB (default `memory` 4Gi with 2.0 vCPU)
- Status: needs public repository
- Still to do (you): make the repository public so the portal can read the template. The template still asks for
  `registryUsername` and `registryPassword` (no default, always sent): give a GitHub user and a token with
  `read:packages` even though the images are public. Making the registry block optional is a change to the
  template, left for a later release.

## Variables to set

| parameter | type | default | meaning |
|---|---|---|---|
| `name` | string | `s1d1-tantive` | container app name |
| `location` | string | resource group's | must be the environment's region |
| `environmentId` | string | **none** | resource id of the existing Container Apps environment |
| `image` | string | `ghcr.io/scombat/s1d1-tantive:0.3.1-full-onnx-int8` | image to run (one per model variant) |
| `registryServer` | string | `ghcr.io` | registry host |
| `registryUsername` | string | **none** | registry user (a GitHub user for GHCR) |
| `registryPassword` | securestring | **none** | registry password (a token with `read:packages`) |
| `ingressExternal` | bool | **none** | `false` = reachable only inside the environment; `true` = public, then set `apiKey` |
| `cpu` | string | `2.0` | vCPU per replica |
| `memory` | string | `4Gi` | memory per replica (2.0 with 4Gi suits full; 1.0 with 2Gi suits small) |
| `minReplicas` | int | 1 | minimum replicas |
| `maxReplicas` | int | 3 | maximum replicas |
| `concurrentRequests` | int | 8 | concurrent requests per replica above which one is added |
| `tiers` | string | **none** | `S1D1_TIERS`; must match the tier names LiteLLM sends, otherwise the service answers 422 |
| `formats` | string | `systemone,decisions,classify` | `S1D1_FORMATS` |
| `deadlineMs` | int | 800 | `S1D1_DEADLINE_MS` |
| `apiKey` | securestring | empty | `S1D1_API_KEY`; empty = no key, no secret |
| `otlpEndpoint` | string | empty | `OTEL_EXPORTER_OTLP_ENDPOINT`; empty = no export |

**Warning:** `ingressExternal=true` without an `apiKey` publishes an unauthenticated endpoint on the internet: the
service only checks a key when `S1D1_API_KEY` is set, and the template sets it only when `apiKey` is not empty.
The template cannot refuse that combination, so give an `apiKey` whenever the ingress is external.

The parameters without a default are the ones that depend on your side (environment, registry, network exposure,
tier names): the portal form and the CLI both refuse to deploy until they are given.

Outputs: `fqdn` and `apiBase` (`https://<fqdn>`). With internal ingress the name resolves only inside the
environment's network.

## Deploy with the CLI

```bash
az deployment group create --resource-group <rg> --template-file deploy/azure/azuredeploy.json \
  --parameters environmentId=<env id> registryUsername=<github user> registryPassword=<read:packages token> \
               ingressExternal=false tiers="TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0"
```

Then, from inside the environment (or with `ingressExternal=true` and an `apiKey`, from anywhere):

```bash
curl https://<fqdn>/info
```

It answers the variant, the tiers and `ready: true`.

## Deploy with the button

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fscombat%2Fs1d1-tantive%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json)

The button opens the Azure portal on this template; it needs the file to be publicly reachable, which is the case
once this repository is public. Until then, use the command above.
