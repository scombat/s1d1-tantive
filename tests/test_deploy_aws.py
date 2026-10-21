"""The AWS CloudFormation template (ECS Fargate) and its Launch Stack instructions (shape checks only)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "deploy" / "aws"
TEMPLATE = FOLDER / "template.yaml"
VARIANT_MIN_RAM = {"full": 2048, "compact": 2048, "small": 1024, "tiny": 512}


class Loader(yaml.SafeLoader):
    """CloudFormation short forms (!Ref, !Sub, !If ...) become {"Fn::X": value}; the shape tests read them as data."""


def _intrinsic(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    name = suffix if suffix in ("Ref", "Condition") else f"Fn::{suffix}"
    return {name: value}


Loader.add_multi_constructor("!", _intrinsic)


def load() -> dict:
    return yaml.load(TEMPLATE.read_text(), Loader=Loader)  # noqa: S506 - SafeLoader subclass


def resources(type_: str) -> dict:
    return {k: v for k, v in load()["Resources"].items() if v["Type"] == type_}


def container() -> dict:
    (task,) = resources("AWS::ECS::TaskDefinition").values()
    (c,) = task["Properties"]["ContainerDefinitions"]
    return c


def test_template_format():
    t = load()
    assert t["AWSTemplateFormatVersion"] == "2010-09-09"
    assert t["Description"]


def test_parameters_names_and_defaults():
    p = load()["Parameters"]
    assert set(p) == {
        "VpcId", "SubnetIds", "AssignPublicIp", "IngressCidr", "Image", "RegistryUsername", "RegistryPassword",
        "Tiers", "Formats", "DeadlineMs", "ApiKey", "OtlpEndpoint", "CaptureFileSystemId", "Cpu", "Memory",
        "DesiredCount",
    }  # fmt: skip
    for name in ("VpcId", "SubnetIds", "AssignPublicIp", "IngressCidr", "Tiers"):
        assert "Default" not in p[name], f"{name} depends on the user's side: no default"
    assert p["AssignPublicIp"]["AllowedValues"] == ["ENABLED", "DISABLED"]
    assert p["Formats"]["Default"] == "systemone,decisions,classify"
    assert p["Cpu"]["Default"] == "2048" and p["Memory"]["Default"] == "4096" and p["DesiredCount"]["Default"] == 1
    assert [p[k]["Default"] for k in ("RegistryUsername", "RegistryPassword", "ApiKey", "CaptureFileSystemId")] == [
        ""
    ] * 4


def test_secret_parameters_are_no_echo():
    p = load()["Parameters"]
    assert p["RegistryPassword"]["NoEcho"] is True
    assert p["ApiKey"]["NoEcho"] is True


def test_every_parameter_is_described():
    for name, spec in load()["Parameters"].items():
        assert spec.get("Description"), f"{name} has no description"


def test_the_service_is_fargate_without_a_load_balancer():
    types = {r["Type"] for r in load()["Resources"].values()}
    assert {"AWS::ECS::Cluster", "AWS::ECS::TaskDefinition", "AWS::ECS::Service", "AWS::EC2::SecurityGroup"} <= types
    assert not any(t.startswith("AWS::ElasticLoadBalancing") for t in types)
    (service,) = resources("AWS::ECS::Service").values()
    assert service["Properties"]["LaunchType"] == "FARGATE"


def test_container_port_and_variables():
    c = container()
    assert [m["ContainerPort"] for m in c["PortMappings"]] == [8788]
    names = {e["Name"] for e in c["Environment"] if "Name" in e}  # the optional OTLP entry is an Fn::If
    assert {"S1D1_TIERS", "S1D1_FORMATS", "S1D1_DEADLINE_MS"} <= names
    assert "S1D1_API_KEY" not in names, "the key is a secret, not a plain environment variable"


def test_api_key_goes_through_secrets_manager_when_given():
    text = TEMPLATE.read_text()
    assert "Secrets:" in text and "ValueFrom" in text
    assert re.search(r"Name: S1D1_API_KEY", text)
    conditions = load()["Conditions"]
    assert "HasApiKey" in conditions and "HasRegistryCredentials" in conditions


def test_health_check_is_readiness_without_curl():
    check = container()["HealthCheck"]
    command = " ".join(check["Command"])
    assert "/ready" in command and "urllib" in command and "curl" not in command
    assert check["StartPeriod"] >= 60


def test_registry_credentials_only_when_given():
    text = TEMPLATE.read_text()
    assert "RepositoryCredentials" in text and "CredentialsParameter" in text
    credentials = container()["RepositoryCredentials"]
    assert credentials["Fn::If"][0] == "HasRegistryCredentials"
    assert credentials["Fn::If"][2] == {"Ref": "AWS::NoValue"}


def test_execution_role_reads_only_its_own_secrets():
    text = TEMPLATE.read_text()
    assert "AmazonECSTaskExecutionRolePolicy" in text
    assert "secretsmanager:GetSecretValue" in text
    assert '"*"' not in text and "Resource: '*'" not in text and 'Resource: "*"' not in text
    (role,) = resources("AWS::IAM::Role").values()
    (policy,) = role["Properties"]["Policies"]["Fn::If"][1]
    (statement,) = policy["PolicyDocument"]["Statement"]
    refs = [item["Fn::If"][1] for item in statement["Resource"]]
    assert refs == [{"Ref": "RegistrySecret"}, {"Ref": "ApiKeySecret"}]


def test_security_group_opens_only_the_service_port_to_the_given_cidr():
    (group,) = resources("AWS::EC2::SecurityGroup").values()
    (rule,) = group["Properties"]["SecurityGroupIngress"]
    assert rule["FromPort"] == 8788 and rule["ToPort"] == 8788 and rule["IpProtocol"] == "tcp"
    assert rule["CidrIp"] == {"Ref": "IngressCidr"}


def test_default_variant_fits_the_default_task_size():
    p = load()["Parameters"]
    variant = re.search(r"-(full|compact|small|tiny)-onnx-int8$", p["Image"]["Default"]).group(1)
    assert int(p["Memory"]["Default"]) >= VARIANT_MIN_RAM[variant]
    assert p["Image"]["Default"].startswith("ghcr.io/scombat/s1d1-tantive:")


def test_outputs_and_no_secret_in_them():
    outputs = load()["Outputs"]
    assert set(outputs) == {"Cluster", "Service", "SecurityGroup"}
    assert "ApiKey" not in json.dumps(outputs) and "RegistryPassword" not in json.dumps(outputs)


def test_readme_launch_stack_and_cli():
    text = (FOLDER / "README.md").read_text()
    launch = (
        "https://eu-west-1.console.aws.amazon.com/cloudformation/home?region=eu-west-1"
        "#/stacks/create/review?templateURL=<s3-https-url>&stackName=s1d1-tantive"
    )
    assert launch in text
    assert "https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png" in text
    assert "aws cloudformation deploy" in text and "--capabilities CAPABILITY_IAM" in text
    assert "aws s3 cp deploy/aws/template.yaml" in text
    assert "- Status: needs a template upload (S3)" in text
    assert re.search(r"^- RAM: 4096 MiB", text, re.M)
    assert re.search(r"^- Image: `ghcr\.io/scombat/s1d1-tantive:[^`]+-full-onnx-int8`", text, re.M)
    assert "2026-10-21" in text and "NoEcho" in text and "Application Load Balancer" in text


def test_no_secret_value_in_the_folder():
    for path in (p for p in FOLDER.rglob("*") if p.is_file()):
        assert not re.search(r"(ghp_|github_pat_|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY)", path.read_text()), path


def test_trivy_config_clean():
    if shutil.which("trivy") is None:
        pytest.skip("trivy is not installed")
    done = subprocess.run(
        "trivy config --exit-code 1 --severity HIGH,CRITICAL deploy/aws",
        shell=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_capture_mounts_efs_only_when_a_file_system_is_given():
    t = load()
    assert t["Conditions"]["HasCapture"] == {"Fn::Not": [{"Fn::Equals": [{"Ref": "CaptureFileSystemId"}, ""]}]}
    (task,) = resources("AWS::ECS::TaskDefinition").values()
    condition, (volume,), nothing = task["Properties"]["Volumes"]["Fn::If"]
    assert condition == "HasCapture" and nothing == {"Ref": "AWS::NoValue"}
    efs = volume["EFSVolumeConfiguration"]
    assert efs["FilesystemId"] == {"Ref": "CaptureFileSystemId"} and efs["TransitEncryption"] == "ENABLED"
    c = container()
    condition, (mount,), _ = c["MountPoints"]["Fn::If"]
    assert condition == "HasCapture"
    assert (mount["SourceVolume"], mount["ContainerPath"]) == (volume["Name"], "/capture")
    env = [e for e in c["Environment"] if "Fn::If" in e]
    assert {"Name": "S1D1_CAPTURE_DIR", "Value": "/capture"} in [e["Fn::If"][1] for e in env]
