"""Generate a draft release from complete Git history with Gemini."""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT_BYTES = 600_000
PIECE_CHARS = 60_000
MODEL = "gemini-3.5-flash-lite"


def run(*args, input=None):
  result = subprocess.run(
    args, input=input, capture_output=True, check=False, timeout=120
  )
  if result.returncode:
    raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
  return result.stdout.decode("utf-8", errors="backslashreplace")


def git(*args):
  return run("git", "--no-pager", *args)


def api(path, method="GET", payload=None):
  args = ["gh", "api", path, "--method", method]
  data = None
  if payload is not None:
    args += ["--input", "-"]
    data = json.dumps(payload).encode()
  return json.loads(run(*args, input=data))


def releases(repo):
  result = []
  page = 1
  while True:
    rows = api(f"repos/{repo}/releases?per_page=100&page={page}")
    result.extend(rows)
    if len(rows) < 100:
      return result
    page += 1


def tag_sha(tag):
  git("check-ref-format", f"refs/tags/{tag}")
  return git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").strip()


def remote_tag_sha(repo, tag):
  ref = urllib.parse.quote(f"tags/{tag}", safe="/")
  obj = api(f"repos/{repo}/git/ref/{ref}")["object"]
  seen = set()
  while obj["type"] == "tag":
    if obj["sha"] in seen:
      raise RuntimeError("Tag object cycle")
    seen.add(obj["sha"])
    obj = api(f"repos/{repo}/git/tags/{obj['sha']}")["object"]
  if obj["type"] != "commit":
    raise RuntimeError("Release tag does not point to a commit")
  return obj["sha"]


def ancestor(base, target):
  code = subprocess.run(
    ["git", "merge-base", "--is-ancestor", base, target],
    capture_output=True,
    check=False,
    timeout=120,
  ).returncode
  if code not in (0, 1):
    raise RuntimeError("Cannot check release ancestry")
  return code == 0


def baseline(rows, tag, target, previous=""):
  published = [r for r in rows if not r["draft"] and r["tag_name"] != tag]
  if previous:
    published = [r for r in published if r["tag_name"] == previous]
    if not published:
      raise RuntimeError("Previous tag has no published release")
  for row in sorted(published, key=lambda r: r["published_at"], reverse=True):
    sha = tag_sha(row["tag_name"])
    if ancestor(sha, target):
      return row["tag_name"], sha
  if published:
    raise RuntimeError("No published ancestor release; select a previous tag")
  return "", ""


def history(target, base):
  revision = f"{base}..{target}" if base else target
  commits = git("rev-list", "--reverse", "--topo-order", revision).splitlines()
  units = []
  for sha in commits:
    parents = git("rev-list", "--parents", "-n", "1", sha).split()[1:]
    units.append((f"{sha}:message", git("show", "-s", "--format=fuller", sha)))
    for parent in parents or [""]:
      args = [
        "diff-tree",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--binary",
        "--full-index",
        "--no-commit-id",
        "-r",
        "-p",
      ]
      args += [parent, sha] if parent else ["--root", sha]
      units.append((f"{sha}:parent:{parent or 'root'}", git(*args)))
  return units


def pieces(units, size=PIECE_CHARS):
  result = []
  for identity, text in units:
    parts = [text[i : i + size] for i in range(0, len(text), size)] or [""]
    if "".join(parts) != text:
      raise RuntimeError("History split lost data")
    result.extend(
      {"id": f"{identity}:{i + 1}/{len(parts)}", "text": part}
      for i, part in enumerate(parts)
    )
  return result


def encoded(value):
  return json.dumps(value, ensure_ascii=False).encode("utf-8")


def batches(items, context, limit=INPUT_BYTES):
  batch = []
  for item in items:
    if len(encoded({**context, "evidence": batch + [item]})) > limit:
      if not batch:
        raise RuntimeError("One evidence item exceeds request size")
      yield batch
      batch = []
    if len(encoded({**context, "evidence": [item]})) > limit:
      raise RuntimeError("One evidence item exceeds request size")
    batch.append(item)
  if batch:
    yield batch


def gemini_call(model, action, payload):
  if not re.fullmatch(r"[a-zA-Z0-9._-]+", model):
    raise ValueError("Invalid Gemini model ID")
  request = urllib.request.Request(
    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{action}",
    data=encoded(payload),
    headers={
      "Content-Type": "application/json",
      "x-goog-api-key": os.environ["GEMINI_API_KEY"],
    },
  )
  try:
    with urllib.request.urlopen(request, timeout=180) as response:
      return json.load(response)
  except urllib.error.HTTPError as exc:
    raise RuntimeError(f"Gemini {action} failed: HTTP {exc.code}") from None


def response_text(data):
  candidates = data.get("candidates", [])
  if data.get("promptFeedback", {}).get("blockReason") or len(candidates) != 1:
    raise RuntimeError("Gemini returned blocked or missing output")
  candidate = candidates[0]
  if candidate.get("finishReason") != "STOP":
    raise RuntimeError("Gemini output is incomplete or blocked")
  parts = candidate.get("content", {}).get("parts", [])
  text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
  if not text or any(p.get("functionCall") for p in parts):
    raise RuntimeError("Gemini returned no usable release text")
  return text


def generate(context, evidence, model):
  context = dict(context)
  phase = context.pop("phase", "release")
  prompt = (HERE / f"gemini-{phase}-prompt.txt").read_text()
  contents = [
    {
      "role": "user",
      "parts": [
        {"text": json.dumps({**context, "evidence": evidence}, ensure_ascii=False)}
      ],
    }
  ]
  system = {"parts": [{"text": prompt}]}
  request = {"contents": contents, "systemInstruction": system}
  count = gemini_call(
    model,
    "countTokens",
    {"generateContentRequest": {"model": f"models/{model}", **request}},
  )
  if count["totalTokens"] > 900_000:
    raise RuntimeError(
      "Request exceeds token allowance; reduce INPUT_BYTES and PIECE_CHARS"
    )
  data = gemini_call(
    model, "generateContent", {**request, "generationConfig": {"maxOutputTokens": 8192}}
  )
  return response_text(data)


def release_body(units, context, model, generate_fn=generate, limit=INPUT_BYTES):
  items = pieces(units)
  if not items:
    raise RuntimeError("No commits in release range")
  expected = [p["id"] for p in items]
  coverage = [[identity] for identity in expected]
  phase = "chunk"
  while True:
    groups = list(batches(items, context, limit))
    if len(groups) == 1:
      if [identity for group in coverage for identity in group] != expected:
        raise RuntimeError("Summary coverage mismatch")
      return generate_fn(context, groups[0], model)
    reduced, next_coverage = [], []
    offset = 0
    for i, group in enumerate(groups):
      text = generate_fn({**context, "phase": phase}, group, model)
      reduced.append({"id": f"summary:{i}", "text": text})
      next_coverage.append(
        [identity for ids in coverage[offset : offset + len(group)] for identity in ids]
      )
      offset += len(group)
    if len(encoded(reduced)) >= len(encoded(items)):
      raise RuntimeError("Summaries did not shrink; cannot combine all history")
    items, coverage = reduced, next_coverage
    phase = "combine"


def save_draft(repo, tag, body, target):
  rows = releases(repo)
  existing = next((r for r in rows if r["tag_name"] == tag), None)
  if remote_tag_sha(repo, tag) != target:
    raise RuntimeError("Remote release tag moved")
  payload = {
    "tag_name": tag,
    "name": tag,
    "body": body,
    "draft": True,
    "target_commitish": target,
  }
  if existing:
    current = api(f"repos/{repo}/releases/{existing['id']}")
    if not current["draft"]:
      raise RuntimeError("Refusing to change a published release")
    saved = api(f"repos/{repo}/releases/{current['id']}", "PATCH", payload)
  else:
    saved = api(f"repos/{repo}/releases", "POST", payload)
  checked = api(f"repos/{repo}/releases/{saved['id']}")
  if any(checked.get(key) != value for key, value in payload.items()):
    raise RuntimeError("Saved draft differs from requested release")
  return checked["html_url"]


def main():
  repo = os.environ["GITHUB_REPOSITORY"]
  if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
    raise ValueError("Invalid repository")
  if git("rev-parse", "--is-shallow-repository").strip() != "false":
    raise RuntimeError("Full Git history is required")
  tag = os.environ["RELEASE_TAG"]
  target = tag_sha(tag)
  rows = releases(repo)
  if any(r["tag_name"] == tag and not r["draft"] for r in rows):
    raise RuntimeError("Target release is already published")
  previous, base = baseline(rows, tag, target, os.getenv("PREVIOUS_TAG", ""))
  link = (
    f"https://github.com/{repo}/compare/{base}...{target}"
    if base
    else f"https://github.com/{repo}/commits/{target}"
  )
  context = {
    "repository": repo,
    "target_tag": tag,
    "previous_tag": previous,
    "comparison_url": link,
    "template": (HERE / "gemini-release-template.md").read_text(),
  }
  body = release_body(history(target, base), context, os.getenv("GEMINI_MODEL", MODEL))
  headings = re.findall(r"^## .+$", context["template"], re.MULTILINE)
  if (
    re.findall(r"^## .+$", body, re.MULTILINE) != headings
    or link not in body
    or "{{" in body
  ):
    raise RuntimeError("Generated release does not match the template")
  if previous and remote_tag_sha(repo, previous) != base:
    raise RuntimeError("Release tag changed during generation")
  print(save_draft(repo, tag, body, target))


if __name__ == "__main__":
  try:
    main()
  except (
    RuntimeError,
    ValueError,
    KeyError,
    OSError,
    subprocess.TimeoutExpired,
  ) as error:
    print(f"Release generation stopped: {error}", file=sys.stderr)
    sys.exit(1)
