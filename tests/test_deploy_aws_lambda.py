"""The AWS Lambda path: a CloudFormation template for the container function and the derived image it needs."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tantive import __version__

from .test_deploy_aws import FOLDER, Loader

TEMPLATE = FOLDER / "template.lambda.yaml"
DOCKERFILE = FOLDER / "Dockerfile.lambda"
README = FOLDER / "README.md"


def load() -> dict:
    return yaml.load(TEMPLATE.read_text(), Loader=Loader)  # noqa: S506 - SafeLoader subclass


def resources(type_: str) -> dict:
    return {k: v for k, v in load()["Resources"].items() if v["Type"] == type_}


def only(type_: str) -> dict:
    (resource,) = resources(type_).values()
    return resource["Properties"]


def test_template_loads_with_its_intrinsic_tags():
    t = load()
    assert t["AWSTemplateFormatVersion"] == "2010-09-09" and t["Description"]
    assert {"AWS::Lambda::Function", "AWS::Lambda::Alias", "AWS::Lambda::Version"} <= {
        r["Type"] for r in t["Resources"].values()
    }


def test_the_function_is_a_container_image_with_the_given_memory_timeout_and_architecture():
    f = only("AWS::Lambda::Function")
    assert f["PackageType"] == "Image" and f["Code"] == {"ImageUri": {"Ref": "ImageUri"}}
    assert f["MemorySize"] == {"Ref": "MemorySize"} and f["Timeout"] == {"Ref": "Timeout"}
    assert f["Architectures"] == [{"Ref": "Architecture"}]
    p = load()["Parameters"]
    assert p["Architecture"]["AllowedValues"] == ["x86_64", "arm64"] and p["Architecture"]["Default"] == "x86_64"
    assert p["MemorySize"]["Default"] == 4096, "4096 MB for full; 2048 for small is in the README"
    assert p["MemorySize"]["MaxValue"] == 10240


def test_the_image_is_an_ecr_uri_without_default():
    image = load()["Parameters"]["ImageUri"]
    assert "Default" not in image and "ecr" in image["AllowedPattern"]


def test_provisioned_concurrency_defaults_to_one_and_sits_on_the_alias():
    p = load()["Parameters"]["ProvisionedConcurrency"]
    assert p["Default"] == 1 and p["MinValue"] == 0 and "cold start" in p["Description"].lower()
    alias = only("AWS::Lambda::Alias")
    config = alias["ProvisionedConcurrencyConfig"]["Fn::If"]
    assert config[2] == {"Ref": "AWS::NoValue"}, "0 removes the configuration (Lambda accepts 1 and more)"
    assert "ProvisionedConcurrentExecutions" in str(config[1])
    assert alias["FunctionVersion"] == {"Fn::GetAtt": "Version.Version"}


def test_secret_and_required_parameters():
    p = load()["Parameters"]
    assert p["ApiKey"]["NoEcho"] is True and p["ApiKey"]["Default"] == ""
    for name in ("ImageUri", "Tiers"):
        assert "Default" not in p[name], f"{name} depends on the user: no default"
    for name, spec in p.items():
        assert spec.get("Description"), f"{name} has no description"


def test_environment_has_the_service_variables_and_the_key_only_when_given():
    env = only("AWS::Lambda::Function")["Environment"]["Variables"]
    assert {"S1D1_TIERS", "S1D1_FORMATS", "S1D1_DEADLINE_MS", "S1D1_API_KEY"} <= set(env)
    assert env["S1D1_API_KEY"]["Fn::If"][0] == "HasApiKey"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"]["Fn::If"][0] == "HasOtlpEndpoint"


def test_function_url_is_iam_by_default_and_open_only_on_request():
    p = load()["Parameters"]["UrlAuthType"]
    assert p["Default"] == "AWS_IAM" and set(p["AllowedValues"]) == {"AWS_IAM", "NONE", "DISABLED"}
    url = resources("AWS::Lambda::Url")
    (resource,) = url.values()
    assert resource["Condition"] == "HasFunctionUrl"
    assert resource["Properties"]["AuthType"] == {"Ref": "UrlAuthType"}
    assert resource["Properties"]["Qualifier"] == "live", "the URL points at the alias, the warm instances"
    permissions = resources("AWS::Lambda::Permission").values()
    open_ones = [r["Properties"]["Action"] for r in permissions if r.get("Condition") == "UrlIsPublic"]
    assert set(open_ones) == {"lambda:InvokeFunctionUrl", "lambda:InvokeFunction"}


def test_an_existing_alb_or_api_gateway_may_invoke_the_alias():
    p = load()["Parameters"]
    assert p["InvokerPrincipal"]["Default"] == "" and p["InvokerSourceArn"]["Default"] == ""
    (permission,) = [r for r in resources("AWS::Lambda::Permission").values() if r.get("Condition") == "HasInvoker"]
    assert permission["Properties"]["Principal"] == {"Ref": "InvokerPrincipal"}
    assert permission["Properties"]["SourceArn"] == {"Ref": "InvokerSourceArn"}
    assert "InvokerSourceArn" in str(load()["Rules"]), "a principal without a source is a confused deputy"
    assert load()["Outputs"]["AliasArn"]


def test_logs_expire_and_the_role_is_the_basic_one():
    assert only("AWS::Logs::LogGroup")["RetentionInDays"] == 30
    assert "AWSLambdaBasicExecutionRole" in TEMPLATE.read_text()
    assert not re.search(r"Resource: ['\"]?\*", TEMPLATE.read_text())


def test_outputs_carry_no_secret():
    assert "ApiKey" not in str(load()["Outputs"])


def test_dockerfile_adds_the_web_adapter_to_our_image():
    text = DOCKERFILE.read_text()
    assert re.search(rf"^ARG BASE=ghcr\.io/scombat/s1d1-tantive:{re.escape(__version__)}-full-onnx-int8$", text, re.M)
    assert re.search(r"^FROM \$\{BASE\}$", text, re.M)
    copy = "COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter"
    assert copy in text.splitlines()
    for setting in ("AWS_LWA_PORT=8788", "AWS_LWA_READINESS_CHECK_PATH=/ready", "AWS_LWA_ASYNC_INIT=true"):
        assert setting in text


def test_dockerfile_respects_the_lambda_constraints():
    text = DOCKERFILE.read_text()
    assert "PYTHONDONTWRITEBYTECODE=1" in text, "read-only file system: no .pyc written next to the sources"
    assert "HOME=/tmp" in text, "/tmp is the only writable path"
    assert re.search(r"^USER 10001$", text, re.M), "never root (and Lambda runs its own unprivileged user anyway)"


def test_readme_has_a_lambda_section():
    text = README.read_text()
    for needle in (
        "## Lambda",
        "aws cloudformation deploy",
        "template.lambda.yaml",
        "Dockerfile.lambda",
        "ProvisionedConcurrency",
        "cold start",
        "LiteLLM",
        "read-only",
        "/tmp",
        "ECR",
        "ECS",
    ):
        assert needle in text, needle
    assert len(text.splitlines()) <= 130
    assert Path(TEMPLATE).exists()
