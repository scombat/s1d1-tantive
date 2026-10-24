# Keep the classified prompts

This page shows how to keep the prompts the service classifies, to label them again and retrain. Capture is off
unless `S1D1_CAPTURE_DIR` is set; the service only writes files into that folder, and shipping them elsewhere is up to
you (last sections), or the opt-in sender posts them to the community ingestion.

> **The prompts are your users' data.** Turn capture on with their agreement and a legal basis (GDPR and the like),
> sample (`S1D1_CAPTURE_SAMPLE`), retain little (`S1D1_CAPTURE_RETENTION_DAYS`) and encrypt the volume. The text
> goes to these files and nowhere else: not to the logs (unless `S1D1_LOG_PROMPTS=1`), not to OpenTelemetry.

## Variables

| variable | default | meaning |
|---|---|---|
| `S1D1_CAPTURE_DIR` | none | the folder to write into (a mounted volume); created if missing, a start-up error if it is not writable |
| `S1D1_CAPTURE_SAMPLE` | 1.0 | 0 to 1: the chance of keeping a decision, drawn for each one |
| `S1D1_CAPTURE_MIN_MARGIN` | empty | keep only the doubtful cases: scores within this distance of a tier bound of the native set (empty = all) |
| `S1D1_CAPTURE_MAX_MB` | 100 | start a new file beyond this size |
| `S1D1_CAPTURE_RETENTION_DAYS` | empty | delete `capture-*` files older than this at start-up and once a day (empty = never) |
| `S1D1_CAPTURE_QUEUE` | 10000 | decisions waiting for the writer; when full, a decision is lost and counted |

One file per UTC day, `capture-2026-10-21.jsonl`, then `capture-2026-10-21-001.jsonl` and so on when a file reaches
`S1D1_CAPTURE_MAX_MB`; a restart appends to the last file of the day. A single thread writes (append, flush after each
line), never the request. `/metrics` counts `tantive_capture_written_total` and `tantive_capture_dropped_total`;
`/info` reports `capture` (`enabled`, `dir`, `sample`, `min_margin`, `retention_days`, and `send`, below).

## One line

```json
{"time": "2026-10-21T12:00:00.000Z", "id": "8b8c846f-d861-4d21-a86b-4582c8e5209a", "prompt": "how do I fix this bug?", "format": "systemone", "route": "/v1/systemone", "tier": "MEDIUM", "score": 0.62, "task": "coding_debugging", "needs_tools": 0.2, "confidence": 0.627, "pieces": 1, "truncated": false, "prompt_tokens": 12, "latency_ms": 12.3, "variant": "small", "revision": "abc1234", "service_version": "0.8.0", "terms_version": "v1"}
```

`prompt` is the current request as classified (after LiteLLM's marker), not the conversation. `tier` is the native
tier and `confidence` that tier's calibrated confidence (1 minus the normalised entropy of the tier probabilities, as
in the responses). `needs_tools` is a probability. `variant` and `revision` say which weights decided. `terms_version` is the version of the usage terms in force when the line was written (`S1D1_TERMS_VERSION`, `v1` by default).

## Mount a volume

The image runs as user 10001, so the folder must be writable by it.

Docker:

```bash
mkdir capture && sudo chown 10001 capture
docker run -p 8788:8788 -v "$PWD/capture:/capture" -e S1D1_CAPTURE_DIR=/capture -e S1D1_CAPTURE_SAMPLE=0.1 \
  ghcr.io/scombat/s1d1-tantive:0.8.0-small-onnx-int8
```

Azure Container Apps, with an Azure Files share (the template creates the environment storage in its resource group,
so the environment must live there):

```bash
az deployment group create -g my-rg -f deploy/azure/azuredeploy.json \
  -p environmentId="$ENV_ID" registryUsername=... registryPassword="$GHCR_TOKEN" ingressExternal=false tiers="$TIERS" \
  -p captureShareName=capture captureStorageAccount=mystorage captureStorageKey="$STORAGE_KEY"
```

ECS, with EFS (`deploy/aws/template.yaml`; the EFS mount targets must accept NFS from the service's security group,
and the file system root must be writable by user 10001, for instance through an access point):

```bash
aws cloudformation deploy --stack-name s1d1 --template-file deploy/aws/template.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides CaptureFileSystemId=fs-0123456789abcdef0 VpcId=... SubnetIds=... AssignPublicIp=... IngressCidr=... Tiers=...
```

Kubernetes, three lines on the pod: a PVC, its volume, and `securityContext.fsGroup: 10001`:

```yaml
volumes: [{name: capture, persistentVolumeClaim: {claimName: s1d1-capture}}]
containers: [{name: tantive, volumeMounts: [{name: capture, mountPath: /capture}], env: [{name: S1D1_CAPTURE_DIR, value: /capture}]}]
securityContext: {fsGroup: 10001}
```

## Send to the community ingestion

**Off by default, and turning it on is your act as the deployer.** With `S1D1_CAPTURE_SEND=1` the service also posts the
captured lines, in batches, to a community ingestion, so that they help retrain s1d1. The prompts you send are kept
and used to retrain s1d1 under the conditions at <https://s1d1.qoo.gy/conditions/v1>; by enabling it you guarantee
that you have the right to send your users' prompts. Without the variable nothing leaves the service, and the
files in the capture folder are all there is.

Sending needs `S1D1_CAPTURE_DIR`: the capture files are the queue, so the service refuses to start with
`S1D1_CAPTURE_SEND=1` and no folder. A cursor file (`.send-cursor`) in that folder marks what was delivered, and
`.instance-id` holds a random identifier of this instance; the retention purge never touches either. Set
`S1D1_CAPTURE_RETENTION_DAYS=2` so that the raw files do not stay on your volume longer than the ingestion window.

| variable | default | meaning |
|---|---|---|
| `S1D1_CAPTURE_SEND` | 0 | `1` to send the captured lines |
| `S1D1_CAPTURE_URL` | `https://s1d1.qoo.gy/ingest` | where to post; `https` only, except `localhost`, `127.0.0.1` and `[::1]` |
| `S1D1_CAPTURE_TOKEN` | none | sent as `Authorization: Bearer <token>` when set; the community instance's own token is optional for anyone else |
| `S1D1_CAPTURE_BATCH` | 100 | lines per batch, 1 to 500 (a batch is also capped at 1 MiB) |
| `S1D1_CAPTURE_SEND_SECONDS` | 30 | wait when there is nothing to send, 1 to 3600 |
| `S1D1_TERMS_VERSION` | `v1` | the conditions version written in each line (`terms_version`) |

One daemon thread posts `application/x-ndjson` with `User-Agent: s1d1-tantive/<version>`. Each line has the keys `v`
(1), `id`, `time`, `prompt`, `format`, `instance_id`, `variant`, `model_revision`, `terms_version`, `pred_tier`,
`pred_score`, `pred_task`, `pred_needs_tools` and `pred_confidence`; route, pieces, token counts, latency and the
service version stay local. A backlog drains batch after batch without waiting. The cursor moves on a 2xx and on a 400
or 413 (that batch will never be taken, so it is skipped and counted as rejected); any other answer or a network error
leaves it where it was and the same lines go again after a wait that doubles at each failure, up to 10 minutes. No
redirect is followed. A line over 1 MiB on its own is skipped and counted as rejected.

Delivery is at least once: a batch accepted just before a crash or a lost reply is sent again, so the ingestion has to
be treated as receiving duplicates and deduplicate on `id`.

The token, the URL's credentials and the prompts never reach the logs, `/info` or OpenTelemetry. `/info` shows
`capture.send` with `enabled`, `url` and `instance_id`. `/metrics` counts `tantive_capture_sent_total` (lines
accepted), `tantive_capture_send_failed_total` (batches not delivered) and `tantive_capture_send_rejected_total`
(lines skipped because the ingestion refused them or they were too large).

## Ship the files elsewhere

Besides the opt-in sender above, the service has no uploader: a scheduled copy or a sidecar does it with the tools you already run. Files
of past days are complete; the current one is still being appended to.

```bash
aws s3 sync /capture s3://bucket/s1d1-capture/
azcopy sync /capture "https://myaccount.blob.core.windows.net/container?<sas>"
```

Run either from a cron job or a Kubernetes CronJob sharing the volume, or as a sidecar in a loop. Vector or Fluent Bit
can tail `capture-*.jsonl` and forward each line to any sink they support.

## Back to labelling

The files feed the import tool of the research repository, `pipeline/12_import_capture.py`.
