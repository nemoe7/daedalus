"""Persistent CDP browser sessions, 42 MCP verbs, auto-crawler, GIF89a, and @step API."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
  from collections.abc import Callable, Mapping, Sequence
  from typing import Any

DEVICE_INDEX: dict[str, int] = {
  "desktop": 0,
  "laptop": 1,
  "tablet": 2,
  "landscape": 3,
  "mobile": 4,
}

MCP_VERBS: tuple[str, ...] = (
  "open",
  "close",
  "goto",
  "back",
  "forward",
  "reload",
  "tab",
  "snapshot",
  "find",
  "click",
  "dblclick",
  "hover",
  "drag",
  "fill",
  "type",
  "select",
  "check",
  "uncheck",
  "upload",
  "press",
  "wheel",
  "mouse-xy",
  "dialog",
  "resize",
  "emulate",
  "console",
  "requests",
  "request",
  "route",
  "unroute",
  "offline",
  "eval",
  "storage",
  "cookie",
  "localstorage",
  "sessionstorage",
  "wait",
  "screenshot",
  "pdf",
  "gif-start",
  "gif-frame",
  "gif-stop",
)

_REGISTERED_STEPS: list[tuple[str, str, Callable[[Walker, Demo], None]]] = []

_INIT_TRACKER_JS = r"""
(() => {
  if (window.__nuuWalkInstalled) return;
  window.__nuuWalkInstalled = true;
  window.__nuuConsole = window.__nuuConsole || [];
  window.__nuuRequests = window.__nuuRequests || [];
  for (const level of ['log', 'info', 'warn', 'error']) {
    const orig = console[level] ? console[level].bind(console) : () => {};
    console[level] = (...args) => {
      try {
        window.__nuuConsole.push({
          level: level === 'warn' ? 'warning' : (level === 'log' ? 'info' : level),
          text: args.map((v) => (typeof v === 'string' ? v : JSON.stringify(v))).join(' '),
        });
      } catch (_err) {}
      return orig(...args);
    };
  }
  window.addEventListener('error', (ev) => {
    window.__nuuConsole.push({ level: 'error', text: String(ev.message || ev.error || 'error') });
  });
  if (typeof window.fetch === 'function') {
    const origFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : (input && input.url ? input.url : String(input));
      const method = (init && init.method) || (input && input.method) || 'GET';
      const entry = { method: String(method).toUpperCase(), url, status: 0 };
      window.__nuuRequests.push(entry);
      try {
        const resp = await origFetch(input, init);
        entry.status = resp.status;
        return resp;
      } catch (err) {
        entry.status = -1;
        entry.error = String(err);
        throw err;
      }
    };
  }
})();
"""

_SNAPSHOT_PROBE_JS = r"""
({ includeBoxes, maxDepth, rootSelector }) => {
  const root = rootSelector ? document.querySelector(rootSelector) : document.body;
  if (!root) return { tree: '', refs: {} };
  let counter = 0;
  const refs = {};
  const lines = [];

  function cssPath(el) {
    if (el.id && /^[A-Za-z_][A-Za-z0-9_:-]*$/.test(el.id)) {
      return `#${CSS.escape(el.id)}`;
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body && cur !== document.documentElement) {
      let seg = cur.tagName.toLowerCase();
      if (cur.id) {
        seg = `#${CSS.escape(cur.id)}`;
        parts.unshift(seg);
        break;
      }
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (same.length > 1) {
          seg += `:nth-of-type(${same.indexOf(cur) + 1})`;
        }
      }
      parts.unshift(seg);
      cur = parent;
    }
    return parts.length ? `body > ${parts.join(' > ')}` : 'body';
  }

  function roleFor(el) {
    const explicit = (el.getAttribute('role') || '').trim();
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') return `input[${(el.getAttribute('type') || 'text').toLowerCase()}]`;
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (/^h[1-6]$/.test(tag)) return `heading[${tag}]`;
    if (tag === 'dialog') return 'dialog';
    if (tag === 'nav') return 'navigation';
    if (tag === 'main') return 'main';
    return tag;
  }

  function labelFor(el) {
    const aria = (el.getAttribute('aria-label') || '').trim();
    if (aria) return aria;
    if (el.labels && el.labels.length) {
      const lt = Array.from(el.labels).map((l) => (l.textContent || '').trim()).join(' ').trim();
      if (lt) return lt;
    }
    const direct = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    if (direct) return direct.slice(0, 80);
    return (el.getAttribute('placeholder') || el.getAttribute('title') || el.value || '').trim();
  }

  const refSel = [
    'a[href], button, input:not([type="hidden"]), select, textarea, summary',
    'dialog, [role="button"], [role="link"], [role="tab"], [role="checkbox"]',
    '[role="menuitem"], h1, h2, h3, [data-testid]',
  ].join(', ');

  function visit(node, depth) {
    if (!node || node.nodeType !== 1) return;
    if (maxDepth !== null && depth > maxDepth) return;
    const tag = node.tagName.toUpperCase();
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(tag)) return;
    const cs = getComputedStyle(node);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const rect = node.getBoundingClientRect();
    const notable = node.matches(refSel);
    if (notable && (rect.width > 0 || rect.height > 0 || tag === 'DIALOG')) {
      counter += 1;
      const refId = `e${counter}`;
      const sel = cssPath(node);
      const role = roleFor(node);
      const name = labelFor(node);
      const box = {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
      refs[refId] = { selector: sel, role, name, box };
      const indent = '  '.repeat(Math.min(depth, 8));
      const boxTxt = includeBoxes ? ` @${box.x},${box.y},${box.width}x${box.height}` : '';
      lines.push(`${indent}- [${refId}] ${role}${name ? ` "${name}"` : ''}${boxTxt}`);
    }
    for (const child of node.children) {
      visit(child, notable ? depth + 1 : depth);
    }
  }

  visit(root, 0);
  return { tree: lines.join('\n'), refs };
}
"""


# Pure-Python GIF89a median-cut quantizer and variable-width LZW encoder.


def _median_cut_palette(
  samples: list[tuple[int, int, int]], max_colors: int = 256
) -> list[tuple[int, int, int]]:
  if not samples:
    return [(0, 0, 0)] * max_colors
  unique = list(dict.fromkeys(samples))
  if len(unique) <= max_colors:
    return unique + [(0, 0, 0)] * (max_colors - len(unique))

  boxes: list[list[tuple[int, int, int]]] = [samples]
  while len(boxes) < max_colors:
    best_idx = -1
    best_span = -1
    best_ch = 0
    for idx, box in enumerate(boxes):
      if len(box) <= 1:
        continue
      rs = [c[0] for c in box]
      gs = [c[1] for c in box]
      bs = [c[2] for c in box]
      spans = (max(rs) - min(rs), max(gs) - min(gs), max(bs) - min(bs))
      ch = max(range(3), key=lambda k: spans[k])
      if spans[ch] > best_span:
        best_span = spans[ch]
        best_idx = idx
        best_ch = ch
    if best_idx < 0 or best_span <= 0:
      break
    target = sorted(boxes.pop(best_idx), key=lambda c: c[best_ch])
    mid = len(target) // 2
    boxes.append(target[:mid])
    boxes.append(target[mid:])

  palette: list[tuple[int, int, int]] = []
  for box in boxes:
    n = max(1, len(box))
    palette.append(
      (
        sum(c[0] for c in box) // n,
        sum(c[1] for c in box) // n,
        sum(c[2] for c in box) // n,
      )
    )
  while len(palette) < max_colors:
    palette.append((0, 0, 0))
  return palette[:max_colors]


def _lzw_encode_indices(indices: bytes | bytearray, min_code_size: int = 8) -> bytes:
  clear_code = 1 << min_code_size
  end_code = clear_code + 1
  code_size = min_code_size + 1
  next_code = end_code + 1

  bit_buf = 0
  bit_cnt = 0
  out = bytearray()

  def emit(code: int, width: int) -> None:
    nonlocal bit_buf, bit_cnt
    bit_buf |= code << bit_cnt
    bit_cnt += width
    while bit_cnt >= 8:
      out.append(bit_buf & 0xFF)
      bit_buf >>= 8
      bit_cnt -= 8

  emit(clear_code, code_size)
  table: dict[tuple[int, int], int] = {}
  if not indices:
    emit(end_code, code_size)
  else:
    prefix = indices[0]
    for sym in indices[1:]:
      key = (prefix, sym)
      found = table.get(key)
      if found is not None:
        prefix = found
      else:
        emit(prefix, code_size)
        if next_code < 4096:
          table[key] = next_code
          next_code += 1
          if next_code > (1 << code_size) and code_size < 12:
            code_size += 1
        else:
          emit(clear_code, code_size)
          table.clear()
          code_size = min_code_size + 1
          next_code = end_code + 1
        prefix = sym
    emit(prefix, code_size)
    emit(end_code, code_size)

  if bit_cnt > 0:
    out.append(bit_buf & 0xFF)

  blocks = bytearray([min_code_size])
  for pos in range(0, len(out), 255):
    chunk = out[pos : pos + 255]
    blocks.append(len(chunk))
    blocks.extend(chunk)
  blocks.append(0)
  return bytes(blocks)


def encode_gif89a(png_frames: Sequence[bytes], *, delay_cs: int = 35) -> bytes:
  """Encode 1+ PNG byte frames into an animated looping GIF89a (0 external binaries)."""
  if not png_frames:
    raise ValueError("encode_gif89a requires at least one PNG frame")
  from annotate import decode_png

  decoded: list[tuple[int, int, bytearray]] = [decode_png(raw) for raw in png_frames]
  width, height, _ = decoded[0]

  normalized: list[bytearray] = []
  samples: list[tuple[int, int, int]] = []
  for fw, fh, rgba in decoded:
    if (fw, fh) != (width, height):
      resized = bytearray(width * height * 4)
      for y in range(height):
        sy = min(fh - 1, (y * fh) // max(1, height))
        for x in range(width):
          sx = min(fw - 1, (x * fw) // max(1, width))
          s_off = (sy * fw + sx) * 4
          d_off = (y * width + x) * 4
          resized[d_off : d_off + 4] = rgba[s_off : s_off + 4]
      rgba = resized
    normalized.append(rgba)
    total_px = width * height
    stride = max(1, total_px // 2048)
    for px_idx in range(0, total_px, stride):
      off = px_idx * 4
      a = rgba[off + 3]
      if a < 255:
        r = (rgba[off] * a + 15 * (255 - a)) // 255
        g = (rgba[off + 1] * a + 15 * (255 - a)) // 255
        b = (rgba[off + 2] * a + 18 * (255 - a)) // 255
      else:
        r, g, b = rgba[off], rgba[off + 1], rgba[off + 2]
      samples.append((r, g, b))

  palette = _median_cut_palette(samples, 256)
  lut_cache: dict[tuple[int, int, int], int] = {}

  def map_rgb(r: int, g: int, b: int) -> int:
    qkey = (r >> 3, g >> 3, b >> 3)
    hit = lut_cache.get(qkey)
    if hit is not None:
      return hit
    qr, qg, qb = (qkey[0] << 3) | 4, (qkey[1] << 3) | 4, (qkey[2] << 3) | 4
    best_i = 0
    best_d = 1 << 30
    for idx, (pr, pg, pb) in enumerate(palette):
      dr, dg, db = qr - pr, qg - pg, qb - pb
      dist = dr * dr + dg * dg + db * db
      if dist < best_d:
        best_d = dist
        best_i = idx
        if dist == 0:
          break
    lut_cache[qkey] = best_i
    return best_i

  gif = bytearray(b"GIF89a")
  gif.extend(struct.pack("<HHBBB", width, height, 0xF7, 0, 0))
  for r, g, b in palette:
    gif.extend((r, g, b))
  gif.extend(b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00")

  for rgba in normalized:
    indices = bytearray(width * height)
    for px_idx in range(width * height):
      off = px_idx * 4
      a = rgba[off + 3]
      if a < 255:
        r = (rgba[off] * a + 15 * (255 - a)) // 255
        g = (rgba[off + 1] * a + 15 * (255 - a)) // 255
        b = (rgba[off + 2] * a + 18 * (255 - a)) // 255
      else:
        r, g, b = rgba[off], rgba[off + 1], rgba[off + 2]
      indices[px_idx] = map_rgb(r, g, b)
    gif.extend(
      struct.pack("<BBBBHBB", 0x21, 0xF9, 4, 0x04, max(1, int(delay_cs)), 0, 0)
    )
    gif.extend(struct.pack("<BHHHHB", 0x2C, 0, 0, width, height, 0x00))
    gif.extend(_lzw_encode_indices(indices, 8))

  gif.append(0x3B)
  return bytes(gif)


# Public Python API: Demo, Walker, and @step.


def _resolve_step_viewport(
  spec: str, viewports: Sequence[Mapping[str, Any]] | None = None
) -> dict[str, Any]:
  from nuu import DEFAULT_VIEWPORTS, parse_viewport_spec

  vps = list(viewports) if viewports else [dict(vp) for vp in DEFAULT_VIEWPORTS]
  key = str(spec).strip().lower()
  if key in DEVICE_INDEX:
    idx = DEVICE_INDEX[key]
    if 0 <= idx < len(vps):
      return dict(vps[idx])
  return parse_viewport_spec(spec, vps)


class Demo:
  """Fixed-data demo controller passed to @step functions."""

  def __init__(self, base_url: str = "http://127.0.0.1:1500") -> None:
    self.base_url = base_url.rstrip("/")
    self.signed_in = True
    self.checked = True
    self.state: dict[str, object] = {}

  def url_for(self, target: int | str) -> str:
    if isinstance(target, int):
      return f"http://127.0.0.1:{target}"
    text = str(target).strip()
    if text.isdigit():
      return f"http://127.0.0.1:{text}"
    if text.startswith(("http://", "https://", "file://")):
      return text
    path = Path(text).resolve()
    if path.exists():
      return path.as_uri()
    return f"{self.base_url}/{text.lstrip('/')}"

  def sign_in(self, signed_in: bool = True) -> None:
    self.signed_in = bool(signed_in)
    self.state["signed_in"] = self.signed_in

  def wait_until_checked(self, checked: bool = True) -> None:
    self.checked = bool(checked)
    self.state["checked"] = self.checked


class Walker:
  """Playwright page wrapper providing view captures, MCP parity verbs, and GIF89a."""

  def __init__(
    self,
    page: Any,
    demo: Demo | None = None,
    *,
    output_dir: Path | None = None,
    refs: Mapping[str, Mapping[str, Any]] | None = None,
  ) -> None:
    self.page = page
    self.demo = demo or Demo()
    self.output_dir = (
      Path(output_dir) if output_dir is not None else Path("./nuu-artifacts")
    )
    self.errors: list[str] = []
    self.refs: dict[str, dict[str, Any]] = {
      str(k): dict(v) for k, v in (refs or {}).items()
    }
    self._gif_recording = False
    self._gif_frames: list[bytes] = []
    self._dialog_policy: tuple[str, str | None] = ("accept", None)
    if hasattr(self.page, "on"):
      self.page.on("console", self._on_console)
      self.page.on("pageerror", self._on_page_error)
      self.page.on("dialog", self._on_dialog)

  def _on_console(self, message: Any) -> None:
    msg_type = getattr(message, "type", "")
    if callable(msg_type):
      msg_type = msg_type()
    if msg_type == "error":
      text = getattr(message, "text", "")
      self.errors.append(text() if callable(text) else str(text))

  def _on_page_error(self, error: Any) -> None:
    self.errors.append(str(error))

  def _on_dialog(self, dlg: Any) -> None:
    action, prompt_text = self._dialog_policy
    try:
      if action == "dismiss":
        dlg.dismiss()
      elif prompt_text is not None:
        dlg.accept(prompt_text)
      else:
        dlg.accept()
    except Exception:
      pass

  def _resolve_selector(self, target: str) -> str:
    token = str(target).strip()
    if token in self.refs and "selector" in self.refs[token]:
      return str(self.refs[token]["selector"])
    return token

  def _locator(self, target: str) -> Any:
    return self.page.locator(self._resolve_selector(target)).first

  def goto(self, target: int | str, wait_until: str = "domcontentloaded") -> Any:
    url = self.demo.url_for(target)
    if hasattr(self.page, "add_init_script"):
      try:
        self.page.add_init_script(_INIT_TRACKER_JS)
      except Exception:
        pass
    resp = self.page.goto(url, wait_until=wait_until)
    if hasattr(self.page, "evaluate"):
      try:
        from nuu import settle_page

        settle_page(self.page)
      except Exception:
        pass
    if self._gif_recording:
      self.gif_frame()
    return resp

  def view(
    self, name: str, selector: str | None = None, *, full_page: bool = False
  ) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name).strip()).strip("-")
    if not safe:
      raise ValueError("view name must not be empty")
    self.output_dir.mkdir(parents=True, exist_ok=True)
    dest = self.output_dir / (safe if safe.lower().endswith(".png") else f"{safe}.png")
    if selector:
      raw = bytes(self._locator(selector).screenshot())
    else:
      raw = self.screenshot(full_page=full_page)
    dest.write_bytes(raw)
    return dest

  def screenshot(self, path: str | Path | None = None, **kwargs: Any) -> bytes:
    call_kwargs = dict(kwargs)
    if path is not None:
      dest = Path(path)
      dest.parent.mkdir(parents=True, exist_ok=True)
      call_kwargs["path"] = str(dest)
    data = bytes(self.page.screenshot(**call_kwargs))
    if path is not None and not Path(path).is_file():
      Path(path).write_bytes(data)
    return data

  def snapshot(
    self,
    *,
    boxes: bool = False,
    depth: int | None = None,
    target: str | None = None,
  ) -> str:
    root_sel = self._resolve_selector(target) if target else None
    probe = self.page.evaluate(
      _SNAPSHOT_PROBE_JS,
      {"includeBoxes": bool(boxes), "maxDepth": depth, "rootSelector": root_sel},
    )
    self.refs = dict(probe.get("refs") or {})
    aria_txt = ""
    try:
      loc = self.page.locator(root_sel or "body").first
      if hasattr(loc, "aria_snapshot"):
        aria_txt = str(loc.aria_snapshot() or "").strip()
    except Exception:
      aria_txt = ""
    ref_tree = str(probe.get("tree") or "").strip()
    if aria_txt and ref_tree:
      return f"{ref_tree}\n\n# ARIA Tree\n{aria_txt}"
    return ref_tree or aria_txt

  def find(self, pattern: str) -> list[dict[str, Any]]:
    if not self.refs:
      self.snapshot(boxes=True)
    raw = str(pattern).strip()
    regex: re.Pattern[str] | None = None
    if len(raw) >= 2 and raw.startswith("/") and raw.endswith("/"):
      regex = re.compile(raw[1:-1], re.IGNORECASE)
    matches: list[dict[str, Any]] = []
    for ref_id, info in self.refs.items():
      hay = f"{info.get('role', '')} {info.get('name', '')} {info.get('selector', '')}"
      ok = bool(regex.search(hay)) if regex else (raw.lower() in hay.lower())
      if ok:
        matches.append({"ref": ref_id, **info})
    return matches

  def click(
    self,
    target: str,
    *,
    double: bool = False,
    button: str = "left",
    modifiers: Sequence[str] = (),
  ) -> None:
    loc = self._locator(target)
    kwargs: dict[str, Any] = {"button": button}
    if modifiers:
      kwargs["modifiers"] = list(modifiers)
    if double:
      loc.dblclick(**kwargs)
    else:
      loc.click(**kwargs)
    if self._gif_recording:
      self.gif_frame()

  def fill(self, target: str, value: str) -> None:
    self._locator(target).fill(str(value))
    if self._gif_recording:
      self.gif_frame()

  def type(
    self, target: str, text: str, *, slowly: bool = False, submit: bool = False
  ) -> None:
    loc = self._locator(target)
    if slowly:
      loc.press_sequentially(str(text), delay=35)
    else:
      loc.press_sequentially(str(text))
    if submit:
      loc.press("Enter")
    if self._gif_recording:
      self.gif_frame()

  def press(self, key: str) -> None:
    self.page.keyboard.press(str(key))
    if self._gif_recording:
      self.gif_frame()

  def select(self, target: str, *values: str) -> None:
    self._locator(target).select_option(list(values))

  def check(self, target: str) -> None:
    self._locator(target).check()

  def uncheck(self, target: str) -> None:
    self._locator(target).uncheck()

  def hover(self, target: str) -> None:
    self._locator(target).hover()

  def drag(self, source: str, dest: str) -> None:
    self._locator(source).drag_to(self._locator(dest))

  def upload(self, target: str, *files: str | Path) -> None:
    self._locator(target).set_input_files([str(Path(f)) for f in files])

  def wheel(self, dx: float, dy: float) -> None:
    self.page.mouse.wheel(float(dx), float(dy))

  def dialog(self, action: str = "accept", prompt_text: str | None = None) -> None:
    if action not in {"accept", "dismiss"}:
      raise ValueError(f"Unsupported dialog action: {action!r}")
    self._dialog_policy = (action, prompt_text)

  def resize(self, width: int, height: int) -> None:
    self.page.set_viewport_size({"width": int(width), "height": int(height)})

  def emulate(
    self,
    *,
    scheme: str | None = None,
    motion: str | None = None,
    contrast: str | None = None,
  ) -> None:
    kwargs: dict[str, Any] = {}
    if scheme is not None:
      kwargs["color_scheme"] = None if scheme == "no-preference" else scheme
    if motion is not None:
      kwargs["reduced_motion"] = motion
    if contrast is not None:
      kwargs["forced_colors"] = "active" if contrast == "more" else "none"
    if kwargs:
      self.page.emulate_media(**kwargs)

  def route(
    self,
    glob: str,
    *,
    status: int = 200,
    body: str | bytes = "",
    content_type: str = "application/json",
    abort: bool = False,
  ) -> None:
    payload = body.encode("utf-8") if isinstance(body, str) else bytes(body)

    def _handler(route_obj: Any) -> None:
      if abort:
        route_obj.abort()
      else:
        route_obj.fulfill(status=int(status), body=payload, content_type=content_type)

    self.page.route(glob, _handler)

  def unroute(self, glob: str | None = None) -> None:
    if glob:
      self.page.unroute(glob)
    elif hasattr(self.page, "unroute_all"):
      self.page.unroute_all(behavior="ignoreErrors")

  def offline(self, enabled: bool) -> None:
    self.page.context.set_offline(bool(enabled))

  def wait(
    self,
    *,
    text: str | None = None,
    text_gone: str | None = None,
    selector: str | None = None,
    sec: float | None = None,
  ) -> None:
    if text is not None:
      self.page.get_by_text(text).first.wait_for(state="visible")
    if text_gone is not None:
      self.page.get_by_text(text_gone).first.wait_for(state="hidden")
    if selector is not None:
      self.page.locator(self._resolve_selector(selector)).first.wait_for(
        state="visible"
      )
    if sec is not None and float(sec) > 0:
      self.page.wait_for_timeout(int(float(sec) * 1000))

  def eval(self, expression: str) -> object:
    return self.page.evaluate(expression)

  def gif_start(self) -> None:
    self._gif_recording = True
    self._gif_frames = [self.screenshot()]

  def gif_frame(self) -> None:
    self._gif_frames.append(self.screenshot())

  def gif_stop(self, output: str | Path) -> Path:
    self._gif_recording = False
    if not self._gif_frames:
      self._gif_frames.append(self.screenshot())
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(encode_gif89a(self._gif_frames))
    return dest


def step(
  viewport: str = "desktop",
) -> Callable[[Callable[[Walker, Demo], None]], Callable[[Walker, Demo], None]]:
  """Register a deterministic walk step for a named device alias or WxH viewport."""
  key = str(viewport).strip()
  if key.lower() not in DEVICE_INDEX and not re.fullmatch(
    r"\d+x\d+", key, re.IGNORECASE
  ):
    valid = ", ".join(DEVICE_INDEX)
    raise ValueError(
      f"Unknown walk viewport {viewport!r} (expected {valid} or <width>x<height>)"
    )

  def decorator(
    func: Callable[[Walker, Demo], None],
  ) -> Callable[[Walker, Demo], None]:
    func._walk_device = key  # type: ignore[attr-defined]
    _REGISTERED_STEPS.append((func.__name__, key, func))
    return func

  return decorator


def registered_steps() -> list[tuple[str, str, Callable[[Walker, Demo], None]]]:
  """Return registered (name, viewport_spec, func) walk steps in registration order."""
  return list(_REGISTERED_STEPS)


def clear_steps() -> None:
  """Clear registered walk steps."""
  _REGISTERED_STEPS.clear()


def load_walk_module(script_path: Path) -> Any:
  """Load a Python walk module from disk."""
  resolved = Path(script_path).resolve()
  if not resolved.is_file():
    raise FileNotFoundError(f"Walk script not found: {resolved}")
  clear_steps()
  scripts_dir = str(Path(__file__).resolve().parent)
  if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
  spec = importlib.util.spec_from_file_location("nuu_walk_user", resolved)
  if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load walk script: {resolved}")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def run_steps_with_factory(
  page_factory: Callable[[tuple[int, int]], Any],
  demo: Demo,
  out_dir: Path | None = None,
  steps: Sequence[tuple[str, str, Callable[[Walker, Demo], None]]] | None = None,
  viewports: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
  """Execute @step functions in registration order and fail on RUN001 console errors."""
  active_steps = list(steps) if steps is not None else registered_steps()
  results: list[dict[str, Any]] = []
  resolved_out = Path(out_dir) if out_dir is not None else None
  if resolved_out is not None:
    resolved_out.mkdir(parents=True, exist_ok=True)
  for index, (name, vp_spec, func) in enumerate(active_steps, 1):
    vp_dict = _resolve_step_viewport(vp_spec, viewports)
    vp_tuple = (int(vp_dict["width"]), int(vp_dict["height"]))
    page = page_factory(vp_tuple)
    walker = Walker(page, demo, output_dir=resolved_out)
    before_errors = len(walker.errors)
    func(walker, demo)
    new_errors = walker.errors[before_errors:]
    if new_errors:
      raise RuntimeError(
        f"RUN001: Step {name} ({vp_spec}) logged console/page errors: {new_errors}"
      )
    shot_path: str | None = None
    if resolved_out is not None:
      target = resolved_out / f"{index:02d}-{name}-{vp_spec}.png"
      target.write_bytes(walker.screenshot())
      shot_path = str(target)
    results.append(
      {
        "step": name,
        "device": vp_spec,
        "viewport": list(vp_tuple),
        "screenshot": shot_path,
      }
    )
  return results


# Live interaction and slow-network optimistic write probes (ACC205..ACC207).


def probe_optimistic_writes(page: Any, url: str) -> list[dict[str, Any]]:
  """Check Clause 10 ACC207 via page.clock + held POST/PUT/PATCH/DELETE route (0ms sleep)."""
  findings: list[dict[str, Any]] = []
  try:
    page.clock.install()
  except Exception:
    pass
  page.goto(url, wait_until="domcontentloaded")

  write_targets = page.evaluate(
    r"""() => {
      const out = [];
      const forms = Array.from(document.querySelectorAll('form'));
      forms.forEach((f, idx) => {
        const btn = f.querySelector('button[type="submit"], button:not([type="button"]), input[type="submit"]');
        const inp = f.querySelector('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea');
        if (btn) {
          f.setAttribute('data-nuu-form-idx', String(idx));
          btn.setAttribute('data-nuu-submit-idx', String(idx));
          if (inp) inp.setAttribute('data-nuu-input-idx', String(idx));
          out.push({ idx, hasInput: Boolean(inp), label: (btn.innerText || btn.value || 'submit').trim() });
        }
      });
      if (!out.length) {
        document.querySelectorAll('[data-optimistic], [data-write]').forEach((btn, idx) => {
          btn.setAttribute('data-nuu-submit-idx', ` standalone-${idx}`);
          out.push({ idx: ` standalone-${idx}`, hasInput: false, label: (btn.innerText || 'write').trim() });
        });
      }
      return out;
    }"""
  )
  if not write_targets:
    return findings

  held: list[tuple[Any, Any]] = []

  def _hold_write(route_obj: Any, req: Any) -> None:
    if str(req.method).upper() in {"POST", "PUT", "PATCH", "DELETE"}:
      held.append((route_obj, req))
    else:
      route_obj.continue_()

  page.route("**/*", _hold_write)
  try:
    first = write_targets[0]
    idx_val = str(first["idx"])
    sample_token = "OpsCheckOptimisticRow"
    if first.get("hasInput"):
      inp_loc = page.locator(f'[data-nuu-input-idx="{idx_val}"]').first
      if not (inp_loc.input_value() or "").strip():
        inp_loc.fill(sample_token)

    before_snap = page.evaluate(
      r"""(token) => ({
        text: (document.body.innerText || '').trim(),
        busyCount: document.querySelectorAll('[aria-busy="true"], [data-pending], .pending, .optimistic').length,
        childCount: document.querySelectorAll('li, tr, [role="row"], .row, article').length,
        tokenHits: ((document.body.innerText || '').match(new RegExp(token, 'g')) || []).length,
      })""",
      sample_token,
    )

    page.locator(f'[data-nuu-submit-idx="{idx_val}"]').first.click()
    try:
      page.clock.fast_forward(50)
    except Exception:
      pass
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => r(true)))")

    if held:
      mid_snap = page.evaluate(
        r"""(token) => ({
          text: (document.body.innerText || '').trim(),
          busyCount: document.querySelectorAll('[aria-busy="true"], [data-pending], .pending, .optimistic').length,
          childCount: document.querySelectorAll('li, tr, [role="row"], .row, article').length,
          tokenHits: ((document.body.innerText || '').match(new RegExp(token, 'g')) || []).length,
        })""",
        sample_token,
      )
      changed_immediately = (
        mid_snap["text"] != before_snap["text"]
        or mid_snap["busyCount"] > before_snap["busyCount"]
        or mid_snap["childCount"] > before_snap["childCount"]
      )
      if not changed_immediately:
        findings.append(
          {
            "rule": "ACC207",
            "selector": f'[data-nuu-submit-idx="{idx_val}"]',
            "evidence": (
              f"Write action ({held[0][1].method} {held[0][1].url}) rendered no immediate "
              "optimistic DOM state after 50ms under held slow-network route"
            ),
          }
        )

      for route_obj, _req in held:
        try:
          route_obj.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "id": "opt_1", "title": sample_token}),
          )
        except Exception:
          pass
      held.clear()
      try:
        page.clock.fast_forward(3000)
      except Exception:
        pass
      page.evaluate("() => new Promise((r) => requestAnimationFrame(() => r(true)))")

      after_hits = page.evaluate(
        r"(token) => ((document.body.innerText || '').match(new RegExp(token, 'g')) || []).length",
        sample_token,
      )
      if mid_snap["tokenHits"] >= 1 and after_hits > mid_snap["tokenHits"]:
        findings.append(
          {
            "rule": "ACC207",
            "selector": f'[data-nuu-submit-idx="{idx_val}"]',
            "evidence": (
              f"Optimistic write duplicated DOM row after slow-network response "
              f"({mid_snap['tokenHits']} -> {after_hits} instances)"
            ),
          }
        )
  finally:
    for route_obj, _req in held:
      try:
        route_obj.abort()
      except Exception:
        pass
    try:
      page.unroute("**/*", _hold_write)
    except Exception:
      pass
  return findings


_INTERACTIVE_STATE_JS = r"""() => {
  const q = '[aria-selected],[aria-expanded],[aria-pressed],[aria-busy],dialog[open],[data-state]';
  const attrs = Array.from(document.querySelectorAll(q))
    .map((n) => [
      n.tagName,
      n.getAttribute('aria-selected'),
      n.getAttribute('aria-expanded'),
      n.hasAttribute('open'),
      n.getAttribute('data-state'),
    ].join(':'))
    .join('|');
  return {
    url: location.href,
    text: (document.body.innerText || '').trim(),
    attrs,
  };
}"""


def _probe_single_control(
  page: Any, item: Mapping[str, Any], *, check_clicks: bool
) -> list[dict[str, Any]]:
  out: list[dict[str, Any]] = []
  sel = f'[data-nuu-ctrl-idx="{item["idx"]}"]'
  loc = page.locator(sel).first
  try:
    loc.hover(timeout=1500)
    hov = loc.bounding_box()
    if hov and (
      abs(hov["width"] - item["width"]) > 1.0
      or abs(hov["height"] - item["height"]) > 1.0
    ):
      out.append(
        {
          "rule": "ACC205",
          "selector": sel,
          "evidence": (
            f"Control {sel} changed size on hover "
            f"({item['width']:.1f}x{item['height']:.1f} -> {hov['width']:.1f}x{hov['height']:.1f})"
          ),
        }
      )
    loc.focus(timeout=1500)
    foc = loc.bounding_box()
    if foc and (
      abs(foc["width"] - item["width"]) > 1.0
      or abs(foc["height"] - item["height"]) > 1.0
    ):
      out.append(
        {
          "rule": "ACC205",
          "selector": sel,
          "evidence": (
            f"Control {sel} changed size on focus "
            f"({item['width']:.1f}x{item['height']:.1f} -> {foc['width']:.1f}x{foc['height']:.1f})"
          ),
        }
      )
    if check_clicks and item.get("safeClick"):
      before_state = page.evaluate(_INTERACTIVE_STATE_JS)
      loc.click(timeout=1500)
      page.wait_for_timeout(60)
      after_box = loc.bounding_box()
      if after_box and (
        abs(after_box["width"] - item["width"]) > 1.0
        or abs(after_box["height"] - item["height"]) > 1.0
      ):
        out.append(
          {
            "rule": "ACC205",
            "selector": sel,
            "evidence": (
              f"Control {sel} changed size on click "
              f"({item['width']:.1f}x{item['height']:.1f} -> {after_box['width']:.1f}x{after_box['height']:.1f})"
            ),
          }
        )
      after_state = page.evaluate(_INTERACTIVE_STATE_JS)
      if before_state == after_state:
        label = item["label"] or item["tag"]
        out.append(
          {
            "rule": "ACC206",
            "selector": sel,
            "evidence": (
              f"Action click on {sel} ({label}) produced no visible DOM or state feedback"
            ),
          }
        )
  except Exception:
    return out
  return out


def probe_interactive_states(
  page: Any, *, check_clicks: bool = True
) -> list[dict[str, Any]]:
  """Check ACC205 (dimension stability on hover/focus/click) and ACC206 (click feedback)."""
  findings: list[dict[str, Any]] = []
  controls = page.evaluate(
    r"""() => {
      const destructive = /\b(delete|remove|destroy|drop|erase|logout|sign\s*out|reset|clear)\b/i;
      const nodes = Array.from(document.querySelectorAll('button, [role="tab"], summary, [role="button"]'));
      const items = [];
      nodes.slice(0, 8).forEach((el, idx) => {
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        if (cs.display === 'none' || cs.visibility === 'hidden' || r.width <= 0 || r.height <= 0 || el.disabled) return;
        el.setAttribute('data-nuu-ctrl-idx', String(idx));
        const label = (el.innerText || el.getAttribute('aria-label') || '').trim();
        items.push({
          idx,
          tag: el.tagName.toLowerCase(),
          role: (el.getAttribute('role') || '').toLowerCase(),
          label,
          safeClick: !destructive.test(label) && el.getAttribute('type') !== 'submit',
          width: r.width,
          height: r.height,
        });
      });
      return items;
    }"""
  )
  for item in controls:
    findings.extend(_probe_single_control(page, item, check_clicks=check_clicks))
  return findings


# Persistent CDP interactive session state and 42-verb executor.


def session_state_path(session: str) -> Path:
  safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(session).strip() or "default")
  xdg = os.environ.get("XDG_RUNTIME_DIR", "").strip()
  if xdg and Path(xdg).is_dir() and os.access(xdg, os.W_OK):
    return Path(xdg) / f"nuu-walk-{safe}.json"
  uid = os.getuid() if hasattr(os, "getuid") else 0
  return Path(tempfile.gettempdir()) / f"nuu-walk-{uid}-{safe}.json"


def _save_session_state(path: Path, state: Mapping[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(".tmp")
  tmp.write_text(json.dumps(dict(state), indent=2), encoding="utf-8")
  os.chmod(tmp, 0o600)
  tmp.replace(path)
  os.chmod(path, 0o600)


def _load_session_state(path: Path) -> dict[str, Any]:
  if not path.is_file():
    raise RuntimeError(
      f"RUN101: No active walk session at {path} (run `nuu walk open <url>` first)"
    )
  return json.loads(path.read_text(encoding="utf-8"))


def _launch_cdp_browser(executable: str | Path | None, session: str) -> dict[str, Any]:
  from nuu import chromium_launch_kwargs

  launch_kwargs = chromium_launch_kwargs(executable)
  exe = launch_kwargs["executable_path"]
  base_args = list(launch_kwargs.get("args") or [])
  env = dict(launch_kwargs.get("env") or os.environ)

  uid = os.getuid() if hasattr(os, "getuid") else 0
  safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session)
  profile_dir = Path(tempfile.mkdtemp(prefix=f"nuu-cdp-{uid}-{safe}-"))
  os.chmod(profile_dir, 0o700)
  stderr_log = profile_dir / "devtools.log"
  log_fh = stderr_log.open("wb")
  cmd = [
    exe,
    *base_args,
    "--headless",
    "--remote-debugging-port=0",
    "--remote-debugging-address=127.0.0.1",
    f"--user-data-dir={profile_dir}",
    "about:blank",
  ]
  proc = subprocess.Popen(
    cmd,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=log_fh,
    env=env,
    start_new_session=True,
  )
  log_fh.close()

  ws_endpoint: str | None = None
  deadline = time.monotonic() + 15.0
  active_port_file = profile_dir / "DevToolsActivePort"
  while time.monotonic() < deadline:
    if proc.poll() is not None:
      err_tail = stderr_log.read_text(encoding="utf-8", errors="replace")[-600:]
      raise RuntimeError(f"RUN102: Detached Chromium exited early: {err_tail}")
    if stderr_log.is_file():
      txt = stderr_log.read_text(encoding="utf-8", errors="replace")
      m = re.search(r"(ws://127\.0\.0\.1:\d+/[^\s]+)", txt)
      if m:
        ws_endpoint = m.group(1)
        break
    if active_port_file.is_file():
      lines = active_port_file.read_text(
        encoding="utf-8", errors="replace"
      ).splitlines()
      if len(lines) >= 2 and lines[0].strip().isdigit():
        ws_endpoint = f"ws://127.0.0.1:{lines[0].strip()}{lines[1].strip()}"
        break
    time.sleep(0.05)

  if not ws_endpoint:
    proc.terminate()
    raise RuntimeError("RUN102: Timed out waiting for Chromium CDP WebSocket endpoint")

  return {
    "pid": proc.pid,
    "ws_endpoint": ws_endpoint,
    "profile_dir": str(profile_dir),
    "current_tab": 0,
    "refs": {},
    "gif_active": False,
    "gif_frames": [],
    "routes": [],
    "offline": False,
    "emulate": {},
    "dialog_policy": ["accept", None],
  }


def _close_cdp_session(state_file: Path) -> str:
  if not state_file.is_file():
    return "# Walk Session Closed\n\n- Session was already closed.\n"
  state = json.loads(state_file.read_text(encoding="utf-8"))
  pid = state.get("pid")
  if isinstance(pid, int) and pid > 0:
    try:
      os.kill(pid, signal.SIGTERM)
      for _ in range(30):
        try:
          os.kill(pid, 0)
          time.sleep(0.03)
        except OSError:
          break
      else:
        os.kill(pid, signal.SIGKILL)
    except OSError:
      pass
  prof = state.get("profile_dir")
  if prof and Path(prof).is_dir():
    shutil.rmtree(prof, ignore_errors=True)
  state_file.unlink(missing_ok=True)
  return f"# Walk Session Closed\n\n- Stopped PID `{pid}`\n"


def _apply_persisted_context_state(
  ctx: Any, page: Any, state: Mapping[str, Any]
) -> None:
  try:
    ctx.add_init_script(_INIT_TRACKER_JS)
  except Exception:
    pass
  try:
    page.evaluate(_INIT_TRACKER_JS)
  except Exception:
    pass
  if state.get("offline"):
    try:
      ctx.set_offline(True)
    except Exception:
      pass
  emu = state.get("emulate") or {}
  if emu:
    kwargs: dict[str, Any] = {}
    if emu.get("scheme"):
      kwargs["color_scheme"] = (
        None if emu["scheme"] == "no-preference" else emu["scheme"]
      )
    if emu.get("motion"):
      kwargs["reduced_motion"] = emu["motion"]
    if emu.get("contrast"):
      kwargs["forced_colors"] = "active" if emu["contrast"] == "more" else "none"
    if kwargs:
      try:
        page.emulate_media(**kwargs)
      except Exception:
        pass
  for rspec in state.get("routes") or []:
    glob = rspec["glob"]
    abort = bool(rspec.get("abort"))
    status = int(rspec.get("status", 200))
    body_b64 = rspec.get("body_b64", "")
    body_bytes = base64.b64decode(body_b64) if body_b64 else b""
    ctype = str(rspec.get("content_type", "application/json"))

    def _mk_handler(ab: bool, st: int, bd: bytes, ct: str):
      def _h(route_obj: Any) -> None:
        if ab:
          route_obj.abort()
        else:
          route_obj.fulfill(status=st, body=bd, content_type=ct)

      return _h

    page.route(glob, _mk_handler(abort, status, body_bytes, ctype))


def _execute_mcp_verb(
  verb: str,
  verb_args: list[str],
  *,
  session: str,
  cfg_info: Mapping[str, Any],
  out_dir: Path,
  executable: str | Path | None,
  timeout_sec: int,
) -> int:
  from playwright.sync_api import sync_playwright

  state_file = session_state_path(session)
  default_vp = cfg_info["config"]["audit"]["viewports"][0]

  if verb == "close":
    print(_close_cdp_session(state_file), end="")
    return 0

  if verb == "open":
    if not verb_args:
      raise ValueError("`nuu walk open` requires <url>")
    if state_file.is_file():
      _close_cdp_session(state_file)
    state = _launch_cdp_browser(executable, session)
    url = Demo().url_for(verb_args[0])
    with sync_playwright() as pw:
      browser = pw.chromium.connect_over_cdp(state["ws_endpoint"])
      ctx = browser.contexts[0] if browser.contexts else browser.new_context()
      page = ctx.pages[0] if ctx.pages else ctx.new_page()
      page.set_default_timeout(max(1, timeout_sec) * 1000)
      page.set_viewport_size(
        {"width": int(default_vp["width"]), "height": int(default_vp["height"])}
      )
      page.emulate_media(color_scheme="dark")
      _apply_persisted_context_state(ctx, page, state)
      walker = Walker(page, Demo(url), output_dir=out_dir)
      walker.goto(url)
      snap = walker.snapshot()
      state["refs"] = walker.refs
      state["emulate"] = {"scheme": "dark"}
    _save_session_state(state_file, state)
    print(f"# Walk Session `{session}` Opened\n\n- URL: `{url}`\n\n{snap}\n", end="")
    return 0

  state = _load_session_state(state_file)
  output_lines: list[str] = []

  with sync_playwright() as pw:
    try:
      browser = pw.chromium.connect_over_cdp(state["ws_endpoint"])
    except Exception as exc:
      raise RuntimeError(
        f"RUN102: Failed to connect to CDP session `{session}`: {exc}"
      ) from exc
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    pages = list(ctx.pages)
    if not pages:
      pages = [ctx.new_page()]
    tab_idx = max(0, min(int(state.get("current_tab", 0)), len(pages) - 1))
    page = pages[tab_idx]
    page.set_default_timeout(max(1, timeout_sec) * 1000)
    _apply_persisted_context_state(ctx, page, state)

    walker = Walker(page, Demo(), output_dir=out_dir, refs=state.get("refs") or {})
    dlg_pol = state.get("dialog_policy") or ["accept", None]
    walker.dialog(str(dlg_pol[0]), dlg_pol[1])

    def _record_gif_if_active() -> None:
      if state.get("gif_active"):
        frame_b64 = base64.b64encode(walker.screenshot()).decode("ascii")
        state.setdefault("gif_frames", []).append(frame_b64)

    if verb == "goto":
      walker.goto(verb_args[0])
      _record_gif_if_active()
      output_lines.append(f"- Navigated to `{page.url}`")
    elif verb == "back":
      page.go_back()
      _record_gif_if_active()
      output_lines.append(f"- Back to `{page.url}`")
    elif verb == "forward":
      page.go_forward()
      _record_gif_if_active()
      output_lines.append(f"- Forward to `{page.url}`")
    elif verb == "reload":
      page.reload()
      _record_gif_if_active()
      output_lines.append(f"- Reloaded `{page.url}`")
    elif verb == "tab":
      sub = verb_args[0] if verb_args else "list"
      if sub == "list":
        for idx, p in enumerate(ctx.pages):
          mark = "*" if idx == tab_idx else " "
          output_lines.append(f"- [{mark}] `{idx}`: `{p.url}`")
      elif sub == "new":
        np = ctx.new_page()
        np.set_viewport_size(
          {"width": int(default_vp["width"]), "height": int(default_vp["height"])}
        )
        np.emulate_media(color_scheme="dark")
        if len(verb_args) >= 2:
          np.goto(Demo().url_for(verb_args[1]))
        state["current_tab"] = len(ctx.pages) - 1
        output_lines.append(f"- Opened tab `{state['current_tab']}` (`{np.url}`)")
      elif sub == "select":
        sel_i = int(verb_args[1])
        if not 0 <= sel_i < len(ctx.pages):
          raise ValueError(f"Tab index out of range: {sel_i}")
        state["current_tab"] = sel_i
        ctx.pages[sel_i].bring_to_front()
        output_lines.append(f"- Selected tab `{sel_i}` (`{ctx.pages[sel_i].url}`)")
      elif sub == "close":
        close_i = int(verb_args[1]) if len(verb_args) >= 2 else tab_idx
        ctx.pages[close_i].close()
        state["current_tab"] = max(0, min(tab_idx, max(0, len(ctx.pages) - 1)))
        output_lines.append(f"- Closed tab `{close_i}`")
      else:
        raise ValueError(f"Unknown tab action: {sub}")
    elif verb == "snapshot":
      sp = argparse.ArgumentParser(prog="nuu walk snapshot", add_help=False)
      sp.add_argument("--boxes", action="store_true")
      sp.add_argument("--depth", type=int, default=None)
      sp.add_argument("target", nargs="?", default=None)
      sargs = sp.parse_args(verb_args)
      snap = walker.snapshot(boxes=sargs.boxes, depth=sargs.depth, target=sargs.target)
      state["refs"] = walker.refs
      output_lines.append(snap or "- (empty snapshot)")
    elif verb == "find":
      matches = walker.find(verb_args[0])
      state["refs"] = walker.refs
      for row in matches:
        output_lines.append(
          f'- `[{row["ref"]}]` `{row["role"]}` "{row["name"]}" (`{row["selector"]}`)'
        )
      if not matches:
        output_lines.append("- No matches")
    elif verb == "click":
      cp = argparse.ArgumentParser(prog="nuu walk click", add_help=False)
      cp.add_argument("target")
      cp.add_argument("--double", action="store_true")
      cp.add_argument("--button", choices=("left", "right", "middle"), default="left")
      cp.add_argument("--mod", nargs="*", default=[])
      cargs = cp.parse_args(verb_args)
      walker.click(
        cargs.target, double=cargs.double, button=cargs.button, modifiers=cargs.mod
      )
      _record_gif_if_active()
      output_lines.append(f"- Clicked `{cargs.target}`")
    elif verb == "dblclick":
      walker.click(verb_args[0], double=True)
      _record_gif_if_active()
      output_lines.append(f"- Double-clicked `{verb_args[0]}`")
    elif verb == "hover":
      walker.hover(verb_args[0])
      output_lines.append(f"- Hovered `{verb_args[0]}`")
    elif verb == "drag":
      walker.drag(verb_args[0], verb_args[1])
      _record_gif_if_active()
      output_lines.append(f"- Dragged `{verb_args[0]}` -> `{verb_args[1]}`")
    elif verb == "fill":
      walker.fill(verb_args[0], verb_args[1])
      _record_gif_if_active()
      output_lines.append(f"- Filled `{verb_args[0]}`")
    elif verb == "type":
      tp = argparse.ArgumentParser(prog="nuu walk type", add_help=False)
      tp.add_argument("target")
      tp.add_argument("text")
      tp.add_argument("--slowly", action="store_true")
      tp.add_argument("--submit", action="store_true")
      targs = tp.parse_args(verb_args)
      walker.type(targs.target, targs.text, slowly=targs.slowly, submit=targs.submit)
      _record_gif_if_active()
      output_lines.append(f"- Typed into `{targs.target}`")
    elif verb == "select":
      walker.select(verb_args[0], *verb_args[1:])
      output_lines.append(f"- Selected options on `{verb_args[0]}`")
    elif verb == "check":
      walker.check(verb_args[0])
      output_lines.append(f"- Checked `{verb_args[0]}`")
    elif verb == "uncheck":
      walker.uncheck(verb_args[0])
      output_lines.append(f"- Unchecked `{verb_args[0]}`")
    elif verb == "upload":
      walker.upload(verb_args[0], *verb_args[1:])
      output_lines.append(
        f"- Uploaded {len(verb_args) - 1} file(s) to `{verb_args[0]}`"
      )
    elif verb == "press":
      walker.press(verb_args[0])
      _record_gif_if_active()
      output_lines.append(f"- Pressed `{verb_args[0]}`")
    elif verb == "wheel":
      walker.wheel(float(verb_args[0]), float(verb_args[1]))
      output_lines.append(f"- Scrolled wheel `({verb_args[0]}, {verb_args[1]})`")
    elif verb == "mouse-xy":
      sub = verb_args[0]
      if sub == "move":
        page.mouse.move(float(verb_args[1]), float(verb_args[2]))
      elif sub == "click":
        page.mouse.click(float(verb_args[1]), float(verb_args[2]))
      elif sub == "down":
        page.mouse.down()
      elif sub == "up":
        page.mouse.up()
      elif sub == "drag":
        page.mouse.move(float(verb_args[1]), float(verb_args[2]))
        page.mouse.down()
        page.mouse.move(float(verb_args[3]), float(verb_args[4]))
        page.mouse.up()
      else:
        raise ValueError(f"Unknown mouse-xy action: {sub}")
      output_lines.append(f"- Executed `mouse-xy {sub}`")
    elif verb == "dialog":
      act = verb_args[0]
      ptxt = verb_args[1] if len(verb_args) >= 2 else None
      walker.dialog(act, ptxt)
      state["dialog_policy"] = [act, ptxt]
      output_lines.append(f"- Set dialog policy to `{act}`")
    elif verb == "resize":
      w_px, h_px = int(verb_args[0]), int(verb_args[1])
      walker.resize(w_px, h_px)
      output_lines.append(f"- Resized viewport to `{w_px}x{h_px}`")
    elif verb == "emulate":
      ep = argparse.ArgumentParser(prog="nuu walk emulate", add_help=False)
      ep.add_argument("--scheme", choices=("dark", "light", "no-preference"))
      ep.add_argument("--motion", choices=("reduce", "no-preference"))
      ep.add_argument("--contrast", choices=("more", "no-preference"))
      eargs = ep.parse_args(verb_args)
      walker.emulate(scheme=eargs.scheme, motion=eargs.motion, contrast=eargs.contrast)
      emu = dict(state.get("emulate") or {})
      if eargs.scheme:
        emu["scheme"] = eargs.scheme
      if eargs.motion:
        emu["motion"] = eargs.motion
      if eargs.contrast:
        emu["contrast"] = eargs.contrast
      state["emulate"] = emu
      output_lines.append(f"- Updated media emulation: `{emu}`")
    elif verb == "console":
      flt = (verb_args[0] if verb_args else "all").lower()
      logs = page.evaluate("() => window.__nuuConsole || []")
      for entry in logs:
        lvl = str(entry.get("level", "info"))
        if flt in {"all", lvl}:
          output_lines.append(f"- `[{lvl}]` {entry.get('text', '')}")
      if not output_lines:
        output_lines.append("- None")
    elif verb in {"requests", "request"}:
      reqs = page.evaluate(
        r"""() => {
          const perf = performance.getEntriesByType('resource').map((r) => ({
            method: 'GET',
            url: r.name,
            status: r.responseStatus || 200,
          }));
          return [...perf, ...(window.__nuuRequests || [])];
        }"""
      )
      if verb == "request":
        ridx = int(verb_args[0])
        if not 0 <= ridx < len(reqs):
          raise IndexError(f"Request index out of range: {ridx}")
        row = reqs[ridx]
        output_lines.append(
          f"- `[{ridx}]` `{row['method']}` `{row['url']}` -> `{row['status']}`"
        )
      else:
        sub_f = verb_args[0] if verb_args else ""
        for idx, row in enumerate(reqs):
          if not sub_f or sub_f in str(row["url"]):
            output_lines.append(
              f"- `[{idx}]` `{row['method']}` `{row['url']}` (`{row['status']}`)"
            )
        if not output_lines:
          output_lines.append("- None")
    elif verb == "route":
      rp = argparse.ArgumentParser(prog="nuu walk route", add_help=False)
      rp.add_argument("glob")
      rp.add_argument("--status", type=int, default=200)
      rp.add_argument("--body", default="")
      rp.add_argument("--content-type", default="application/json")
      rp.add_argument("--abort", action="store_true")
      rargs = rp.parse_args(verb_args)
      body_raw = rargs.body
      if isinstance(body_raw, str) and body_raw.startswith("@"):
        body_bytes = Path(body_raw[1:]).read_bytes()
      else:
        body_bytes = str(body_raw).encode("utf-8")
      routes = [r for r in (state.get("routes") or []) if r.get("glob") != rargs.glob]
      routes.append(
        {
          "glob": rargs.glob,
          "status": rargs.status,
          "body_b64": base64.b64encode(body_bytes).decode("ascii"),
          "content_type": rargs.content_type,
          "abort": rargs.abort,
        }
      )
      state["routes"] = routes
      output_lines.append(f"- Registered route `{rargs.glob}`")
    elif verb == "unroute":
      glob_arg = verb_args[0] if verb_args else None
      if glob_arg:
        state["routes"] = [
          r for r in (state.get("routes") or []) if r.get("glob") != glob_arg
        ]
      else:
        state["routes"] = []
      output_lines.append(f"- Cleared route `{glob_arg or '*'}`")
    elif verb == "offline":
      enabled = str(verb_args[0]).lower() in {"true", "1", "yes", "on"}
      walker.offline(enabled)
      state["offline"] = enabled
      output_lines.append(f"- Offline mode: `{enabled}`")
    elif verb == "eval":
      res = walker.eval(verb_args[0])
      output_lines.append(f"- Result: `{res}`")
    elif verb == "storage":
      sub, spath = verb_args[0], Path(verb_args[1])
      if sub == "save":
        spath.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(spath))
        output_lines.append(f"- Saved storage state to `{spath}`")
      elif sub == "load":
        data = json.loads(spath.read_text(encoding="utf-8"))
        if data.get("cookies"):
          ctx.add_cookies(data["cookies"])
        for origin_entry in data.get("origins") or []:
          if origin_entry.get("origin") and page.url.startswith(origin_entry["origin"]):
            for kv in origin_entry.get("localStorage") or []:
              page.evaluate(
                "([k, v]) => localStorage.setItem(k, v)", [kv["name"], kv["value"]]
              )
        output_lines.append(f"- Loaded storage state from `{spath}`")
      else:
        raise ValueError(f"Unknown storage subcommand: {sub}")
    elif verb == "cookie":
      sub = verb_args[0]
      if sub == "list":
        for ck in ctx.cookies():
          output_lines.append(
            f"- `{ck['name']}={ck['value']}` (`{ck.get('domain', '')}`)"
          )
        if not output_lines:
          output_lines.append("- None")
      elif sub == "get":
        name = verb_args[1]
        hit = next((ck for ck in ctx.cookies() if ck["name"] == name), None)
        output_lines.append(f"- `{name}`: `{hit['value'] if hit else ''}`")
      elif sub == "set":
        cp = argparse.ArgumentParser(prog="nuu walk cookie set", add_help=False)
        cp.add_argument("name")
        cp.add_argument("val")
        cp.add_argument("--url", default=None)
        cargs = cp.parse_args(verb_args[1:])
        ck_url = cargs.url or page.url
        ctx.add_cookies([{"name": cargs.name, "value": cargs.val, "url": ck_url}])
        output_lines.append(f"- Set cookie `{cargs.name}`")
      elif sub == "delete":
        ctx.clear_cookies(name=verb_args[1])
        output_lines.append(f"- Deleted cookie `{verb_args[1]}`")
      elif sub == "clear":
        ctx.clear_cookies()
        output_lines.append("- Cleared all cookies")
      else:
        raise ValueError(f"Unknown cookie action: {sub}")
    elif verb in {"localstorage", "sessionstorage"}:
      store_js = "localStorage" if verb == "localstorage" else "sessionStorage"
      sub = verb_args[0]
      if sub == "list":
        items = page.evaluate(f"() => Object.entries({store_js})")
        for k, v in items:
          output_lines.append(f"- `{k}` = `{v}`")
        if not output_lines:
          output_lines.append("- None")
      elif sub == "get":
        val = page.evaluate(f"(k) => {store_js}.getItem(k)", verb_args[1])
        output_lines.append(f"- `{verb_args[1]}` = `{val if val is not None else ''}`")
      elif sub == "set":
        page.evaluate(
          f"([k, v]) => {store_js}.setItem(k, v)", [verb_args[1], verb_args[2]]
        )
        output_lines.append(f"- Set `{store_js}[{verb_args[1]!r}]`")
      elif sub == "delete":
        page.evaluate(f"(k) => {store_js}.removeItem(k)", verb_args[1])
        output_lines.append(f"- Deleted `{store_js}[{verb_args[1]!r}]`")
      elif sub == "clear":
        page.evaluate(f"() => {store_js}.clear()")
        output_lines.append(f"- Cleared `{store_js}`")
      else:
        raise ValueError(f"Unknown {verb} action: {sub}")
    elif verb == "wait":
      wp = argparse.ArgumentParser(prog="nuu walk wait", add_help=False)
      wp.add_argument("--text", default=None)
      wp.add_argument("--text-gone", dest="text_gone", default=None)
      wp.add_argument("--selector", default=None)
      wp.add_argument("--sec", type=float, default=None)
      wargs = wp.parse_args(verb_args)
      walker.wait(
        text=wargs.text,
        text_gone=wargs.text_gone,
        selector=wargs.selector,
        sec=wargs.sec,
      )
      output_lines.append("- Wait condition satisfied")
    elif verb == "screenshot":
      sp = argparse.ArgumentParser(prog="nuu walk screenshot", add_help=False)
      sp.add_argument("target", nargs="?", default=None)
      sp.add_argument("-o", "--output", type=Path, default=None)
      sp.add_argument("--full-page", action="store_true")
      sargs = sp.parse_args(verb_args)
      dest = sargs.output or (out_dir / "screenshot.png")
      dest.parent.mkdir(parents=True, exist_ok=True)
      if sargs.target:
        dest.write_bytes(bytes(walker._locator(sargs.target).screenshot()))
      else:
        walker.screenshot(path=dest, full_page=sargs.full_page)
      output_lines.append(f"- Screenshot saved to `{dest}`")
    elif verb == "pdf":
      pp = argparse.ArgumentParser(prog="nuu walk pdf", add_help=False)
      pp.add_argument("-o", "--output", type=Path, required=True)
      pargs = pp.parse_args(verb_args)
      pargs.output.parent.mkdir(parents=True, exist_ok=True)
      page.pdf(path=str(pargs.output))
      output_lines.append(f"- PDF saved to `{pargs.output}`")
    elif verb == "gif-start":
      state["gif_active"] = True
      state["gif_frames"] = [base64.b64encode(walker.screenshot()).decode("ascii")]
      output_lines.append("- GIF89a recording started")
    elif verb == "gif-frame":
      state.setdefault("gif_frames", []).append(
        base64.b64encode(walker.screenshot()).decode("ascii")
      )
      output_lines.append(f"- Captured GIF frame (`{len(state['gif_frames'])}` total)")
    elif verb == "gif-stop":
      gp = argparse.ArgumentParser(prog="nuu walk gif-stop", add_help=False)
      gp.add_argument("-o", "--output", type=Path, required=True)
      gargs = gp.parse_args(verb_args)
      frames_raw = [
        base64.b64decode(b64) for b64 in (state.get("gif_frames") or [])
      ] or [walker.screenshot()]
      gargs.output.parent.mkdir(parents=True, exist_ok=True)
      gargs.output.write_bytes(encode_gif89a(frames_raw))
      state["gif_active"] = False
      state["gif_frames"] = []
      output_lines.append(
        f"- Wrote `GIF89a` (`{len(frames_raw)}` frames) to `{gargs.output}`"
      )
    else:
      raise ValueError(f"Unsupported walk verb: {verb}")

  _save_session_state(state_file, state)
  print("\n".join(output_lines) + "\n", end="")
  return 0


# Auto-crawl and batch @step execution.


def _run_auto_crawl(
  browser: Any,
  start_url: str,
  *,
  cfg_info: Mapping[str, Any],
  out_dir: Path,
  gif_path: Path | None,
  video_path: Path | None,
  timeout_sec: int,
) -> tuple[list[dict[str, Any]], list[str]]:
  from nuu import enrich_finding_rule, settle_page

  viewports = cfg_info["config"]["audit"]["viewports"]
  severities = cfg_info["severities"]
  findings: list[dict[str, Any]] = []
  captures: list[str] = []
  gif_frames: list[bytes] = []
  origin = urlparse(start_url)

  for idx, vp in enumerate(viewports):
    vw, vh = int(vp["width"]), int(vp["height"])
    ctx_kwargs: dict[str, Any] = {
      "viewport": {"width": vw, "height": vh},
      "color_scheme": "dark",
      "is_mobile": bool(vp.get("mobile")),
      "has_touch": bool(vp.get("touch")),
    }
    if video_path is not None and idx == 0:
      ctx_kwargs["record_video_dir"] = str(out_dir)
      ctx_kwargs["record_video_size"] = {"width": vw, "height": vh}
    ctx = browser.new_context(**ctx_kwargs)
    page = ctx.new_page()
    page.set_default_timeout(max(1, timeout_sec) * 1000)
    walker = Walker(page, Demo(start_url), output_dir=out_dir)
    walker.goto(start_url)
    settle_page(page, timeout_sec)

    shot_file = out_dir / f"walk-{vw}x{vh}.png"
    raw_shot = walker.screenshot(path=shot_file)
    captures.append(shot_file.name)
    if idx == 0 or gif_path is not None:
      gif_frames.append(raw_shot)

    body_txt = page.evaluate("() => (document.body && document.body.innerText) || ''")
    if re.search(
      r"\b(?:lorem\s+ipsum|dolor\s+sit\s+amet|consectetur\s+adipiscing)\b",
      body_txt,
      re.IGNORECASE,
    ):
      findings.append(
        enrich_finding_rule(
          {
            "rule": "HRC203",
            "location": f"{vw}x{vh}",
            "evidence": f"Placeholder Lorem ipsum copy rendered on {start_url}",
          },
          severities,
        )
      )

    if idx == 0:
      for item in probe_interactive_states(page, check_clicks=True):
        findings.append(
          enrich_finding_rule(
            {
              "rule": item["rule"],
              "selector": item.get("selector"),
              "location": f"{vw}x{vh}",
              "evidence": item["evidence"],
            },
            severities,
          )
        )
      if gif_path is not None:
        gif_frames.append(walker.screenshot())

      links = page.evaluate(
        r"""() => Array.from(document.querySelectorAll('a[href]'))
          .map((a) => a.href)
          .filter(Boolean)
          .slice(0, 5)"""
      )
      visited = {start_url}
      for href in links:
        parsed = urlparse(href)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
          continue
        if href in visited:
          continue
        visited.add(href)
        try:
          walker.goto(href)
          if gif_path is not None:
            gif_frames.append(walker.screenshot())
        except Exception as exc:
          findings.append(
            enrich_finding_rule(
              {
                "rule": "RUN101",
                "location": href,
                "evidence": f"Auto-crawl navigation failed: {exc}",
              },
              severities,
            )
          )

      for opt_err in probe_optimistic_writes(page, start_url):
        findings.append(
          enrich_finding_rule(
            {
              "rule": opt_err["rule"],
              "selector": opt_err.get("selector"),
              "location": f"{vw}x{vh}",
              "evidence": opt_err["evidence"],
            },
            severities,
          )
        )

    for err_msg in walker.errors:
      findings.append(
        enrich_finding_rule(
          {
            "rule": "RUN001",
            "location": f"{vw}x{vh}",
            "evidence": f"Console/page error: {err_msg}",
          },
          severities,
        )
      )

    vid_obj = page.video if video_path is not None and idx == 0 else None
    ctx.close()
    if vid_obj is not None and video_path is not None:
      try:
        src_vid = Path(vid_obj.path())
        dest_vid = (
          video_path if video_path.is_absolute() else (out_dir / video_path.name)
        )
        dest_vid.parent.mkdir(parents=True, exist_ok=True)
        if src_vid.is_file() and src_vid.resolve() != dest_vid.resolve():
          shutil.move(str(src_vid), str(dest_vid))
        captures.append(dest_vid.name)
      except Exception:
        pass

  if gif_path is not None and gif_frames:
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    gif_path.write_bytes(encode_gif89a(gif_frames))
    captures.append(str(gif_path))

  return findings, captures


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="nuu walk",
    description="Crawl a live page, drive an interactive 42-verb CDP session, or run @step walk.py.",
  )
  parser.add_argument(
    "target",
    nargs="?",
    default="http://127.0.0.1:1500",
    help="Base URL, port, HTML path, walk.py script, or MCP verb.",
  )
  parser.add_argument("-c", "--config", type=Path, default=None)
  parser.add_argument("-S", "--session", default="default")
  parser.add_argument("-o", "--output", type=Path, default=Path("./nuu-artifacts"))
  parser.add_argument("-g", "--gif", type=Path, default=None)
  parser.add_argument("--video", nargs="?", const="walk.webm", default=None)
  parser.add_argument("-t", "--timeout", type=int, default=20)
  parser.add_argument("-x", "--executable", default=None)
  return parser


def _split_top_and_verb_args(
  raw: Sequence[str],
) -> tuple[argparse.Namespace, list[str]]:
  tokens = list(raw)
  ns = argparse.Namespace(
    config=None,
    session="default",
    output=Path("./nuu-artifacts"),
    gif=None,
    video=None,
    timeout=20,
    executable=None,
  )
  rest: list[str] = []
  seen_verb: str | None = None
  i = 0
  while i < len(tokens):
    tok = tokens[i]
    if tok in {"-c", "--config"} and i + 1 < len(tokens):
      ns.config = Path(tokens[i + 1])
      i += 2
      continue
    if tok in {"-S", "--session"} and i + 1 < len(tokens):
      ns.session = tokens[i + 1]
      i += 2
      continue
    if tok in {"-g", "--gif"} and i + 1 < len(tokens):
      ns.gif = Path(tokens[i + 1])
      i += 2
      continue
    if tok == "--video":
      if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
        ns.video = Path(tokens[i + 1])
        i += 2
      else:
        ns.video = Path("walk.webm")
        i += 1
      continue
    if tok in {"-t", "--timeout"} and i + 1 < len(tokens):
      ns.timeout = int(tokens[i + 1])
      i += 2
      continue
    if tok in {"-x", "--executable"} and i + 1 < len(tokens):
      ns.executable = tokens[i + 1]
      i += 2
      continue
    if (
      tok in {"-o", "--output"}
      and i + 1 < len(tokens)
      and seen_verb not in {"screenshot", "pdf", "gif-stop"}
    ):
      ns.output = Path(tokens[i + 1])
      i += 2
      continue
    if seen_verb is None and not tok.startswith("-") and tok in MCP_VERBS:
      seen_verb = tok
    rest.append(tok)
    i += 1
  return ns, rest


def main(argv: Sequence[str] | None = None) -> int:
  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8", errors="replace")
  raw_list = list(sys.argv[1:] if argv is None else argv)
  if "-h" in raw_list or "--help" in raw_list:
    build_parser().parse_args(raw_list)
    return 0

  from nuu import chromium_launch_kwargs, ensure_artifacts_dir, load_nuu_config

  try:
    ns, rest = _split_top_and_verb_args(raw_list)
    cfg_info = load_nuu_config(None, ns.config)
    out_dir = ensure_artifacts_dir(ns.output)

    if rest and rest[0] in MCP_VERBS:
      return _execute_mcp_verb(
        rest[0],
        rest[1:],
        session=ns.session,
        cfg_info=cfg_info,
        out_dir=out_dir,
        executable=ns.executable,
        timeout_sec=ns.timeout,
      )

    if rest and rest[0].startswith("-"):
      sys.stderr.write(f"nuu walk: unrecognized argument: {rest[0]}\n")
      return 2

    target_str = rest[0] if rest else "http://127.0.0.1:1500"
    target_path = Path(target_str)
    is_py_script = target_path.suffix.lower() == ".py"
    if is_py_script and not target_path.is_file():
      raise FileNotFoundError(f"RUN101: Walk script not found: {target_path}")

    from playwright.sync_api import sync_playwright

    launch_opts = chromium_launch_kwargs(ns.executable)
    viewports = cfg_info["config"]["audit"]["viewports"]
    gif_frames: list[bytes] = []

    with sync_playwright() as pw:
      browser = pw.chromium.launch(**launch_opts)
      try:
        if is_py_script:
          load_walk_module(target_path)
          demo = Demo("http://127.0.0.1:1500")
          open_contexts: list[Any] = []

          def make_page(vp_tuple: tuple[int, int]) -> Any:
            matched = next(
              (
                v
                for v in viewports
                if int(v["width"]) == vp_tuple[0] and int(v["height"]) == vp_tuple[1]
              ),
              {},
            )
            ctx_opts: dict[str, Any] = {
              "viewport": {"width": vp_tuple[0], "height": vp_tuple[1]},
              "color_scheme": "dark",
              "is_mobile": bool(matched.get("mobile")),
              "has_touch": bool(matched.get("touch")),
            }
            if ns.video is not None and not open_contexts:
              ctx_opts["record_video_dir"] = str(out_dir)
              ctx_opts["record_video_size"] = {
                "width": vp_tuple[0],
                "height": vp_tuple[1],
              }
            ctx = browser.new_context(**ctx_opts)
            open_contexts.append(ctx)
            page = ctx.new_page()
            page.set_default_timeout(max(1, ns.timeout) * 1000)
            return page

          step_results = run_steps_with_factory(
            make_page, demo, out_dir=out_dir, viewports=viewports
          )
          for res in step_results:
            if res.get("screenshot") and Path(res["screenshot"]).is_file():
              gif_frames.append(Path(res["screenshot"]).read_bytes())
          for ctx in open_contexts:
            ctx.close()
          if ns.gif is not None and gif_frames:
            ns.gif.parent.mkdir(parents=True, exist_ok=True)
            ns.gif.write_bytes(encode_gif89a(gif_frames))
          print(
            f"# Walk Complete\n\n"
            f"- Steps: `{len(step_results)}`\n"
            f"- Output: `{out_dir}`\n",
            end="",
          )
          return 0

        start_url = Demo().url_for(target_str)
        findings, captures = _run_auto_crawl(
          browser,
          start_url,
          cfg_info=cfg_info,
          out_dir=out_dir,
          gif_path=ns.gif,
          video_path=Path(ns.video) if ns.video is not None else None,
          timeout_sec=ns.timeout,
        )
      finally:
        browser.close()

    blocking = [f for f in findings if f.get("severity") == "error"]
    exit_code = 1 if blocking else 0
    lines = [
      "# Walk Complete",
      "",
      f"- Status: `{'findings' if blocking else 'pass'} (exit {exit_code})`",
      f"- Output: `{out_dir}`",
      f"- Captures: `{len(captures)}`",
      f"- Blocking errors: `{len(blocking)}`",
      "",
      "## Findings",
    ]
    if findings:
      for idx, item in enumerate(findings, 1):
        lines.append(
          f"{idx}. `{item['rule']}` ({item['severity']}) "
          f"`{item.get('location', 'walk')}`: {item['evidence']}"
        )
    else:
      lines.append("- None")
    print("\n".join(lines) + "\n", end="")
    return exit_code
  except SystemExit as exc:
    return int(exc.code) if isinstance(exc.code, int) else 2
  except Exception as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
