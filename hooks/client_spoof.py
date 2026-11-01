# ---
# version: 1.0.0
# surfaces: [on-upstream]
# scope: global
# author: nemoe7
# title: Client spoofing
# description: Present one coding harness as another through outbound identity headers.
# license: nemoe7 Noncommercial and Public-Interest License 1.2.0
# settings:
#   - key: rules
#     label: Client rules
#     type: rules
#     columns:
#       - key: source
#         label: From
#         type: choice
#         choices: [owui, kilo, opencode]
#         unique: true
#       - key: target
#         label: To
#         type: choice
#         choices: [owui, kilo, opencode]
#       - key: version
#         label: Version
#         type: text
#         default: latest
#         options: true
#     default:
#       - source: kilo
#         target: opencode
#         version: latest
# ---

import logging
from typing import Any

import httpx

from daedalus.providers import hooks

logger = logging.getLogger("daedalus.hooks")

# Clients that the hook knows, and the only names a rule may use.
CLIENTS = ("owui", "kilo", "opencode")
# The identity headers that a rule replaces. Session and credential headers stay.
IDENTITY = ("user-agent", "x-title", "http-referer", "x-opencode-client")
# The known latest version of each client, used until a discovery refresh lands.
FALLBACK = {"owui": "0.12.5", "kilo": "7.8.18", "opencode": "2.2.5"}
# The discovered version list of each client, filled by the options route, never on the hot path.
_versions: dict[str, list[str]] = {}
TIMEOUT_SECONDS = 10


def source_of(headers: dict[str, Any]) -> str:
  """The client that sent the request, from its identity headers, or an empty text."""
  found = {str(name).lower(): str(value) for name, value in headers.items()}
  agent = found.get("user-agent", "").lower()
  title = found.get("x-title", "").lower()
  if agent.startswith("opencode/") or title == "opencode":
    return "opencode"
  if agent.startswith(("kilo-code/", "kilo/")) or title == "kilo code":
    return "kilo"
  if title == "open webui" or any(name.startswith("x-openwebui-") for name in found):
    return "owui"
  return ""


def latest_version(client: str) -> str:
  """Newest known version of 1 client, from cache or fallback."""
  found = _versions.get(client) or []
  return found[0] if found else FALLBACK.get(client, "0.0.0")


def profile(client: str, version: str) -> dict[str, str]:
  """The identity headers of 1 target client, and no credential or session header."""
  resolved = latest_version(client) if version in ("", "latest") else version
  if client == "opencode":
    return {
      "User-Agent": f"opencode/latest/{resolved}/cli",
      "HTTP-Referer": "https://opencode.ai/",
      "X-Title": "opencode",
      "x-opencode-client": "cli",
    }
  if client == "kilo":
    return {
      "User-Agent": f"Kilo-Code/{resolved}",
      "HTTP-Referer": "https://kilo.ai/",
      "X-Title": "Kilo Code",
    }
  if client == "owui":
    return {"HTTP-Referer": "https://openwebui.com/", "X-Title": "Open WebUI"}
  return {}


def rules_ok(rules: Any) -> bool:
  """True when each rule uses supported clients, and each source stays unique."""
  if not isinstance(rules, list):
    return False
  seen: set[str] = set()
  for rule in rules:
    if not isinstance(rule, dict):
      return False
    source, target = rule.get("source"), rule.get("target")
    if source not in CLIENTS or target not in CLIENTS or source in seen:
      return False
    if not isinstance(rule.get("version", "latest"), str):
      return False
    seen.add(source)
  return True


def on_upstream(body: dict, model: str, headers: dict) -> None:
  """Apply the first rule whose source matches the request, and keep every other header."""
  rules = hooks.values(__file__).get("rules") or []
  if not rules_ok(rules):
    return
  source = source_of(headers)
  if not source:
    return
  for rule in rules:
    if rule.get("source") != source:
      continue
    for name in IDENTITY:
      headers.pop(name, None)
    headers.update(profile(str(rule["target"]), str(rule.get("version", "latest"))))
    return


def _npm_versions(package: str) -> list[str]:
  """The stable release versions of 1 npm package, newest first."""
  response = httpx.get(f"https://registry.npmjs.org/{package}", timeout=TIMEOUT_SECONDS)
  response.raise_for_status()
  tags = response.json().get("dist-tags") or {}
  versions = response.json().get("versions") or {}
  stable = [name for name in versions if name.replace(".", "").isdigit()]
  stable.sort(
    key=lambda name: tuple(int(part) for part in name.split(".")), reverse=True
  )
  latest = tags.get("latest")
  if latest and latest in stable:
    stable.remove(latest)
    stable.insert(0, latest)
  return stable


def _pypi_versions(package: str) -> list[str]:
  """The release versions of 1 PyPI package, newest first."""
  response = httpx.get(f"https://pypi.org/pypi/{package}/json", timeout=TIMEOUT_SECONDS)
  response.raise_for_status()
  data = response.json()
  releases = data.get("releases") or {}
  stable = [name for name in releases if name.replace(".", "").isdigit()]
  stable.sort(
    key=lambda name: tuple(int(part) for part in name.split(".")), reverse=True
  )
  latest = (data.get("info") or {}).get("version")
  if latest and latest in stable:
    stable.remove(latest)
    stable.insert(0, latest)
  return stable


def discover(client: str) -> list[str]:
  """Version list of 1 client, latest first, from registry or fallback."""
  if client in _versions:
    return _versions[client]
  found: list[str] = []
  try:
    if client == "owui":
      found = _pypi_versions("open-webui")
    elif client == "kilo":
      found = _npm_versions("@kilocode/cli")
    elif client == "opencode":
      found = _npm_versions("@opencode/cli")
  except (httpx.HTTPError, ValueError, KeyError):
    logger.info("client spoof version discovery failed for %s", client)
  if not found:
    found = [FALLBACK[client]]
  _versions[client] = found
  return found


def options(key: str, column: str, row: dict[str, Any]) -> list[str]:
  """The version choices of the target client of 1 rule row, with latest first."""
  if key != "rules" or column != "version":
    return []
  client = str(row.get("target") or "")
  if client not in CLIENTS:
    return []
  return ["latest", *discover(client)]
