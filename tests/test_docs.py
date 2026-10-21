"""Documentation checks: the README stays a front door, links resolve, no secret literal sneaks into an example."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
GUIDES = sorted((ROOT / "docs").glob("*.md"))
# deploy/huggingface/README.md is the Space card (front matter, shown on the Hub): its guide is PUBLISH.md
DEPLOY_READMES = sorted(
    p if p.parent.name != "huggingface" else p.with_name("PUBLISH.md") for p in (ROOT / "deploy").glob("*/README.md")
)
MARKDOWN = [
    README,
    ROOT / "deploy/huggingface/README.md",
    *GUIDES,
    *DEPLOY_READMES,
    ROOT / "MAINTAINERS.md",
    ROOT / "CHANGELOG.md",
    ROOT / "SECURITY.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CODE_OF_CONDUCT.md",
    ROOT / ".github/PULL_REQUEST_TEMPLATE.md",
]

SECTIONS = [
    "## What you get",
    "## Deploy",
    "## Documentation",
    "## Quickstart",
    "## Test it",
    "## Verify the image",
    "## LiteLLM",
    "## Licence",
]
HERO = "assets/hero.svg"
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
LINK = re.compile(r"\]\(([^)\s]+)\)")
SECRET = re.compile(r"ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN")
# a key exported with a literal value, or a bearer token that is neither a variable nor a <placeholder>
KEY_LITERAL = re.compile(r"\b\w*(?:KEY|TOKEN|PASSWORD)=(?![<$\"'])\S+|Bearer\s+(?![$<])\S+")


def lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def prose(path: Path) -> str:
    """The file without fenced blocks and inline code: what a reader sees as text and links."""
    return re.sub(r"`[^`\n]*`", "", FENCE.sub("", path.read_text()))


def test_readme_length():
    assert len(lines(README)) <= 130


def test_readme_section_order():
    heads = [line for line in lines(README) if line.startswith("## ")]
    assert heads == SECTIONS


def test_readme_what_you_get_is_eight_to_ten_one_line_bullets():
    text = README.read_text()
    block = text.split("## What you get\n", 1)[1].split("\n## ", 1)[0]
    bullets = [line for line in block.splitlines() if line.strip()]
    assert 8 <= len(bullets) <= 10
    assert all(line.startswith("- ") and len(line) <= 160 for line in bullets), bullets


def test_readme_shows_the_hero_after_the_badges():
    head = "\n".join(lines(README)[:12])
    assert f'<img src="{HERO}"' in head and 'width="100%"' in head
    assert head.index(HERO) > head.index("huggingface.co/collections/")
    svg = (ROOT / HERO).read_text()
    assert "<script" not in svg and "http" not in svg.replace("http://www.w3.org", "")


def test_readme_opens_with_badges_then_what_it_is():
    top = lines(README)
    assert top[0] == "# s1d1-tantive"
    first_block = next(i for i, line in enumerate(top) if line.startswith("## "))
    intro = "\n".join(top[1:first_block])
    assert intro.count("img.shields.io") + intro.count("slsa.dev") >= 6
    paragraphs = [p for p in re.split(r"\n\s*\n", intro) if p.strip() and "img.shields.io" not in p and HERO not in p]
    assert len(paragraphs) == 2  # s1d1 the model, then this service


def test_docs_links_resolve():
    broken = []
    for md in MARKDOWN:
        for target in LINK.findall(prose(md)):
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not (md.parent / path).exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, broken


def test_readme_links_every_guide():
    text = README.read_text()
    for guide in [
        *GUIDES,
        *(ROOT / n for n in ("MAINTAINERS.md", "CHANGELOG.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md")),
    ]:
        assert guide.relative_to(ROOT).as_posix() in text, guide.name
    assert {g.name for g in GUIDES} >= {
        "getting-started.md",
        "litellm.md",
        "formats.md",
        "configuration.md",
        "verify.md",
    }


def test_no_secret_literals():
    for md in [*MARKDOWN, *ROOT.glob("deploy/*/*.md")]:
        text = md.read_text()
        assert not SECRET.search(text), f"{md.relative_to(ROOT)}: token-like literal"
        assert not KEY_LITERAL.search(text), f"{md.relative_to(ROOT)}: {KEY_LITERAL.search(text).group(0)}"


def test_badges_target_existing_workflows():
    badges = re.findall(r"actions/workflows/([\w.-]+?)/badge\.svg", README.read_text())
    assert sorted(badges) == ["build.yml", "ci.yml"]
    for name in badges:
        assert (ROOT / ".github/workflows" / name).is_file(), name


def test_badges_cover_release_licence_slsa_and_hub():
    head = "\n".join(lines(README)[:12])
    assert "shields.io/github/v/release/scombat/s1d1-tantive" in head
    assert "BSL-1.1" in head
    assert "SLSA" in head and "slsa.dev" in head
    assert "huggingface.co/scombat/s1d1-router)" in head  # the model
    assert "huggingface.co/collections/" in head  # the variants
    assert "huggingface.co/spaces/scombat/s1d1-tantive" not in head  # no Space badge before the Space exists


def test_guides_answer_one_question_and_stay_short():
    for md in [*GUIDES, *DEPLOY_READMES, ROOT / "MAINTAINERS.md", ROOT / "CONTRIBUTING.md"]:
        body = [line for line in lines(md) if line.strip() and not line.startswith("#")]
        assert body[0].startswith("This page shows how to"), md.relative_to(ROOT)
        assert len(lines(md)) <= 130, md.relative_to(ROOT)


def test_deploy_guides_share_one_shape():
    for md in DEPLOY_READMES:
        text = md.read_text()
        assert "\n## Facts\n" in text, md
        assert "```" in text, f"{md}: a manual command"


SECURITY = ROOT / "SECURITY.md"


def test_security_md_exists_and_is_linked_from_readme():
    text = SECURITY.read_text()
    for needle in ("Supported versions", "Reporting a vulnerability", "7 days", "Scope", "security/advisories"):
        assert needle in text, needle
    assert "Security: see [SECURITY.md](SECURITY.md)" in README.read_text()


def test_security_md_has_no_placeholder():
    assert "<security contact>" not in SECURITY.read_text()


LLMS = ROOT / "llms.txt"


def test_llms_txt_follows_the_llmstxt_convention():
    rows = lines(LLMS)
    assert rows[0] == "# s1d1-tantive"
    assert rows[2].startswith("> ")
    heads = [r for r in rows if r.startswith("## ")]
    assert heads[-1] == "## Optional"
    assert "## How to choose" in heads


def test_llms_txt_links_resolve_and_cover_the_guides():
    text = LLMS.read_text()
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    for target in targets:
        if not re.match(r"[a-z][a-z0-9+.-]*:", target):
            assert (ROOT / target.split("#", 1)[0]).exists(), target
    for guide in GUIDES:
        assert guide.relative_to(ROOT).as_posix() in targets, guide.name
    assert "docs/sizing.md" in text and "docs/use-cases.md" in text


ISSUE_TEMPLATES = ROOT / ".github/ISSUE_TEMPLATE"


def test_issue_forms_ask_for_the_same_facts():
    for name in ("bug_report.yml", "question.yml"):
        form = yaml.safe_load((ISSUE_TEMPLATES / name).read_text())
        assert form["name"] and form["description"], name
        labels = [f["attributes"]["label"] for f in form["body"] if f["type"] != "markdown"]
        assert labels == [
            "Version or image tag",
            "Variant",
            "Deployment target",
            "What you did",
            "What you saw",
            "What you expected",
        ], name
        assert all(f.get("id") for f in form["body"] if f["type"] != "markdown"), name


def test_issue_config_sends_vulnerabilities_to_the_advisory_of_security_md():
    config = yaml.safe_load((ISSUE_TEMPLATES / "config.yml").read_text())
    assert config["blank_issues_enabled"] is False
    (link,) = config["contact_links"]
    assert link["url"] in SECURITY.read_text()


def test_code_of_conduct_is_the_contributor_covenant_with_the_security_contact():
    text = (ROOT / "CODE_OF_CONDUCT.md").read_text()
    assert text.startswith("# Contributor Covenant Code of Conduct\n")
    assert "version 2.1" in text and "[INSERT CONTACT METHOD]" not in text
    email = re.search(r"`([^`]+@[^`]+)`", SECURITY.read_text()).group(1)
    assert f"enforcement at {email}." in text


def test_pull_request_template_has_three_checkboxes():
    text = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    assert text.count("- [ ] ") == 3
