"""Check staging isolation and parity with the standalone ODP deployment."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40


@pytest.fixture(scope="module")
def pipeline() -> dict[str, Any]:
    """Load YAML aliases and merged pipeline steps before checking them."""
    return yaml.safe_load((ROOT / "bitbucket-pipelines.yml").read_text())


def test_staging_compose_matches_odp() -> None:
    """Allow only staging identity, image, config, and password-policy changes."""
    expected = (ROOT / "docker-compose-strayos-deploy-odp.yml").read_text()
    for old, new in (
        ("strayos-titiler-odp", "strayos-titiler-staging"),
        ("strayos1/strayos-titiler:app", "strayos1/strayos-titiler:staging"),
        ("com.strayos.stack: odp", "com.strayos.stack: staging"),
        ("nginx-strayos-odp.conf", "nginx-strayos-staging.conf"),
        ("prometheus-odp.yml", "prometheus-staging.yml"),
        ("config-odp.alloy", "config-staging.alloy"),
        ("titilerodp.strayos.com", "titilerstaging.strayos.com"),
        (
            "${GRAFANA_ADMIN_PASSWORD:-SimpleYetComplex}",
            "${GRAFANA_ADMIN_PASSWORD:?Set GRAFANA_ADMIN_PASSWORD in the VM .env}",
        ),
    ):
        expected = expected.replace(old, new)
    actual = (ROOT / "docker-compose-strayos-deploy-staging.yml").read_text()
    assert yaml.safe_load(actual) == yaml.safe_load(expected)


@pytest.mark.parametrize(
    ("odp", "staging", "old", "new"),
    [
        (
            "nginx-strayos-odp.conf",
            "nginx-strayos-staging.conf",
            "titilerodp.strayos.com",
            "titilerstaging.strayos.com",
        ),
        ("prometheus-odp.yml", "prometheus-staging.yml", "", ""),
        (
            "alloy/config-odp.alloy",
            "alloy/config-staging.alloy",
            'regex         = "odp"',
            'regex         = "staging"',
        ),
    ],
)
def test_staging_service_config_matches_odp(
    odp: str, staging: str, old: str, new: str
) -> None:
    """Keep routing, cache, scraping, and log shipping identical to ODP."""
    expected = (ROOT / "dockerfiles" / odp).read_text().replace(old, new)
    assert (ROOT / "dockerfiles" / staging).read_text() == expected


def test_pipeline_targets_are_isolated(pipeline: dict[str, Any]) -> None:
    """Never attach staging to the production deployment environment or main."""
    branches = pipeline["pipelines"]["branches"]
    assert set(branches) == {"main", "staging"}
    staging = branches["staging"]
    assert len(staging) == 2
    assert staging[0]["step"]["name"] == "Build and Push Staging TiTiler Image"
    assert staging[1]["step"]["concurrency-group"] == "titiler-staging-deploy"
    assert all("deployment" not in item["step"] for item in staging)
    main_targets = branches["main"][1]["parallel"]["steps"]
    assert [item["step"]["concurrency-group"] for item in main_targets] == [
        "titiler-app-deploy",
        "titiler-odp-deploy",
    ]
    assert main_targets[0]["step"]["deployment"] == "Production"
    assert branches["main"][1]["parallel"]["fail-fast"] is False


@pytest.mark.parametrize(
    ("branch", "tag"), [("main", "app"), ("staging", f"staging-{COMMIT}")]
)
def test_publish_image_tag(pipeline: dict[str, Any], branch: str, tag: str) -> None:
    """Execute only tag selection, without logging in, building, or publishing."""
    script = pipeline["pipelines"]["branches"][branch][0]["step"]["script"]
    selection = next(
        command for command in script if 'case "$BITBUCKET_BRANCH"' in command
    )
    result = subprocess.run(
        ["bash", "-eu", "-c", selection + '\nprintf "%s" "$TAG"'],
        env={
            "PATH": os.environ["PATH"],
            "BITBUCKET_BRANCH": branch,
            "BITBUCKET_COMMIT": COMMIT,
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == tag


def test_staging_deploy_defaults(pipeline: dict[str, Any]) -> None:
    """Check the real setup script selects only the verified staging target."""
    setup = pipeline["pipelines"]["branches"]["staging"][1]["step"]["script"][0]
    variables = (
        "TITILER_SERVER",
        "TITILER_SSH_USER",
        "DEPLOY_RESOURCE_GROUP",
        "DEPLOY_NSG_NAME",
        "DEPLOY_PRIVATE_IP",
        "DEPLOY_COMPOSE_FILE",
        "DEPLOY_WORKER_COUNT",
        "DEPLOY_IMAGE_TAG",
    )
    output = '\nprintf "%s\\n" ' + " ".join(f'"${name}"' for name in variables)
    result = subprocess.run(
        ["bash", "-eu", "-c", setup + output],
        env={
            "PATH": os.environ["PATH"],
            "SSH_KEY_B64": "test-only",
            "BITBUCKET_COMMIT": COMMIT,
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "titilerstaging.strayos.com",
        "ubuntu",
        "TiTiler",
        "TiTiler-Staging-nsg",
        "172.16.0.4",
        "docker-compose-strayos-deploy-staging.yml",
        "3",
        f"staging-{COMMIT}",
    ]


def test_staging_publish_and_deploy_use_the_same_commit(
    pipeline: dict[str, Any],
) -> None:
    """Publish and deploy the selected commit, not the mutable staging alias."""
    steps = pipeline["pipelines"]["branches"]["staging"]
    publish = "\n".join(steps[0]["step"]["script"])
    deploy_setup = steps[1]["step"]["script"][0]
    assert 'TAG="staging-${BITBUCKET_COMMIT:?Set BITBUCKET_COMMIT}"' in publish
    assert (
        'DEPLOY_IMAGE_TAG="staging-${BITBUCKET_COMMIT:?Set BITBUCKET_COMMIT}"'
        in deploy_setup
    )
    assert 'docker push "${IMAGE_REPO}:${TAG}"' in publish
    assert 'docker push "${IMAGE_REPO}:staging"' in publish


@pytest.mark.parametrize("step", ["publish", "deploy"])
def test_staging_requires_a_commit(pipeline: dict[str, Any], step: str) -> None:
    """Fail safely instead of falling back to a mutable tag without a commit."""
    steps = pipeline["pipelines"]["branches"]["staging"]
    if step == "publish":
        commands = steps[0]["step"]["script"]
        script = next(
            command for command in commands if 'case "$BITBUCKET_BRANCH"' in command
        )
    else:
        script = steps[1]["step"]["script"][0]
    result = subprocess.run(
        ["bash", "-eu", "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "BITBUCKET_BRANCH": "staging",
            "SSH_KEY_B64": "test-only",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Set BITBUCKET_COMMIT" in result.stderr


def test_shared_deploy_preserves_environment_and_selects_tag(
    pipeline: dict[str, Any],
) -> None:
    """Keep Docker authentication, local secrets, and rolling deployment behavior."""
    script = pipeline["pipelines"]["branches"]["staging"][1]["step"]["script"][-1]
    assert 'TAG="${DEPLOY_IMAGE_TAG:-app}"' in script
    assert "--password-stdin" in script
    assert "--exclude=.env" in script
    assert "trap cleanup_nsg_rule EXIT" in script
    assert "--force-recreate titiler-\\$i" in script
    assert script.index("--force-recreate nginx") > script.index(
        "--force-recreate titiler-\\$i"
    )


def test_pipeline_shell_syntax(pipeline: dict[str, Any]) -> None:
    """Parse every defined shell block without executing deployment commands."""
    for item in pipeline["definitions"]["steps"]:
        subprocess.run(
            ["bash", "-n"],
            input="\n".join(item["step"]["script"]),
            check=True,
            capture_output=True,
            text=True,
        )
