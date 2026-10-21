# Deploy s1d1-tantive on AWS (ECS Fargate)

This page shows how to deploy s1d1-tantive on AWS with a CloudFormation template, `template.yaml`, that runs the
published image as one ECS Fargate service in a VPC you already have. It creates an ECS cluster, a CloudWatch log
group (30 days), a security group (port 8788 from the CIDR you give), a task execution role, the task definition, the
service, and, only when you give them, Secrets Manager secrets for the registry credentials and the API key. It
creates no VPC, no load balancer and no DNS.

**The README button opens this page** until the template is on S3. Once it is uploaded, replace the link of the
AWS button in the root README (`deploy/aws/README.md`) with the quick-create link of "Deploy with the button" below
(`console.aws.amazon.com/cloudformation`), whose `templateURL` is your template's HTTPS URL.

Checked against the AWS documentation on 2026-10-21 (quick-create links, Fargate CPU and memory pairs, repository
credentials, pricing).

## Facts

- Image: `ghcr.io/scombat/s1d1-tantive:0.6.0-full-onnx-int8`
- RAM: 4096 MiB (default `Memory`, with `Cpu` 2048)
- Status: needs a template upload (S3)
- Still to do (you): upload `template.yaml` to an S3 bucket you own, build the link below from its URL and put it
  in the AWS button of the root README.

## Cost and variant

The Fargate pricing page (2026-10-21) shows no free tier: Linux/x86 in us-east-1 is $0.0405 per vCPU-hour and
$0.00445 per GB-hour. The default task (2 vCPU, 4 GB, the `full` variant) costs about $0.10 an hour, about $72 for
a month running all the time. `small` fits 1 vCPU and 2 GB (about $0.05 an hour, about $36 a month): set
`Image` to `...:0.6.0-small-onnx-int8`, `Cpu` to `1024` and `Memory` to `2048`. `tiny` fits 512 MiB, but Fargate's
smallest CPU/memory pair that holds it is 256 CPU with 512 MiB, which leaves almost nothing (the service uses about
450 MiB idle): use 512 CPU with 1024 MiB instead.

## Variables to set

| parameter | default | meaning |
|---|---|---|
| `VpcId`, `SubnetIds` | **none** | where the tasks run |
| `AssignPublicIp` | **none** (`ENABLED` or `DISABLED`) | `ENABLED` in public subnets (needed to pull from ghcr.io); `DISABLED` in private subnets that have a NAT gateway or a registry mirror |
| `IngressCidr` | **none** | who may reach port 8788; the VPC CIDR keeps it inside, `0.0.0.0/0` publishes it (then give `ApiKey`) |
| `Image` | `ghcr.io/scombat/s1d1-tantive:0.6.0-full-onnx-int8` | image to run |
| `RegistryUsername`, `RegistryPassword` | empty | only for a private image or mirror; the password is `NoEcho` and goes to a Secrets Manager secret. The GHCR images are public, so they need no credentials |
| `Tiers` | **none** | `S1D1_TIERS`; must match the tier names your gateway sends, otherwise the service answers 422 |
| `Formats`, `DeadlineMs` | `systemone,decisions,classify`, 800 | `S1D1_FORMATS`, `S1D1_DEADLINE_MS` |
| `ApiKey` | empty | `S1D1_API_KEY`, `NoEcho`, kept in Secrets Manager and injected as a container secret (it is never a plain environment variable); empty = no key |
| `OtlpEndpoint` | empty | `OTEL_EXPORTER_OTLP_ENDPOINT`, optional: an OpenTelemetry collector for traces and metrics; empty = no export, the variable is not set |
| `Cpu`, `Memory` | 2048, 4096 | task size in CPU units and MiB; the pair must be one Fargate allows |
| `DesiredCount` | 1 | number of tasks |

The parameters without a default depend on your side (network, exposure, tier names); the console and the CLI refuse
to continue until they are given. The container health check calls `/ready` (60 s start period), so a task counts as
healthy only once the model has loaded.

**Warning:** `IngressCidr=0.0.0.0/0` without an `ApiKey` publishes an unauthenticated endpoint on the internet: the
service checks a key only when `S1D1_API_KEY` is set.

## No load balancer

The service has no Application Load Balancer: a task is reached on its own IP, which changes when the task is
replaced. That is enough for a test from inside the VPC. For production put an Application Load Balancer (target
type `ip`, health check `/ready`) or ECS Service Connect in front, and let its security group be the only source of
`IngressCidr`.

## Deploy with the button

CloudFormation quick-create links take the template from an S3 URL, so upload it first:

```bash
aws s3 cp deploy/aws/template.yaml s3://<your-bucket>/s1d1-tantive/template.yaml
```

The bucket must let CloudFormation read the object (the console reads it with your credentials, so a private bucket
in your account works). Then the link, with the bucket's HTTPS object URL:

```text
https://eu-west-1.console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/create/review?templateURL=<s3-https-url>&stackName=s1d1-tantive
```

for example `<s3-https-url>` = `https://s3.eu-west-1.amazonaws.com/<your-bucket>/s1d1-tantive/template.yaml`. As
button markdown:

```markdown
[![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://eu-west-1.console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/create/review?templateURL=<s3-https-url>&stackName=s1d1-tantive)
```

The default region is `eu-west-1` (Ireland). To change the region, replace `eu-west-1` twice in the link (the host and
`region=`) and once after `--region` in the CLI command below; the template itself names no region.

You may add `&param_Formats=...` style parameters to the link; `NoEcho` parameters (`RegistryPassword`, `ApiKey`) are
ignored in links by design and are entered in the console. The console asks you to acknowledge that the stack creates
an IAM role.

## Deploy with the CLI

```bash
aws cloudformation deploy --stack-name s1d1-tantive --region eu-west-1 --template-file deploy/aws/template.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides VpcId=<vpc-id> SubnetIds=<subnet-a>,<subnet-b> AssignPublicIp=ENABLED \
                        IngressCidr=<10.0.0.0/16> Tiers="TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0"
```

Add `ApiKey=<key>` when the service is reachable beyond the VPC.
