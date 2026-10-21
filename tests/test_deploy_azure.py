"""The Azure Container Apps ARM template and its Deploy-to-Azure button (shape checks only)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "azure" / "azuredeploy.json"
RAW_URL = "https://raw.githubusercontent.com/scombat/s1d1-tantive/main/deploy/azure/azuredeploy.json"


def load() -> dict:
    return json.loads(TEMPLATE.read_text())


def app() -> dict:
    return load()["resources"][0]


def container() -> dict:
    return app()["properties"]["template"]["containers"][0]


def test_template_is_valid_json_with_schema():
    t = load()
    assert t["$schema"].startswith("https://schema.management.azure.com/schemas/")
    assert t["contentVersion"] == "1.0.0.0"


def test_parameters_names_types_defaults():
    p = load()["parameters"]
    expected = {
        "name": ("string", "s1d1-tantive"),
        "location": ("string", "[resourceGroup().location]"),
        "environmentId": ("string", None),
        "image": ("string", "ghcr.io/scombat/s1d1-tantive:0.6.0-full-onnx-int8"),
        "registryServer": ("string", "ghcr.io"),
        "registryUsername": ("string", None),
        "registryPassword": ("securestring", None),
        "ingressExternal": ("bool", None),
        "cpu": ("string", "2.0"),
        "memory": ("string", "4Gi"),
        "minReplicas": ("int", 1),
        "maxReplicas": ("int", 3),
        "concurrentRequests": ("int", 8),
        "tiers": ("string", None),
        "formats": ("string", "systemone,decisions,classify"),
        "deadlineMs": ("int", 800),
        "apiKey": ("securestring", ""),
        "otlpEndpoint": ("string", ""),
        "captureShareName": ("string", ""),
        "captureStorageAccount": ("string", ""),
        "captureStorageKey": ("securestring", ""),
    }
    assert set(p) == set(expected)
    for key, (type_, default) in expected.items():
        assert p[key]["type"] == type_, key
        if default is None:
            assert "defaultValue" not in p[key], key
        else:
            assert p[key]["defaultValue"] == default, key


def test_secure_parameters():
    p = load()["parameters"]
    assert p["registryPassword"]["type"] == "securestring"
    assert p["apiKey"]["type"] == "securestring"
    assert p["captureStorageKey"]["type"] == "securestring"


def test_tiers_has_no_default():
    p = load()["parameters"]
    assert "defaultValue" not in p["tiers"]
    assert "defaultValue" not in p["environmentId"]


def test_ingress_requires_choice():
    p = load()["parameters"]["ingressExternal"]
    assert p["type"] == "bool" and "defaultValue" not in p
    description = p["metadata"]["description"].lower()
    assert "internal" in description and "external" in description and "apikey" in description


def test_every_parameter_is_described():
    for key, spec in load()["parameters"].items():
        assert spec.get("metadata", {}).get("description"), f"{key} has no description"


def test_container_app_shape():
    r = app()
    assert r["type"] == "Microsoft.App/containerApps"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["apiVersion"])
    probes = {p["type"].lower(): p["httpGet"]["path"] for p in container()["probes"]}
    assert probes == {"liveness": "/health", "readiness": "/ready", "startup": "/ready"}
    assert r["properties"]["configuration"]["ingress"]["targetPort"] == 8788
    assert r["properties"]["configuration"]["ingress"]["transport"] == "http"
    assert r["properties"]["configuration"]["ingress"]["external"] == "[parameters('ingressExternal')]"
    assert r["properties"]["environmentId"] == "[parameters('environmentId')]"
    assert r["identity"] == {"type": "SystemAssigned"}


def test_ingress_does_not_allow_insecure_or_open_a_second_port():
    # Trivy has no check for Microsoft.App/containerApps, so the posture is asserted here
    ingress = app()["properties"]["configuration"]["ingress"]
    assert set(ingress) == {"external", "targetPort", "transport"}


def test_startup_probe_waits_a_minute():
    startup = next(p for p in container()["probes"] if p["type"].lower() == "startup")
    assert startup["failureThreshold"] == 6 and startup["periodSeconds"] == 10
    assert all(p["httpGet"]["port"] == 8788 for p in container()["probes"])


def test_scale_rule_on_concurrent_requests():
    scale = app()["properties"]["template"]["scale"]
    assert scale["minReplicas"] == "[parameters('minReplicas')]"
    assert scale["maxReplicas"] == "[parameters('maxReplicas')]"
    (rule,) = scale["rules"]
    # the KEDA http scaler takes its metadata as strings
    assert rule["http"]["metadata"]["concurrentRequests"] == "[string(parameters('concurrentRequests'))]"


def test_registry_pulls_with_a_secret():
    cfg = app()["properties"]["configuration"]
    (registry,) = cfg["registries"]
    assert registry["passwordSecretRef"] == "ghcr-password"
    assert registry["server"] == "[parameters('registryServer')]"
    assert "ghcr-password" in json.dumps(cfg["secrets"]) and "registryPassword" in json.dumps(cfg["secrets"])


def test_env_names():
    env = container()["env"]
    # env is an ARM expression (the API key and OTLP endpoint are optional): assert on its text
    text = env if isinstance(env, str) else json.dumps(env)
    for name in ("S1D1_TIERS", "S1D1_FORMATS", "S1D1_DEADLINE_MS", "S1D1_API_KEY", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        assert name in text, name
    assert "secretRef" in text and "s1d1-api-key" in text


def test_container_resources():
    resources = container()["resources"]
    assert resources["cpu"] == "[json(parameters('cpu'))]"
    assert resources["memory"] == "[parameters('memory')]"


def test_optional_api_key_conditional():
    text = TEMPLATE.read_text()
    # the secret and the env var that points to it exist together or not at all
    assert text.count("if(empty(parameters('apiKey'))") == 2 or text.count("if(equals(parameters('apiKey'), '')") == 2
    assert "if(empty(parameters('otlpEndpoint'))" in text or "if(equals(parameters('otlpEndpoint'), '')" in text


def test_outputs():
    outputs = load()["outputs"]
    assert set(outputs) == {"fqdn", "apiBase"}
    assert "configuration.ingress.fqdn" in outputs["fqdn"]["value"]
    assert outputs["apiBase"]["value"].startswith("[concat('https://'") or outputs["apiBase"]["value"].startswith(
        "[format('https://"
    )


def test_no_secret_in_outputs():
    text = json.dumps(load()["outputs"])
    assert "registryPassword" not in text and "apiKey" not in text


def test_button_link_matches_file():
    readme = (ROOT / "README.md").read_text()
    m = re.search(r"portal\.azure\.com/#create/Microsoft\.Template/uri/([^)\s]+)", readme)
    assert m and unquote(m[1]) == RAW_URL
    assert "https://aka.ms/deploytoazurebutton" in readme
    # the manual command lives in the Azure guide, which test_deploy_readme_covers_both_ways checks


def test_deploy_readme_covers_both_ways():
    text = (ROOT / "deploy" / "azure" / "README.md").read_text()
    assert "az deployment group create" in text
    assert "azuredeploy.json" in text
    assert "publicly reachable" in text
    assert re.search(r"portal\.azure\.com/#create/Microsoft\.Template/uri/", text)


def test_deploy_readme_warns_that_external_ingress_needs_an_api_key():
    text = " ".join((ROOT / "deploy" / "azure" / "README.md").read_text().split())
    assert "Warning:" in text
    assert "ingressExternal=true" in text and "without an `apiKey`" in text and "unauthenticated" in text


def test_readmes_carry_no_secret_value():
    for path in ("README.md", "deploy/azure/README.md"):
        text = (ROOT / path).read_text()
        for key in ("registryPassword", "apiKey"):
            for m in re.finditer(rf"{key}=(\S+)", text):
                assert m[1].startswith("<"), f"{path}: {key} given as a value ({m[1]})"
        assert "ghp_" not in text and "github_pat_" not in text


def test_trivy_config_clean():
    """Passes vacuously for the container app: Trivy's ARM checks do not cover Microsoft.App (it detects no file)."""
    if shutil.which("trivy") is None:
        pytest.skip("trivy is not installed")
    done = subprocess.run(
        "trivy config --exit-code 1 --severity HIGH,CRITICAL deploy/azure",
        shell=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_capture_is_off_unless_a_share_is_given():
    t = load()
    (storage,) = [r for r in t["resources"] if r["type"] == "Microsoft.App/managedEnvironments/storages"]
    assert storage["condition"] == "[not(empty(parameters('captureShareName')))]"
    azure_file = storage["properties"]["azureFile"]
    assert azure_file["shareName"] == "[parameters('captureShareName')]"
    assert azure_file["accountKey"] == "[parameters('captureStorageKey')]"
    assert azure_file["accessMode"] == "ReadWrite"
    the_app = next(r for r in t["resources"] if r["type"] == "Microsoft.App/containerApps")
    assert "storages" in json.dumps(the_app["dependsOn"])
    template = the_app["properties"]["template"]
    c = template["containers"][0]
    for expression in (c["env"], c["volumeMounts"], template["volumes"]):
        assert "empty(parameters('captureShareName'))" in expression
    assert "S1D1_CAPTURE_DIR', 'value', '/capture'" in c["env"]
    assert "'mountPath', '/capture'" in c["volumeMounts"]
    assert "'storageType', 'AzureFile', 'storageName', 's1d1-capture'" in template["volumes"]
