"""Deterministic browser walk and named view capture helper for nuu boards."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from collections.abc import Callable, Mapping, Sequence
  from typing import Any

DEVICE_VIEWPORTS: dict[str, tuple[int, int]] = {
  "desktop": (1280, 800),
  "laptop": (960, 640),
  "tablet": (640, 800),
  "mobile": (360, 640),
  "landscape": (640, 360),
}

_REGISTERED_STEPS: list[tuple[str, str, Callable[[Walker, Demo], None]]] = []


class Demo:
  """Fixed-data demo controller passed to @step functions and capture_views."""

  def __init__(self, base_url: str = "http://127.0.0.1:1500") -> None:
    self.base_url = base_url.rstrip("/")
    self.signed_in = True
    self.checked = True
    self.state: dict[str, Any] = {}

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
  """Browser page wrapper that records console errors and step screenshots."""

  def __init__(self, page: Any, demo: Demo | None = None) -> None:
    self.page = page
    self.demo = demo or Demo()
    self.errors: list[str] = []
    if hasattr(self.page, "on"):
      self.page.on("console", self._on_console)
      self.page.on("pageerror", self._on_page_error)

  def _on_console(self, message: Any) -> None:
    msg_type = getattr(message, "type", "")
    if callable(msg_type):
      msg_type = msg_type()
    if msg_type == "error":
      text = getattr(message, "text", "")
      self.errors.append(text() if callable(text) else str(text))

  def _on_page_error(self, error: Any) -> None:
    self.errors.append(str(error))

  def goto(self, target: int | str, wait_until: str = "networkidle") -> Any:
    url = self.demo.url_for(target)
    return self.page.goto(url, wait_until=wait_until)

  def screenshot(self, **kwargs: Any) -> bytes:
    data = self.page.screenshot(**kwargs)
    return bytes(data)


def step(
  device: str = "desktop",
) -> Callable[[Callable[[Walker, Demo], None]], Callable[[Walker, Demo], None]]:
  """Register a deterministic browser walk step for a named device viewport."""
  if device not in DEVICE_VIEWPORTS:
    valid = ", ".join(sorted(DEVICE_VIEWPORTS))
    raise ValueError(f"Unknown walk device {device!r} (expected one of: {valid})")

  def decorator(
    func: Callable[[Walker, Demo], None],
  ) -> Callable[[Walker, Demo], None]:
    func._walk_device = device  # type: ignore[attr-defined]
    _REGISTERED_STEPS.append((func.__name__, device, func))
    return func

  return decorator


def registered_steps() -> list[tuple[str, str, Callable[[Walker, Demo], None]]]:
  """Return registered (name, device, func) walk steps in registration order."""
  return list(_REGISTERED_STEPS)


def clear_steps() -> None:
  """Clear registered walk steps."""
  _REGISTERED_STEPS.clear()


def capture_views(
  walker: Walker,
  demo: Demo,
  views: Mapping[str, int | str | Callable[[Walker, Demo], None]] | None = None,
) -> dict[str, bytes]:
  """Capture named view PNG bytes for nuu board from targets or step callables."""
  shots: dict[str, bytes] = {}
  if not views:
    walker.goto(demo.base_url)
    shots["dashboard"] = walker.screenshot()
    return shots
  for name, target in views.items():
    if callable(target):
      target(walker, demo)
    else:
      walker.goto(target)
    shots[name] = walker.screenshot()
  return shots


def load_walk_module(script_path: Path) -> Any:
  """Load a Python walk module from disk."""
  resolved = Path(script_path).resolve()
  if not resolved.is_file():
    raise FileNotFoundError(f"Walk script not found: {resolved}")
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
) -> list[dict[str, Any]]:
  """Execute walk steps using a page factory and assert no new console errors."""
  active_steps = list(steps) if steps is not None else registered_steps()
  results: list[dict[str, Any]] = []
  if out_dir is not None:
    out_dir.mkdir(parents=True, exist_ok=True)
  for index, (name, device, func) in enumerate(active_steps, 1):
    viewport = DEVICE_VIEWPORTS.get(device, DEVICE_VIEWPORTS["desktop"])
    page = page_factory(viewport)
    walker = Walker(page, demo)
    before_errors = len(walker.errors)
    func(walker, demo)
    new_errors = walker.errors[before_errors:]
    if new_errors:
      raise AssertionError(
        f"Step {name} ({device}) logged console errors: {new_errors}"
      )
    shot_path: str | None = None
    if out_dir is not None:
      target = out_dir / f"{index:02d}-{name}-{device}.png"
      target.write_bytes(walker.screenshot())
      shot_path = str(target)
    results.append(
      {
        "step": name,
        "device": device,
        "viewport": list(viewport),
        "screenshot": shot_path,
      }
    )
  return results


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="nuu walk",
    description="Run deterministic browser walk steps or capture named views for nuu board.",
  )
  parser.add_argument(
    "target",
    nargs="?",
    default="http://127.0.0.1:1500",
    help="Base URL, port, or HTML file path to walk.",
  )
  parser.add_argument(
    "-s",
    "--script",
    type=Path,
    default=None,
    help="Optional Python walk script defining @step functions or capture_views().",
  )
  parser.add_argument(
    "-V",
    "--views",
    action="store_true",
    help="Capture named views via capture_views() instead of running @step functions.",
  )
  parser.add_argument(
    "-o",
    "--out",
    type=Path,
    default=Path("/tmp/walk"),
    help="Directory where step or view PNGs are written.",
  )
  parser.add_argument(
    "-x",
    "--executable",
    default=os.environ.get("CHROMIUM_PATH") or None,
    help="Optional browser executable path (defaults to CHROMIUM_PATH if set).",
  )
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  demo = Demo(demo_url := Demo().url_for(args.target))
  module = load_walk_module(args.script) if args.script is not None else None
  try:
    from playwright.sync_api import sync_playwright
  except ImportError as exc:
    print(f"Playwright is required to run browser walks: {exc}", file=sys.stderr)
    return 1

  args.out.mkdir(parents=True, exist_ok=True)
  launch_opts: dict[str, Any] = {"headless": True}
  if args.executable:
    launch_opts["executable_path"] = str(args.executable)
  with sync_playwright() as pw:
    browser = pw.chromium.launch(**launch_opts)
    try:
      if args.views:
        context = browser.new_context(
          viewport={"width": 1280, "height": 800},
          color_scheme="dark",
        )
        page = context.new_page()
        walker = Walker(page, demo)
        custom_capture = getattr(module, "capture_views", None) if module else None
        shots = (
          custom_capture(walker, demo)
          if callable(custom_capture)
          else capture_views(walker, demo, {"dashboard": demo_url})
        )
        for name, png_bytes in shots.items():
          (args.out / f"{name}.png").write_bytes(png_bytes)
        context.close()
        return 0

      def make_page(viewport: tuple[int, int]) -> Any:
        ctx = browser.new_context(
          viewport={"width": viewport[0], "height": viewport[1]},
          color_scheme="dark",
        )
        return ctx.new_page()

      run_steps_with_factory(make_page, demo, out_dir=args.out)
    finally:
      browser.close()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
