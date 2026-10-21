"""The README "Deploy" row of buttons against the per-target folders: links, badges, guides, sizing, health paths."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {
    "works now",
    "needs a one-time setup",
    "needs public images",
    "needs public repository",
    "needs a template upload (S3)",
}
VARIANT_MIN_RAM = {"full": 2048, "compact": 2048, "small": 1024, "tiny": 512}
FOLDERS = {
    "Azure": "deploy/azure",
    "Hugging Face": "deploy/huggingface",
    "Railway": "deploy/railway",
    "Render": "deploy/render",
    "Google Cloud Run": "deploy/gcp",
    "AWS": "deploy/aws",
    "Heroku": "deploy/heroku",
}
ALTS = [
    "Deploy to Azure",
    "Deploy on Hugging Face",
    "Deploy on Railway",
    "Deploy to Render",
    "Run on Google Cloud",
    "Launch on AWS",
    "Deploy to Heroku",
]
IMAGE = re.compile(r"ghcr\.io/scombat/s1d1-tantive:(\d+\.\d+\.\d+)-(full|compact|small|tiny)-onnx-int8")
BUTTON = re.compile(r"\[!\[([^\]]*)\]\(([^)\s]+)\)\]\(([^)\s]+)\)")
PLACEHOLDER = re.compile(r"<[^>]*>")


def deploy_section() -> str:
    text = (ROOT / "README.md").read_text()
    match = re.search(r"^## Deploy\n(.*?)(?=^## )", text, re.S | re.M)
    assert match, "README has a '## Deploy' section"
    return match.group(1)


def paragraphs() -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", deploy_section().strip())]


def buttons() -> dict[str, dict[str, str]]:
    """The buttons of the row by alt text: {alt: {image, href}} (an unlinked or text button is simply missing)."""
    return {alt: {"image": image, "href": href} for alt, image, href in BUTTON.findall(deploy_section())}


def facts_block(folder: str) -> str:
    for path in sorted((ROOT / folder).glob("*.md")):
        match = re.search(r"^## Facts\n(.*?)(?=^## |\Z)", path.read_text(), re.S | re.M)
        if match:
            return match.group(1)
    raise AssertionError(f"{folder} has no 'Facts' block")


def facts(folder: str) -> dict:
    return dict(re.findall(r"^- (\w[\w ()]*?): (.+)$", facts_block(folder), re.M))


def test_the_section_is_a_sentence_a_row_of_buttons_and_a_caption():
    sentence, row, caption = paragraphs()
    assert "one folder per target under `deploy/`" in sentence.lower()
    assert "same image and variables everywhere" in sentence.lower()
    assert "\n" not in row, "the buttons are on ONE line"
    assert "guide" in caption and "button" in caption
    assert not [line for line in deploy_section().splitlines() if line.startswith("|")], "no table any more"


def test_seven_buttons_in_order_each_a_markdown_image_link():
    assert [m[0] for m in BUTTON.findall(deploy_section())] == ALTS
    row = paragraphs()[1]
    assert not BUTTON.sub("", row).strip(), "nothing but buttons on the row (no text button, no bare URL)"


def test_no_placeholder_in_any_href():
    # a <placeholder> in a clickable link would send readers to a dead page
    for alt, button in buttons().items():
        for url in button.values():
            assert not PLACEHOLDER.search(unquote(url)), f"{alt}: {url}"


OFFICIAL_IMAGES = {
    "Deploy to Azure": "https://aka.ms/deploytoazurebutton",
    "Deploy to Render": "https://render.com/images/deploy-to-render-button.svg",
    "Run on Google Cloud": "https://deploy.cloud.run/button.svg",
    "Deploy to Heroku": "https://www.herokucdn.com/deploy/button.svg",
}
EXPECTED_HREFS = {
    "Deploy to Azure": "https://portal.azure.com/#create/Microsoft.Template/uri/"
    "https://raw.githubusercontent.com/scombat/s1d1-tantive/main/deploy/azure/azuredeploy.json",
    "Deploy to Render": "https://render.com/deploy?repo=https://github.com/scombat/s1d1-tantive",
    "Run on Google Cloud": "https://deploy.cloud.run/?git_repo=https://github.com/scombat/s1d1-tantive&dir=deploy/gcp",
    "Deploy to Heroku": "https://www.heroku.com/deploy?template=https://github.com/scombat/s1d1-tantive",
}


def test_official_buttons_keep_their_image_and_their_exact_url():
    # no fork, no branch, no revision: the exact href of each target (a substring match let a wrong repo through)
    found = buttons()
    for alt, href in EXPECTED_HREFS.items():
        assert unquote(found[alt]["href"]) == href, alt
        assert found[alt]["image"] == OFFICIAL_IMAGES[alt], alt


GUIDES = {
    "Deploy on Hugging Face": "deploy/huggingface/PUBLISH.md",
    "Deploy on Railway": "deploy/railway/README.md",
    "Launch on AWS": "deploy/aws/README.md",
}


def test_buttons_without_a_ready_target_open_their_guide():
    for alt, guide in GUIDES.items():
        href = buttons()[alt]["href"]
        assert href == guide, alt
        assert (ROOT / href).is_file(), f"{alt}: {href} does not exist"


# simple-icons has no Amazon Web Services icon (checked 2026-10-21: no `amazon*` or `aws*` slug): no logo for AWS
BADGES = {
    "Deploy on Hugging Face": ("FFD21E", "huggingface"),
    "Deploy on Railway": ("0B0D0E", "railway"),
    "Launch on AWS": ("FF9900", None),
}


def test_providers_without_an_official_button_get_a_shields_badge():
    for alt, (colour, logo) in BADGES.items():
        url = urlsplit(buttons()[alt]["image"])
        assert (url.scheme, url.netloc) == ("https", "img.shields.io"), alt
        assert url.path.startswith("/badge/"), alt
        label, _, got_colour = unquote(url.path.removeprefix("/badge/")).rpartition("-")
        assert (label, got_colour) == (alt, colour), alt
        query = parse_qs(url.query)
        assert query["style"] == ["for-the-badge"], alt
        assert query.get("logo") == ([logo] if logo else None), alt


def test_guides_say_at_the_top_how_to_replace_the_button_link():
    ready_link = {
        "deploy/huggingface/PUBLISH.md": "https://huggingface.co/spaces/scombat/s1d1-tantive?duplicate=true",
        "deploy/railway/README.md": "https://railway.com/deploy/",
        "deploy/aws/README.md": "console.aws.amazon.com/cloudformation",
    }
    for guide, link in ready_link.items():
        head = (ROOT / guide).read_text().split("\n## ", 1)[0]
        assert "README" in head and "button" in head and "replace" in head.lower(), guide
        assert link in head, guide


def test_aws_guide_defaults_to_eu_west_1_and_says_how_to_change_it():
    text = (ROOT / "deploy/aws/README.md").read_text()
    assert (
        "https://eu-west-1.console.aws.amazon.com/cloudformation/home?region=eu-west-1"
        "#/stacks/create/review?templateURL=<s3-https-url>&stackName=s1d1-tantive"
    ) in text
    assert re.search(r"aws cloudformation deploy[^`]*--region eu-west-1", text, re.S)
    assert "<region>" not in text
    assert re.search(r"change the region.*eu-west-1", " ".join(text.split()), re.I)
    assert "eu-west-1" not in (ROOT / "deploy/aws/template.yaml").read_text(), "the template stays region-free"


def test_every_guide_has_valid_facts():
    for target, folder in FOLDERS.items():
        f = facts(folder)
        assert f["Status"] in STATUSES, target
        assert IMAGE.search(f["Image"]), target
        assert re.match(r"\d+ MiB", f["RAM"]), target


def image_tag_of_manifest(folder: str) -> str | None:
    """The image a target's machine-readable manifest runs, when it has one."""
    if folder.endswith("azure"):
        return json.loads((ROOT / folder / "azuredeploy.json").read_text())["parameters"]["image"]["defaultValue"]
    if folder.endswith("render"):
        return yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]["image"]["url"]
    if folder.endswith("aws"):
        return re.search(r"Default: (ghcr\.io/\S+)", (ROOT / folder / "template.yaml").read_text()).group(1)
    if folder.endswith("railway"):
        return None  # Railway has no file: the guide is the manifest
    return IMAGE.search((ROOT / folder / "Dockerfile").read_text()).group(0)  # Hugging Face, Cloud Run, Heroku


def test_manifest_image_is_the_image_the_guide_declares():
    for target, folder in FOLDERS.items():
        declared = IMAGE.search(facts(folder)["Image"]).group(0)
        manifest = image_tag_of_manifest(folder)
        if manifest is not None:
            assert IMAGE.search(manifest).group(0) == declared, target


def test_variant_fits_declared_ram():
    for target, folder in FOLDERS.items():
        f = facts(folder)
        variant = IMAGE.search(f["Image"]).group(2)
        ram = int(re.match(r"(\d+) MiB", f["RAM"]).group(1))
        assert ram >= VARIANT_MIN_RAM[variant], f"{target}: {variant} needs {VARIANT_MIN_RAM[variant]} MiB, has {ram}"


def test_health_paths():
    # wherever a provider has one readiness check, it is /ready; /health is only a liveness probe
    azure = json.loads((ROOT / "deploy/azure/azuredeploy.json").read_text())["resources"][0]
    probes = azure["properties"]["template"]["containers"][0]["probes"]
    assert {p["type"].lower(): p["httpGet"]["path"] for p in probes} == {
        "liveness": "/health",
        "readiness": "/ready",
        "startup": "/ready",
    }
    assert yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]["healthCheckPath"] == "/ready"
    assert "/ready" in (ROOT / "deploy/aws/template.yaml").read_text()
    assert "/health'" not in (ROOT / "deploy/aws/template.yaml").read_text()
    railway = (ROOT / "deploy/railway/README.md").read_text()
    assert re.search(r"Healthcheck path \| `/ready`", railway)
    for name in ("deploy/huggingface/Dockerfile", "deploy/gcp/Dockerfile", "deploy/heroku/Dockerfile"):
        assert "HEALTHCHECK" not in (ROOT / name).read_text(), name


def test_changelog_lists_the_six_targets_and_their_status():
    sections = (ROOT / "CHANGELOG.md").read_text().split("\n### ")[1:]  # the pre-release entries
    top = next(s for s in sections if s.startswith("0.5.0"))  # the release that added the six targets
    for name in ("Hugging Face", "Railway", "Render", "Cloud Run", "AWS", "Heroku"):
        assert name in top, name
    for status in ("needs a one-time setup", "needs public repository", "needs a template upload (S3)"):
        assert status in top, status


def test_azure_still_needs_registry_parameters_and_its_guide_says_so():
    # azuredeploy.json has no default for registryUsername/registryPassword and always sets a registry
    params = json.loads((ROOT / "deploy/azure/azuredeploy.json").read_text())["parameters"]
    assert "defaultValue" not in params["registryUsername"] and "defaultValue" not in params["registryPassword"]
    azure = " ".join((ROOT / "deploy/azure/README.md").read_text().split())
    assert "images are private" not in azure
    assert "public" in azure and "still asks for" in azure


def test_hugging_face_guide_mentions_the_paid_plan_in_its_facts():
    assert "paid plan" in facts_block("deploy/huggingface")
