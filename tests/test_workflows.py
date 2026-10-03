"""Every workflow file: its name, its triggers, its permissions and its tools."""

import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from ruamel.yaml import YAML

WORKFLOWS = Path(".github/workflows")
FOLDER = WORKFLOWS

NAMES = {
  "ci.yml": "CI",
  "docker.yml": "Docker",
  "gemini-release.yml": "Gemini Release Draft",
  "codeql.yml": "CodeQL",
  "dependency-review.yml": "Dependency review",
  "container-scan.yml": "Container scan",
  "container-trivy.yml": "Container scan (Trivy)",
  "secret-scan.yml": "Secret scan",
  "workflow-security.yml": "Workflow security",
  "pr-check.yml": "PR title check",
  "pages.yml": "Pages demo",
}

# The workflows that must answer a pull request, and the ones that must answer main.
ON_PULL_REQUEST = (
  "ci.yml",
  "codeql.yml",
  "dependency-review.yml",
  "container-scan.yml",
  "container-trivy.yml",
  "secret-scan.yml",
  "workflow-security.yml",
  "pr-check.yml",
)
ON_MAIN = (
  "ci.yml",
  "pages.yml",
  "codeql.yml",
  "dependency-review.yml",
  "container-scan.yml",
  "container-trivy.yml",
  "secret-scan.yml",
  "workflow-security.yml",
)
ON_DISPATCH = (
  "docker.yml",
  "pages.yml",
  "dependency-review.yml",
  "container-scan.yml",
  "container-trivy.yml",
  "secret-scan.yml",
  "workflow-security.yml",
)


def load(name: str) -> dict:
  return YAML(typ="safe").load((WORKFLOWS / name).read_text())


def triggers(name: str) -> dict:
  data = load(name)
  return data["on"] if "on" in data else data[True]


def test_every_workflow_file_has_the_agreed_name() -> None:
  """Each file carries the name the handoff table gives it."""
  assert sorted(path.name for path in WORKFLOWS.glob("*.yml")) == sorted(NAMES)
  for name, expected in NAMES.items():
    assert load(name)["name"] == expected, name


def test_the_workflows_that_answer_a_pull_request_do() -> None:
  """A pull request runs the checks, the scans and the title rule."""
  for name in ON_PULL_REQUEST:
    event = (
      "pull_request"
      if name in ("ci.yml", "container-trivy.yml")
      else "pull_request_target"
    )
    assert event in triggers(name), name


def test_the_workflows_that_answer_main_do() -> None:
  """A push to main runs the checks and the scans again on the merged tree."""
  for name in ON_MAIN:
    found = triggers(name)
    assert "push" in found, name
    assert found["push"]["branches"] == ["main"], name


def test_the_workflows_that_offer_a_manual_run_do() -> None:
  """An operator can start these from the Actions tab."""
  for name in ON_DISPATCH:
    assert "workflow_dispatch" in triggers(name), name


def test_no_workflow_asks_for_write_all_permissions() -> None:
  """Least privilege: no file grants every scope at once."""
  for name in NAMES:
    data = load(name)
    assert "permissions" in data, name
    assert data["permissions"] not in ("read-all", "write-all"), name
    for job in data["jobs"].values():
      if "permissions" in job:
        assert job["permissions"] not in ("read-all", "write-all"), name


def test_codeql_scans_python_and_the_workflows_only() -> None:
  """daedalus is Python and Actions. No JavaScript enters the scan."""
  matrix = load("codeql.yml")["jobs"]["analyze"]["strategy"]["matrix"]
  assert matrix["language"] == ["python", "actions"]
  assert "javascript" not in matrix["language"]
  assert (
    load("codeql.yml")["jobs"]["analyze"]["permissions"]["security-events"] == "write"
  )


def test_only_the_main_jobs_write_code_scanning_results() -> None:
  """security-events: write belongs to the jobs that run on main, and nowhere else."""
  for name in ("container-trivy.yml", "workflow-security.yml"):
    for job_id, job in load(name)["jobs"].items():
      if "permissions" not in job:
        continue
      granted = job["permissions"]
      if "security-events" in granted and granted["security-events"] == "write":
        assert job["if"] == "github.event_name == 'push'", (name, job_id)


def test_dependency_workflows_use_the_uv_model() -> None:
  """The dependency model is pyproject.toml and uv.lock, never a requirements file."""
  content = (WORKFLOWS / "dependency-review.yml").read_text()
  assert "uv.lock" in content
  assert "pyproject.toml" in content
  assert "uv lock --check" in content
  assert "uv export" in content
  assert "requirements.txt" not in content


def test_the_pr_title_uses_the_local_contract_checker() -> None:
  """The title and commit jobs share the copied subject rules, not a title action."""
  data = load("pr-check.yml")
  assert data["name"] == "PR title check"
  step = data["jobs"]["title"]["steps"][-1]
  assert step["run"] == 'python3 scripts/check_pr.py --pr-title "$PR_TITLE"'
  assert step["env"]["PR_TITLE"] == "${{ github.event.pull_request.title }}"
  assert "action-semantic-pull-request" not in (WORKFLOWS / "pr-check.yml").read_text()


def test_the_gemini_release_helper_stays_in_place() -> None:
  """gemini_release.py drives the release workflow, and the workflow still calls it."""
  assert (WORKFLOWS / "gemini_release.py").exists()

  content = (WORKFLOWS / "gemini-release.yml").read_text()
  assert "python .github/workflows/gemini_release.py" in content
  assert (WORKFLOWS / "gemini-release.yml").exists()


def test_one_docker_workflow_publishes_every_image() -> None:
  """No second file publishes to GHCR. The dev and release jobs live in docker.yml."""
  assert not (WORKFLOWS / "dev-image.yml").exists()
  assert not (WORKFLOWS / "image.yml").exists()
  assert sorted(load("docker.yml")["jobs"]) == ["dev", "release"]


def test_the_secret_scan_reads_the_whole_history() -> None:
  """A secret from years ago still fails the scan, so the checkout is not shallow."""
  content = (WORKFLOWS / "secret-scan.yml").read_text()
  assert "gitleaks" in content
  assert "fetch-depth: 0" in content


def test_the_container_scan_uses_hadolint() -> None:
  """Hadolint lints the Dockerfile, and the pull request check blocks."""
  content = (WORKFLOWS / "container-scan.yml").read_text()
  assert "hadolint/hadolint-action" in content
  assert "continue-on-error" not in content
  assert Path(".hadolint.yaml").exists()


def test_the_workflow_security_scan_uses_actionlint_and_zizmor() -> None:
  """Both tools run, and a config tells zizmor how this repository pins actions."""
  content = (WORKFLOWS / "workflow-security.yml").read_text()
  assert "raven-actions/actionlint" in content
  assert "zizmorcore/zizmor-action" in content
  assert (Path(".github") / "zizmor.yml").exists()


def test_governance_runs_only_from_trusted_base_code() -> None:
  """A PR cannot select the governance workflow or its executable code."""
  data = load("pr-check.yml")
  assert "pull_request_target" in data["on"]
  assert "pull_request" not in data["on"]
  assert "github.event.pull_request.number" in data["concurrency"]["group"]
  for job_id in ("title", "body", "commits"):
    checkout = data["jobs"][job_id]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] is False
  assert data["permissions"] == {"contents": "read"}


def test_pr_files_cannot_replace_the_trusted_checks() -> None:
  """PR programs and a disabled PR workflow cannot change trusted check results."""
  with TemporaryDirectory() as directory:
    root = Path(directory)
    base = root / "base"
    head = root / "head"
    base.mkdir()
    head.mkdir()
    marker = root / "executed"
    for name in (
      "scripts/check_pr.py",
      ".agents/skills/asd-ste100/scripts/ste-lint.py",
    ):
      trusted = base / name
      trusted.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(name, trusted)
      malicious = head / name
      malicious.parent.mkdir(parents=True, exist_ok=True)
      malicious.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
      )
    disabled = head / ".github/workflows/pr-check.yml"
    disabled.parent.mkdir(parents=True)
    disabled.write_text("on: workflow_dispatch\njobs: {}\n", encoding="utf-8")
    (base / "pr-body.md").write_text("An invalid description.\n", encoding="utf-8")
    (base / "commits.bin").write_text("invalid subject\x00", encoding="utf-8")
    (base / "dirty.md").write_text("The job checks files; the job reads data.\n")
    workflow = load("pr-check.yml")
    assert "pull_request_target" in workflow["on"]
    body_steps = workflow["jobs"]["body"]["steps"]
    command = next(
      s["run"] for s in body_steps if s.get("name") == "Check the structure"
    )
    commands = (
      shlex.split(command)[1:],
      ["scripts/check_pr.py", "--pr-title", "invalid subject"],
      [".agents/skills/asd-ste100/scripts/ste-lint.py", "dirty.md"],
    )
    for args in commands:
      result = subprocess.run(
        [sys.executable, *args], cwd=base, capture_output=True, check=False
      )
      assert result.returncode == 1, result.stdout + result.stderr
    assert not marker.exists()
    assert disabled.read_text() == "on: workflow_dispatch\njobs: {}\n"


def test_the_pr_check_validates_the_body_prose_and_commits() -> None:
  """One workflow holds the title, the description and the commit checks."""
  data = load("pr-check.yml")
  assert sorted(data["jobs"]) == ["body", "commits", "title"]

  content = (WORKFLOWS / "pr-check.yml").read_text()
  assert "scripts/check_pr.py --body-file" in content
  assert "scripts/check_pr.py --range" in content
  assert "ste-lint.py --selftest" in content
  assert "ste-lint.py pr-body.md" in content
  # The vendored linter runs on the runner Python. No skill install, no new package.
  assert "install.sh" not in content
  assert "pip install" not in content
  assert "uv sync" not in content


def test_read_only_security_gates_keep_their_workflow_and_policy_trusted() -> None:
  """Security jobs read PR data with base commands and base policy."""
  for name in (
    "codeql.yml",
    "container-scan.yml",
    "dependency-review.yml",
    "secret-scan.yml",
    "workflow-security.yml",
    "pr-check.yml",
  ):
    data = load(name)
    assert "pull_request_target" in data["on"], name
    assert "pull_request" not in data["on"], name
    assert "github.event.pull_request.number" in data["concurrency"]["group"]
    for job in data["jobs"].values():
      for step in job["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
          assert step["with"]["ref"] == "${{ github.sha }}", name
          assert step["with"]["persist-credentials"] is False, name
  codeql = load("codeql.yml")
  init = next(
    s for s in codeql["jobs"]["analyze"]["steps"] if "init@" in s.get("uses", "")
  )
  assert init["with"]["build-mode"] == "none"
  assert init["with"]["source-root"] == "${{ env.SOURCE }}"
  assert "uv sync" not in (WORKFLOWS / "codeql.yml").read_text()
  hadolint = load("container-scan.yml")["jobs"]["hadolint"]["steps"][-1]
  assert hadolint["with"]["config"] == ".hadolint.yaml"
  steps = load("workflow-security.yml")["jobs"]
  assert (
    steps["actionlint"]["steps"][-1]["with"]["flags"]
    == "-config-file .github/actionlint.yaml"
  )
  assert steps["zizmor"]["steps"][-1]["with"]["config"] == ".github/zizmor.yml"
  gitleaks = (WORKFLOWS / "secret-scan.yml").read_text()
  assert "--log-opts=--all" in gitleaks
  assert "sha256sum --check" in gitleaks
  assert "gitleaks/gitleaks-action" not in gitleaks
  for name in ("ci.yml", "container-trivy.yml"):
    assert "pull_request_target" not in load(name)["on"]


def test_the_pages_demo_builds_the_page_and_deploys_it() -> None:
  """The demo goes to Pages from an artifact, and only main deploys it."""
  data = load("pages.yml")
  assert "pull_request" not in (triggers("pages.yml"), triggers("pages.yml"))
  assert triggers("pages.yml")["push"]["paths"] == [
    "daedalus/dashboard/ui/**",
    "scripts/pages_demo.py",
    "scripts/pages_fixtures.json",
    ".github/workflows/pages.yml",
  ]
  build, deploy = data["jobs"]["build"], data["jobs"]["deploy"]
  assert build["steps"][-1]["with"]["path"] == "_site"
  assert "pages" not in build.get("permissions", {}), "the build does not deploy"
  assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
  assert deploy["environment"]["name"] == "github-pages"
  assert deploy["steps"][-1]["uses"].startswith("actions/deploy-pages@")
  assert deploy["steps"][-1]["id"] == "deployment"
