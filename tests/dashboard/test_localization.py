import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from daedalus.server import api

UI = Path("daedalus/dashboard/ui")
CATALOG = UI / "en.js"


def read_catalog() -> dict[str, str]:
  text = CATALOG.read_text(encoding="utf-8")
  prefix = '"use strict";\nglobalThis.DAEDALUS_LOCALE = '
  assert text.startswith(prefix) and text.endswith(";\n")
  pairs = json.loads(text[len(prefix) : -2], object_pairs_hook=list)
  assert pairs and all(isinstance(key, str) for key, _ in pairs)
  assert all(isinstance(value, str) for _, value in pairs)
  keys = [key for key, _ in pairs]
  assert len(keys) == len(set(keys)), "the catalog has duplicate keys"
  return dict(pairs)


def test_english_catalog_is_a_versioned_dashboard_asset() -> None:
  client = TestClient(api.app)
  page = client.get("/")
  assert page.status_code == 200
  assert re.search(r'<script src="ui/en\.js\?v=[0-9a-f]{12}"></script>', page.text)
  assert page.text.index('src="ui/en.js?v=') < page.text.index('src="ui/app.js?v=')
  asset = client.get("/ui/en.js")
  assert asset.status_code == 200
  assert "javascript" in asset.headers["content-type"]
  assert asset.headers["cache-control"] == "no-cache"


def test_every_locale_reference_has_a_catalog_entry() -> None:
  catalog = read_catalog()
  html = (UI / "index.html").read_text(encoding="utf-8")
  app = (UI / "app.js").read_text(encoding="utf-8")
  referenced = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([a-z][a-z0-9_.-]+)"', html))
  referenced.update(re.findall(r'\bt\(\s*["\']([a-z][a-z0-9_.-]+)["\']', app))
  for one, other in re.findall(
    r'\bplural\(\s*[^,]+,\s*["\']([a-z][a-z0-9_.-]+)["\']\s*,\s*'
    r'["\']([a-z][a-z0-9_.-]+)["\']',
    app,
  ):
    referenced.update((one, other))
  assert referenced, "the page references locale keys"
  missing = sorted(referenced - catalog.keys())
  assert not missing, f"locale keys without English copy: {missing}"


def test_locale_keys_are_semantic_and_unique() -> None:
  catalog = read_catalog()
  key = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$")
  invalid = sorted(name for name in catalog if not key.fullmatch(name))
  assert not invalid, invalid


def test_translation_formats_values_and_returns_a_missing_key() -> None:
  app = (UI / "app.js").read_text(encoding="utf-8")
  helper = re.search(
    r"function t\(key, values = \{\}\) \{.*?^\}", app, re.MULTILINE | re.DOTALL
  )
  assert helper, "app.js has the shared translation helper"
  script = f"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {{}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("{CATALOG.as_posix()}", "utf8"), sandbox);
vm.runInContext({json.dumps(helper.group(0))}, sandbox);
const assert = require("assert");
assert.strictEqual(sandbox.t("nav.overview_link"), sandbox.DAEDALUS_LOCALE["nav.overview_link"]);
assert.strictEqual(sandbox.t("test.missing"), "test.missing");
sandbox.DAEDALUS_LOCALE["test.template"] = "{{value}} then {{value}} and {{missing}}";
assert.strictEqual(
  sandbox.t("test.template", {{ value: "again" }}),
  "again then again and {{missing}}",
);
assert.strictEqual(
  sandbox.t("request.count.other_text", {{ count: 3 }}),
  sandbox.DAEDALUS_LOCALE["request.count.other_text"].replace("{{count}}", "3"),
);
"""
  subprocess.run(["node", "-e", script], check=True)


def test_apply_locale_updates_text_and_accessible_attributes() -> None:
  app = (UI / "app.js").read_text(encoding="utf-8")
  preamble = app.split("const POOL_NOTES", 1)[0]
  locale_script = json.dumps(preamble + "\napplyLocale();")
  script = f"""
const fs = require("fs");
const vm = require("vm");
const element = (attributes = {{}}, dataset = {{}}) => ({{
  attributes, dataset, textContent: "",
  getAttribute(name) {{ return this.attributes[name]; }},
  setAttribute(name, value) {{ this.attributes[name] = value; }},
}});
const text = element({{}}, {{ i18n: "nav.overview_link" }});
const missing = element({{}}, {{ i18n: "test.missing" }});
const aria = element({{ "data-i18n-aria-label": "nav.requests_link" }});
const label = element({{ "data-i18n-label": "model.filter.media_label" }});
const placeholder = element({{ "data-i18n-placeholder": "model.filter.placeholder" }});
const title = element({{ "data-i18n-title": "auth.password.show_label" }});
const groups = {{
  "[data-i18n]": [text, missing],
  "[data-i18n-aria-label]": [aria],
  "[data-i18n-label]": [label],
  "[data-i18n-placeholder]": [placeholder],
  "[data-i18n-title]": [title],
}};
const sandbox = {{ document: {{ querySelectorAll: (selector) => groups[selector] || [] }} }};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("{CATALOG.as_posix()}", "utf8"), sandbox);
vm.runInContext({locale_script}, sandbox);
const assert = require("assert");
assert.strictEqual(text.textContent, sandbox.DAEDALUS_LOCALE["nav.overview_link"]);
assert.strictEqual(missing.textContent, "test.missing");
assert.strictEqual(aria.attributes["aria-label"], sandbox.DAEDALUS_LOCALE["nav.requests_link"]);
assert.strictEqual(label.attributes.label, sandbox.DAEDALUS_LOCALE["model.filter.media_label"]);
assert.strictEqual(placeholder.attributes.placeholder, sandbox.DAEDALUS_LOCALE["model.filter.placeholder"]);
assert.strictEqual(title.attributes.title, sandbox.DAEDALUS_LOCALE["auth.password.show_label"]);
"""
  subprocess.run(["node", "-e", script], check=True)


def test_static_copy_uses_locale_keys() -> None:
  html = (UI / "index.html").read_text(encoding="utf-8")
  catalog = read_catalog()

  class CopyAudit(HTMLParser):
    void = frozenset(
      (
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
      )
    )

    def __init__(self) -> None:
      super().__init__()
      self.stack: list[tuple[str, dict[str, str | None]]] = []
      self.direct_text: list[str] = []
      self.direct_attributes: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
      fields = dict(attrs)
      for attribute in ("aria-label", "label", "placeholder", "title"):
        value = fields.get(attribute)
        if (
          value
          and re.search(r"[A-Za-z]", value)
          and fields.get(f"data-i18n-{attribute}") != value
        ):
          self.direct_attributes.append((tag, attribute, value))
      if tag not in self.void:
        self.stack.append((tag, fields))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
      self.handle_starttag(tag, attrs)
      if tag not in self.void:
        self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
      while self.stack:
        opened, _ = self.stack.pop()
        if opened == tag:
          return

    def handle_data(self, data: str) -> None:
      text = " ".join(data.split())
      if not text or not re.search(r"[A-Za-z]", text):
        return
      if any(tag in {"script", "style"} for tag, _ in self.stack):
        return
      fields = self.stack[-1][1] if self.stack else {}
      if fields.get("data-i18n") == text and text in catalog:
        return
      self.direct_text.append(text)

  audit = CopyAudit()
  audit.feed(html)
  assert set(audit.direct_text) == {"daedalus"}, audit.direct_text
  assert not audit.direct_attributes, audit.direct_attributes
