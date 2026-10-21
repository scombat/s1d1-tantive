"""Static checks of the GitHub workflows: pinned actions, the build chain's order and gates."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ["full-onnx-int8", "compact-onnx-int8", "small-onnx-int8", "tiny-onnx-int8"]


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def steps_of(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def uses_of(workflow: dict) -> list[str]:
    found = []
    for job in workflow["jobs"].values():
        if "uses" in job:
            found.append(job["uses"])
        for step in job.get("steps", []):
            if "uses" in step:
                found.append(step["uses"])
    return found


# The SLSA generator must be referenced by a full version tag: slsa-verifier cannot verify a reusable workflow pinned
# by hash (slsa-verifier issue #12, documented in the generator's README).
TAG_ONLY = ("slsa-framework/slsa-github-generator/",)


def test_every_action_is_pinned_to_a_commit_sha():
    for name in ("ci.yml", "build.yml"):
        for ref in uses_of(load(name)):
            assert "@" in ref, f"{name}: {ref} is not pinned"
            repo, version = ref.rsplit("@", 1)
            if repo.startswith(TAG_ONLY):
                assert re.fullmatch(r"v\d+\.\d+\.\d+", version), f"{name}: {ref} needs a full vX.Y.Z tag"
            else:
                assert re.fullmatch(r"[0-9a-f]{40}", version), f"{name}: {ref} is not pinned to a commit sha"


def test_every_sha_pin_carries_its_tag_as_a_comment():
    for name in ("ci.yml", "build.yml"):
        for line in (ROOT / ".github" / "workflows" / name).read_text().splitlines():
            m = re.search(r"uses: \S+@([0-9a-f]{40})(.*)$", line)
            if m:
                assert re.fullmatch(r"\s+# v\d+(\.\d+)*", m[2]), f"{name}: {line.strip()} lacks its tag comment"


def test_build_matrix_has_the_four_variants():
    # the variants come from the `version` job (a job-level `if` cannot read `matrix`):
    # all four on a tag, the requested subset on dispatch
    build = load("build.yml")
    plan = next(s for s in steps_of(build, "version") if s.get("id") == "version")["run"]
    assert all(f'"{v}"' in plan for v in VARIANTS)
    for job in ("variant", "variant-manifest", "provenance"):
        assert build["jobs"][job]["strategy"]["matrix"]["variant"] == "${{ fromJSON(needs.version.outputs.variants) }}"
        assert build["jobs"][job]["strategy"]["fail-fast"] is False


def test_build_triggers_on_version_tags_only():
    build = load("build.yml")
    on = build[True] if True in build else build["on"]  # YAML 1.1 reads the key `on` as a boolean
    assert on["push"] == {"tags": ["v*"]}
    assert "workflow_dispatch" in on


def test_build_permissions():
    permissions = load("build.yml")["permissions"]
    assert permissions["packages"] == "write"
    assert permissions["id-token"] == "write"
    assert permissions["contents"] == "read"


def test_trivy_gate_is_blocking_and_ignores_unfixed_only():
    steps = steps_of(load("build.yml"), "variant")
    trivy = next(s for s in steps if str(s.get("uses", "")).startswith("aquasecurity/trivy-action"))
    assert str(trivy["with"]["exit-code"]) == "1"
    assert trivy["with"]["ignore-unfixed"] is True
    assert trivy["with"]["severity"] == "HIGH,CRITICAL"
    assert trivy["with"]["trivyignores"] == ".trivyignore"


def test_push_comes_after_sbom_and_scan():
    steps = steps_of(load("build.yml"), "variant")

    def index(predicate) -> int:
        return next(i for i, s in enumerate(steps) if predicate(s))

    sbom = index(lambda s: str(s.get("uses", "")).startswith("anchore/sbom-action"))
    scan = index(lambda s: str(s.get("uses", "")).startswith("aquasecurity/trivy-action"))
    push = index(
        lambda s: (
            str(s.get("uses", "")).startswith("docker/build-push-action")
            and "push-by-digest=true" in str(s["with"].get("outputs", ""))
        )
    )
    assert sbom < scan < push
    # signing happens on the manifest list, in the job that needs every platform image
    assert "variant" in load("build.yml")["jobs"]["variant-manifest"]["needs"]


def test_variant_images_carry_version_and_model_build_args():
    steps = steps_of(load("build.yml"), "variant")
    build = next(s for s in steps if str(s.get("uses", "")).startswith("docker/build-push-action"))
    for arg in ("VARIANT=", "VERSION=", "GIT_SHA=", "MODEL_REPO=", "MODEL_REVISION=", "MODEL_SHA256="):
        assert arg in build["with"]["build-args"], arg


def test_provenance_is_the_slsa_container_generator_for_a_private_repo():
    job = load("build.yml")["jobs"]["provenance"]
    assert job["uses"].startswith(
        "slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@"
    )
    assert job["with"]["private-repository"] is True
    assert job["with"]["image"] == "ghcr.io/scombat/s1d1-tantive"
    assert "variant-manifest" in job["needs"]


def test_ci_lints_workflows_and_dockerfile():
    ci = load("ci.yml")
    assert "actionlint" in ci["jobs"]
    assert "hadolint" in ci["jobs"]
    assert "rhysd/actionlint:" in str(ci["jobs"]["actionlint"]["steps"])


# --- multi-architecture (task 6): one tag, a manifest list of linux/amd64 and linux/arm64 ---

PLATFORMS = ["linux/amd64", "linux/arm64"]


def build_steps(job: str, action: str) -> list[dict]:
    return [s for s in steps_of(load("build.yml"), job) if str(s.get("uses", "")).startswith(action)]


def test_base_and_variant_jobs_build_both_platforms():
    build = load("build.yml")
    for job in ("base", "variant"):
        assert build["jobs"][job]["strategy"]["matrix"]["platform"] == PLATFORMS, job
        for step in build_steps(job, "docker/build-push-action"):
            assert step["with"]["platforms"] == "${{ matrix.platform }}", job


def test_qemu_is_set_up_and_sha_pinned():
    for job in ("base", "variant"):
        qemu = build_steps(job, "docker/setup-qemu-action")
        assert len(qemu) == 1, job
        assert re.fullmatch(r"[0-9a-f]{40}", qemu[0]["uses"].rsplit("@", 1)[1])


def test_platform_builds_push_by_digest_without_tags():
    for job in ("base", "variant"):
        pushes = [
            s
            for s in build_steps(job, "docker/build-push-action")
            if "push-by-digest=true" in str(s["with"].get("outputs", ""))
        ]
        assert len(pushes) == 1, job
        assert "tags" not in pushes[0]["with"], job
        assert "name-canonical=true" in pushes[0]["with"]["outputs"]


def test_manifest_lists_are_created_from_two_digests():
    build = load("build.yml")
    for job in ("base-manifest", "variant-manifest"):
        creates = [s for s in steps_of(build, job) if "imagetools create" in str(s.get("run", ""))]
        assert len(creates) == 1, job
        run = creates[0]["run"]
        assert run.count("@${{") == 2, run  # exactly two per-platform digests
        assert "amd64" in run and "arm64" in run


def test_scan_and_sbom_run_on_each_platform_image():
    steps = steps_of(load("build.yml"), "variant")
    load_build = next(s for s in build_steps("variant", "docker/build-push-action") if s["with"].get("load") is True)
    assert load_build["with"]["platforms"] == "${{ matrix.platform }}"
    sbom = next(s for s in steps if str(s.get("uses", "")).startswith("anchore/sbom-action"))
    assert "${{ steps.arch.outputs.arch }}" in sbom["with"]["artifact-name"]
    trivy = next(s for s in steps if str(s.get("uses", "")).startswith("aquasecurity/trivy-action"))
    assert trivy["with"]["image-ref"] == load_build["with"]["tags"]


def test_signing_and_provenance_use_the_manifest_list_digest():
    build = load("build.yml")
    manifest_steps = steps_of(build, "variant-manifest")
    sign = next(s for s in manifest_steps if "cosign sign" in str(s.get("run", "")))
    assert "steps.manifest.outputs.digest" in sign["run"]
    attests = [s for s in manifest_steps if "cosign attest" in str(s.get("run", ""))]
    assert attests and all("steps.manifest.outputs.digest" in s["run"] for s in attests)
    assert any("amd64" in s["run"] and "arm64" in s["run"] for s in attests)  # both platform SBOMs attested
    provenance = build["jobs"]["provenance"]
    assert "variant-manifest" in provenance["needs"]
    assert provenance["with"]["digest"] == "${{ needs.variant-manifest.outputs[matrix.variant] }}"


def test_variant_builds_on_the_base_of_its_platform():
    for step in build_steps("variant", "docker/build-push-action"):
        contexts = step["with"]["build-contexts"]
        assert "base=docker-image://ghcr.io/scombat/s1d1-tantive-base@${{ needs.base.outputs[" in contexts


def test_workflows_have_no_cosign_key():
    # keyless since 0.6: a leftover reference to the private key would fail the sign step on every tag
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text()
        assert "COSIGN_PRIVATE_KEY" not in text and "COSIGN_PASSWORD" not in text, path.name
        assert "--key" not in text, f"{path.name}: cosign must run keyless"


def test_signing_job_has_id_token_permission():
    # a job-level `permissions` replaces the workflow-level one, so the job must say it itself
    job = load("build.yml")["jobs"]["variant-manifest"]
    assert job["permissions"]["id-token"] == "write"
    assert job["permissions"]["packages"] == "write"  # the manifest push
    assert job["permissions"]["contents"] == "read"
    for step in job["steps"]:
        run = str(step.get("run", ""))
        if "cosign sign" in run or "cosign attest" in run:
            assert "--yes" in run, "keyless signing needs --yes to skip the prompt"


def test_verify_doc_shows_keyless_command():
    text = (ROOT / "docs" / "verify.md").read_text()
    assert "--certificate-identity-regexp" in text
    assert "https://token.actions.githubusercontent.com" in text
    assert "build\\.yml@refs/tags/v" in text
    assert "cosign.pub" in text and "0.5.0" in text  # the key still verifies the older images
    assert text.index("--certificate-identity-regexp") < text.index("--key cosign.pub")  # keyless comes first


def test_maintainers_notes_say_keyless_needs_nothing():
    text = (ROOT / "MAINTAINERS.md").read_text()
    assert "cosign generate-key-pair" not in text
    assert "keyless" in text.lower()


GITLEAKS_IMAGE = re.compile(r"ghcr\.io/gitleaks/gitleaks:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}")


def test_ci_scans_the_whole_history_for_secrets():
    job = load("ci.yml")["jobs"]["gitleaks"]
    checkout = next(s for s in job["steps"] if "actions/checkout" in s.get("uses", ""))
    assert checkout["with"]["fetch-depth"] == 0  # every commit, or the scan only sees the tip
    run = next(s["run"] for s in job["steps"] if "gitleaks" in str(s.get("run", "")))
    assert GITLEAKS_IMAGE.search(run), "the gitleaks image must be pinned by version and digest"
    assert "--redact" in run and "--log-opts" in run and "--all" in run


def test_gitleaks_allowlists_are_narrow_and_explained():
    import tomllib

    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    assert config["extend"]["useDefault"] is True  # the default rules stay on
    assert "rules" not in config  # no rule disabled
    by_path, by_regex = config["allowlists"]
    assert by_path["paths"] == [r"""^cosign\.pub$"""]  # the public key, by path
    assert by_regex["condition"] == "AND"  # path AND value must match; OR would skip whole files
    assert by_regex["regexTarget"] == "match"  # the detected value only, never the whole line
    assert by_regex["regexes"] == [r"""^[A-Za-z0-9_]*\s*=\s*var\.s1d1_api_key\s*[,}]?\s*$"""]  # anchored both ends
    assert by_regex["paths"] == [r"""^deploy/[^/]+/terraform/.*\.tf$"""]  # the Terraform examples only
    for allow in (by_path, by_regex):
        assert allow["description"].strip()


def test_release_job_publishes_the_github_release_from_the_changelog():
    build = load("build.yml")
    job = build["jobs"]["release"]
    assert "provenance" in job["needs"]
    assert job["if"] == "github.ref_type == 'tag'"
    assert job["permissions"] == {"contents": "write"}  # this job only; the workflow default stays read
    assert all(j.get("permissions", {}).get("contents") != "write" for n, j in build["jobs"].items() if n != "release")
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "tools/release_notes.py" in run
    assert "gh release view" in run  # idempotent: an existing release is left alone
    assert "gh release create" in run and "--notes-file" in run
    assert any("GH_TOKEN" in s.get("env", {}) and "secrets.GITHUB_TOKEN" in s["env"]["GH_TOKEN"] for s in job["steps"])


def test_maintainers_notes_mention_the_release_job():
    assert "`release` job" in (ROOT / "MAINTAINERS.md").read_text()


def test_dependabot_updates_three_ecosystems_weekly_grouped():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    updates = config["updates"]
    assert config["version"] == 2
    assert [u["package-ecosystem"] for u in updates] == ["github-actions", "docker", "uv"]
    assert {u["directory"] for u in updates} == {"/"}
    assert {(u["schedule"]["interval"], u["schedule"]["day"]) for u in updates} == {("weekly", "monday")}
    for u in updates:
        assert u["open-pull-requests-limit"] == 5
        (group,) = u["groups"].values()
        assert group["update-types"] == ["minor", "patch"]
