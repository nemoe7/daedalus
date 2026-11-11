"""Draw bounding boxes, labels, arrows, callouts, and composite boards onto PNG images."""

from __future__ import annotations

import base64
import heapq
import html
import itertools
import math
import os
import re
import shutil
import struct
import subprocess
import tomllib
import zlib
from pathlib import Path

from nuu import (
  chromium_launch_kwargs,
  load_nuu_config,
  parse_viewport_spec,
  resolve_renderer,
  settle_page,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
COLORS = {
  "red": (225, 29, 72, 255),
  "blue": (37, 99, 235, 255),
  "green": (22, 163, 74, 255),
  "amber": (217, 119, 6, 255),
  "white": (255, 255, 255, 255),
  "black": (17, 24, 39, 255),
}

DEFAULT_NUU_TOML = Path(__file__).resolve().parent.parent / "references" / "nuu.toml"
DEFAULT_FONT_SANS = '"Inter", system-ui, -apple-system, "Segoe UI", sans-serif'
DEFAULT_FONT_MONO = (
  '"Monaspace Neon", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace'
)
SKIP_DIRS = {
  ".git",
  ".agents",
  "node_modules",
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
}
FONT_MIMES = {
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
}
DEFAULT_RASTER_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_TEXT_RASTER_CACHE: dict[
  tuple[str, tuple[int, int, int, int], int, str], tuple[int, int, bytearray]
] = {}


def parse_color(value):
  if not value:
    return COLORS["red"]
  text = str(value).strip().lower()
  if text in COLORS:
    return COLORS[text]
  if text.startswith("#") and len(text) in {4, 7}:
    if len(text) == 4:
      text = "#" + "".join(ch * 2 for ch in text[1:])
    try:
      return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16), 255)
    except ValueError as error:
      raise ValueError(f"invalid hex color: {value}") from error
  raise ValueError(f"unsupported color: {value}")


def css_hex(color: tuple[int, int, int, int]) -> str:
  return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


def _iter_project_files(root: Path, suffixes: set[str]) -> list[Path]:
  found: list[Path] = []
  if not root.is_dir():
    return found
  for current, dirs, filenames in os.walk(root):
    dirs[:] = sorted(
      d for d in dirs if d not in SKIP_DIRS and not (root / current / d).is_symlink()
    )
    base = Path(current)
    for name in sorted(filenames):
      path = base / name
      if path.suffix.lower() in suffixes and not path.is_symlink():
        found.append(path)
  return found


def _first_str(mapping: dict, keys: tuple[str, ...]) -> str | None:
  for key in keys:
    val = mapping.get(key)
    if isinstance(val, str) and val.strip():
      return val.strip()
  return None


def _extract_primary_family(font_stack: str | None, fallback: str) -> str:
  if not font_stack:
    return fallback
  first = font_stack.split(",", 1)[0].strip().strip("'\"")
  return first or fallback


def resolve_project_typography(
  project_root: Path | None = None, config_path: Path | None = None
) -> dict:
  root = (Path(project_root) if project_root else Path.cwd()).resolve()
  cfg_info = load_nuu_config(root, config_path)
  cfg = cfg_info["config"]
  cfg_dir = cfg_info["dir"] or root
  fonts_tbl = (
    cfg.get("fonts")
    if isinstance(cfg.get("fonts"), dict)
    else cfg.get("font")
    if isinstance(cfg.get("font"), dict)
    else cfg.get("typography")
    if isinstance(cfg.get("typography"), dict)
    else {}
  )
  cfg_sans = _first_str(
    fonts_tbl, ("font_sans", "sans", "default_font", "font_family", "family")
  ) or _first_str(cfg, ("font_sans", "default_font", "font_family"))
  cfg_mono = _first_str(fonts_tbl, ("font_mono", "mono", "monospace")) or _first_str(
    cfg, ("font_mono",)
  )
  prefer_project_css = bool(
    fonts_tbl.get("prefer_project_css", cfg.get("prefer_project_css", False))
  )

  scan_root = (
    cfg_dir
    if (cfg_info["path"] and not prefer_project_css and cfg_dir.is_dir())
    else root
  )
  SKIP_SCAN = SKIP_DIRS | {"arena-state", "tests", "archive"}
  style_files = [
    p
    for p in _iter_project_files(scan_root, {".css", ".html"})
    if not any(part in SKIP_SCAN for part in p.parts)
  ]
  cfg_sheet = cfg_info.get("stylesheet_path")
  if (
    isinstance(cfg_sheet, Path) and cfg_sheet.is_file() and cfg_sheet not in style_files
  ):
    style_files.insert(0, cfg_sheet)
  font_Index: dict[str, Path] = {}
  for font_path in _iter_project_files(scan_root, set(FONT_MIMES)):
    font_Index.setdefault(font_path.name, font_path)
  if not style_files and not font_Index and cfg_info["path"] is None:
    cur = scan_root
    while cur.parent != cur and cur != Path("/tmp"):
      if (cur / ".git").exists():
        scan_root = cur
        root = cur
        style_files = _iter_project_files(scan_root, {".css", ".html"})
        for font_path in _iter_project_files(scan_root, set(FONT_MIMES)):
          font_Index.setdefault(font_path.name, font_path)
        break
      cur = cur.parent

  # Prefer product CSS and HTML with @font-face declarations over reference fallback sheets.
  def style_priority(path: Path) -> tuple[int, int, str]:
    rel = path.relative_to(root).as_posix() if root in path.parents else path.as_posix()
    is_nuu_ref = "nuu/references/style.css" in rel
    is_html = path.suffix.lower() == ".html"
    return (1 if is_nuu_ref else 0, 1 if is_html else 0, rel)

  style_files.sort(key=style_priority)
  font_sans = None
  font_mono = None
  source_css = None
  font_face_rules: list[str] = []
  seen_faces: set[tuple[str, str, str, str]] = set()

  def _embed_font_file(
    candidate: Path, family: str, weight: str = "400", fstyle: str = "normal"
  ) -> None:
    if not candidate.is_file():
      return
    ext = candidate.suffix.lower()
    mime = FONT_MIMES.get(ext)
    if not mime:
      return
    key = (family, weight, fstyle, candidate.name)
    if key in seen_faces:
      return
    seen_faces.add(key)
    b64 = base64.b64encode(candidate.read_bytes()).decode("ascii")
    fmt = "woff2" if ext == ".woff2" else "woff" if ext == ".woff" else "truetype"
    font_face_rules.append(
      f'@font-face {{ font-family: "{family}"; src: url("data:{mime};base64,{b64}") format("{fmt}"); '
      f"font-weight: {weight}; font-style: {fstyle}; font-display: block; }}"
    )

  explicit_files_set = any(
    k in fonts_tbl for k in ("files", "font_files", "faces", "path")
  ) or any(k in cfg for k in ("font_files", "font_path"))
  raw_files = (
    fonts_tbl.get("files")
    if "files" in fonts_tbl
    else (
      fonts_tbl.get("font_files")
      or fonts_tbl.get("faces")
      or fonts_tbl.get("path")
      or cfg.get("font_files")
      or cfg.get("font_path")
    )
  )
  if not explicit_files_set and raw_files is None and DEFAULT_NUU_TOML.is_file():
    try:
      default_raw = tomllib.loads(DEFAULT_NUU_TOML.read_text(encoding="utf-8"))
      default_fonts = (
        default_raw.get("fonts") if isinstance(default_raw.get("fonts"), dict) else {}
      )
      raw_files = default_fonts.get("files")
    except (OSError, tomllib.TOMLDecodeError):
      raw_files = None
  if isinstance(raw_files, (str, dict)):
    raw_files = [raw_files]
  if isinstance(raw_files, list):
    default_fam = _extract_primary_family(cfg_sans or DEFAULT_FONT_SANS, "Inter")
    for item in raw_files:
      if isinstance(item, str):
        p = Path(item)
        cand = p if p.is_absolute() else (cfg_dir / p).resolve()
        if not cand.is_file():
          cand = font_Index.get(p.name) or cand
        _embed_font_file(cand, default_fam)
      elif isinstance(item, dict) and item.get("path"):
        p = Path(str(item["path"]))
        cand = p if p.is_absolute() else (cfg_dir / p).resolve()
        if not cand.is_file():
          cand = font_Index.get(p.name) or cand
        fam = str(item.get("family") or default_fam).strip()
        w = str(item.get("weight") or "400").strip()
        st = str(item.get("style") or "normal").strip()
        _embed_font_file(cand, fam, w, st)

  for css_path in style_files:
    try:
      text = css_path.read_text(encoding="utf-8")
    except OSError:
      continue
    if css_path.suffix.lower() == ".html":
      blocks = re.findall(
        r"<style[^>]*>(.*?)</style>", text, flags=re.IGNORECASE | re.DOTALL
      )
      if not blocks:
        continue
      text = "\n".join(blocks)
    clean = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    css_vars: dict[str, str] = {}
    for m in re.finditer(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;{}]+)", clean):
      css_vars.setdefault(m.group(1), m.group(2).strip())

    def _deref_var(val: str | None, vars_map: dict[str, str] = css_vars) -> str | None:
      cur_val = val
      for _ in range(4):
        if not cur_val:
          return None
        vm = re.fullmatch(r"var\(\s*(--[a-zA-Z0-9-]+)\s*\)", cur_val.strip())
        if not vm:
          return cur_val
        cur_val = vars_map.get(vm.group(1))
      return cur_val

    if font_sans is None:
      cand_sans = _deref_var(
        css_vars.get("--font-sans") or css_vars.get("--sans") or css_vars.get("--font")
      )
      if cand_sans:
        font_sans = cand_sans
        source_css = str(css_path)
      else:
        m_body = re.search(
          r"\bbody\s*\{[^}]*?\bfont-family\s*:\s*([^;{}]+)", clean, flags=re.DOTALL
        )
        if m_body and "var(" not in m_body.group(1):
          font_sans = m_body.group(1).strip()
          source_css = str(css_path)
    if font_mono is None:
      cand_mono = _deref_var(css_vars.get("--font-mono") or css_vars.get("--mono"))
      if cand_mono:
        font_mono = cand_mono
    for block_match in re.finditer(r"@font-face\s*\{([^}]*)\}", clean, flags=re.DOTALL):
      block = block_match.group(1)
      fam_m = re.search(r"font-family\s*:\s*['\"]?([^'\";]+)['\"]?", block)
      url_m = re.search(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", block)
      if not (fam_m and url_m):
        continue
      family = fam_m.group(1).strip()
      raw_url = url_m.group(1).strip().split("?", 1)[0].split("#", 1)[0]
      weight_m = re.search(r"font-weight\s*:\s*([^;]+)", block)
      style_m = re.search(r"font-style\s*:\s*([^;]+)", block)
      weight = weight_m.group(1).strip() if weight_m else "400"
      fstyle = style_m.group(1).strip() if style_m else "normal"
      candidate = (css_path.parent / raw_url.lstrip("/")).resolve()
      if not candidate.is_file():
        candidate = (css_path.parent / Path(raw_url).name).resolve()
      if not candidate.is_file():
        candidate = font_Index.get(Path(raw_url).name)
      if not candidate or not candidate.is_file():
        continue
      _embed_font_file(candidate, family, weight, fstyle)

  resolved_sans = (
    cfg_sans
    or (font_sans if prefer_project_css else None)
    or font_sans
    or DEFAULT_FONT_SANS
  )
  resolved_mono = (
    cfg_mono
    or (font_mono if prefer_project_css else None)
    or font_mono
    or DEFAULT_FONT_MONO
  )
  if cfg_sans and (not prefer_project_css or not source_css):
    source_css = cfg_info["path"]

  return {
    "font_sans": resolved_sans,
    "font_mono": resolved_mono,
    "font_faces_css": "\n".join(font_face_rules),
    "source_css": source_css,
    "config_path": None if cfg_info.get("used_bundled") else cfg_info["path"],
    "embedded_fonts": len(font_face_rules),
  }


def paeth(a, b, c):
  p = a + b - c
  pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
  if pa <= pb and pa <= pc:
    return a
  return b if pb <= pc else c


def decode_png(raw: bytes) -> tuple[int, int, bytearray]:
  if not raw.startswith(PNG_SIGNATURE):
    raise ValueError("input file is not a valid PNG")
  pos = len(PNG_SIGNATURE)
  width = height = bit_depth = color_type = interlace = None
  palette = b""
  trns = b""
  compressed = bytearray()
  while pos + 8 <= len(raw):
    length, chunk_type = struct.unpack(">I4s", raw[pos : pos + 8])
    pos += 8
    if pos + length + 4 > len(raw):
      raise ValueError("truncated PNG chunk")
    chunk_data = raw[pos : pos + length]
    pos += length + 4
    if chunk_type == b"IHDR":
      width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
        ">IIBBBBB", chunk_data
      )
      if comp != 0 or filt != 0 or interlace != 0 or bit_depth != 8:
        raise ValueError("unsupported PNG format (requires 8-bit non-interlaced PNG)")
    elif chunk_type == b"PLTE":
      palette = chunk_data
    elif chunk_type == b"tRNS":
      trns = chunk_data
    elif chunk_type == b"IDAT":
      compressed.extend(chunk_data)
    elif chunk_type == b"IEND":
      break
  if not width or not height or not compressed:
    raise ValueError("incomplete PNG image")
  channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
  if channels is None:
    raise ValueError("unsupported PNG color type")
  stride = width * channels
  data = zlib.decompress(bytes(compressed))
  if len(data) < height * (stride + 1):
    raise ValueError("corrupt PNG scanline data")
  unfiltered = bytearray(height * stride)
  offset = 0
  for row in range(height):
    filter_type = data[offset]
    offset += 1
    row_start = row * stride
    prev_start = (row - 1) * stride
    for col in range(stride):
      val = data[offset + col]
      left = unfiltered[row_start + col - channels] if col >= channels else 0
      up = unfiltered[prev_start + col] if row > 0 else 0
      up_left = (
        unfiltered[prev_start + col - channels] if row > 0 and col >= channels else 0
      )
      if filter_type == 0:
        recon = val
      elif filter_type == 1:
        recon = (val + left) & 0xFF
      elif filter_type == 2:
        recon = (val + up) & 0xFF
      elif filter_type == 3:
        recon = (val + ((left + up) >> 1)) & 0xFF
      elif filter_type == 4:
        recon = (val + paeth(left, up, up_left)) & 0xFF
      else:
        raise ValueError(f"unsupported PNG filter type {filter_type}")
      unfiltered[row_start + col] = recon
    offset += stride
  rgba = bytearray(width * height * 4)
  for index in range(width * height):
    src = index * channels
    dst = index * 4
    if color_type == 6:
      rgba[dst : dst + 4] = unfiltered[src : src + 4]
    elif color_type == 2:
      rgba[dst : dst + 3] = unfiltered[src : src + 3]
      rgba[dst + 3] = 255
    elif color_type == 0:
      gray = unfiltered[src]
      rgba[dst : dst + 4] = bytes((gray, gray, gray, 255))
    elif color_type == 4:
      gray, alpha = unfiltered[src], unfiltered[src + 1]
      rgba[dst : dst + 4] = bytes((gray, gray, gray, alpha))
    elif color_type == 3:
      pal_idx = unfiltered[src]
      if pal_idx * 3 + 3 > len(palette):
        raise ValueError("palette index out of range")
      r, g, b = palette[pal_idx * 3 : pal_idx * 3 + 3]
      alpha = trns[pal_idx] if pal_idx < len(trns) else 255
      rgba[dst : dst + 4] = bytes((r, g, b, alpha))
  return width, height, rgba


def chunk(chunk_type: bytes, data: bytes) -> bytes:
  crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
  return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def encode_png(width: int, height: int, rgba: bytearray) -> bytes:
  stride = width * 4
  raw_rows = bytearray()
  for row in range(height):
    raw_rows.append(0)
    start = row * stride
    raw_rows.extend(rgba[start : start + stride])
  ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
  idat = zlib.compress(bytes(raw_rows), level=6)
  return (
    PNG_SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
  )


def crop_rgba(
  width: int, height: int, rgba: bytearray, x: int, y: int, w: int, h: int
) -> tuple[int, int, bytearray]:
  x0 = max(0, min(width, x))
  y0 = max(0, min(height, y))
  x1 = max(x0, min(width, x + w))
  y1 = max(y0, min(height, y + h))
  cw, ch = x1 - x0, y1 - y0
  if cw <= 0 or ch <= 0:
    raise ValueError("crop rectangle is outside image bounds")
  out = bytearray(cw * ch * 4)
  for row in range(ch):
    src_start = ((y0 + row) * width + x0) * 4
    dst_start = row * cw * 4
    out[dst_start : dst_start + cw * 4] = rgba[src_start : src_start + cw * 4]
  return cw, ch, out


def resize_rgba(
  width: int, height: int, rgba: bytearray, out_width: int, out_height: int
) -> bytearray:
  if out_width <= 0 or out_height <= 0:
    raise ValueError("resized image dimensions must be positive")
  if width == out_width and height == out_height:
    return bytearray(rgba)
  output = bytearray(out_width * out_height * 4)
  for y in range(out_height):
    source_y = min(height - 1, y * height // out_height)
    for x in range(out_width):
      source_x = min(width - 1, x * width // out_width)
      source_index = (source_y * width + source_x) * 4
      output_index = (y * out_width + x) * 4
      output[output_index : output_index + 4] = rgba[source_index : source_index + 4]
  return output


def blit_rgba(
  dst: bytearray,
  dst_w: int,
  dst_h: int,
  src: bytearray,
  src_w: int,
  src_h: int,
  dx: int,
  dy: int,
):
  for row in range(src_h):
    ty = dy + row
    if not (0 <= ty < dst_h):
      continue
    for col in range(src_w):
      tx = dx + col
      if not (0 <= tx < dst_w):
        continue
      s_idx = (row * src_w + col) * 4
      blend_pixel(dst, dst_w, dst_h, tx, ty, src[s_idx : s_idx + 4])


def blend_pixel(rgba: bytearray, width: int, height: int, x: int, y: int, color):
  if not (0 <= x < width and 0 <= y < height):
    return
  sr, sg, sb, sa = color
  if sa <= 0:
    return
  idx = (y * width + x) * 4
  if sa >= 255:
    rgba[idx : idx + 4] = bytes((sr, sg, sb, 255))
    return
  dr, dg, db, da = rgba[idx : idx + 4]
  if da <= 0:
    rgba[idx : idx + 4] = bytes((sr, sg, sb, sa))
    return
  sa_f = sa / 255.0
  da_f = da / 255.0
  out_a = sa_f + da_f * (1.0 - sa_f)
  if out_a <= 0:
    rgba[idx : idx + 4] = b"\x00\x00\x00\x00"
    return
  rgba[idx] = min(255, round((sr * sa_f + dr * da_f * (1.0 - sa_f)) / out_a))
  rgba[idx + 1] = min(255, round((sg * sa_f + dg * da_f * (1.0 - sa_f)) / out_a))
  rgba[idx + 2] = min(255, round((sb * sa_f + db * da_f * (1.0 - sa_f)) / out_a))
  rgba[idx + 3] = min(255, round(out_a * 255.0))


def fill_rect(rgba, width, height, x, y, w, h, color):
  for py in range(max(0, y), min(height, y + h)):
    for px in range(max(0, x), min(width, x + w)):
      blend_pixel(rgba, width, height, px, py, color)


def draw_box(rgba, width, height, x, y, w, h, color, thickness=2):
  if w <= 0 or h <= 0:
    raise ValueError("box width and height must be positive")
  for t in range(thickness):
    for px in range(x + t, x + w - t):
      blend_pixel(rgba, width, height, px, y + t, color)
      blend_pixel(rgba, width, height, px, y + h - 1 - t, color)
    for py in range(y + t, y + h - t):
      blend_pixel(rgba, width, height, x + t, py, color)
      blend_pixel(rgba, width, height, x + w - 1 - t, py, color)


def draw_line(rgba, width, height, x1, y1, x2, y2, color, radius=1):
  steps = int(max(abs(x2 - x1), abs(y2 - y1), 1))
  for step in range(steps + 1):
    t = step / steps
    cx = round(x1 + (x2 - x1) * t)
    cy = round(y1 + (y2 - y1) * t)
    for dy in range(-radius, radius + 1):
      for dx in range(-radius, radius + 1):
        blend_pixel(rgba, width, height, cx + dx, cy + dy, color)


def draw_arrow(rgba, width, height, x1, y1, x2, y2, color):
  draw_line(rgba, width, height, x1, y1, x2, y2, color, radius=1)
  angle = math.atan2(y2 - y1, x2 - x1)
  head_len = 10
  for side in (-0.5, 0.5):
    hx = round(x2 - head_len * math.cos(angle + side))
    hy = round(y2 - head_len * math.sin(angle + side))
    draw_line(rgba, width, height, x2, y2, hx, hy, color, radius=1)


def _rasterize_text(
  text: str,
  color: tuple[int, int, int, int],
  scale: int = 1,
  font_path: str | None = None,
) -> tuple[int, int, bytearray]:
  label = str(text)
  if not label:
    return 1, 1, bytearray(4)
  resolved_font = (
    font_path
    if font_path and Path(font_path).is_file()
    else str(DEFAULT_RASTER_FONT)
    if DEFAULT_RASTER_FONT.is_file()
    else "DejaVu-Sans-Bold"
  )
  cache_key = (label, tuple(color), max(1, int(scale)), resolved_font)
  cached = _TEXT_RASTER_CACHE.get(cache_key)
  if cached is not None:
    return cached
  point_size = max(11, 6 * max(1, int(scale)) + 1)
  convert_bin = shutil.which("convert")
  if convert_bin:
    cmd = [
      convert_bin,
      "-background",
      "none",
      "-fill",
      css_hex(color),
      "-font",
      resolved_font,
      "-pointsize",
      str(point_size),
      f"label:{label}",
      "-depth",
      "8",
      "-define",
      "png:color-type=6",
      "PNG32:-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=10, check=False)
    if proc.returncode == 0 and proc.stdout:
      tw, th, t_rgba = decode_png(proc.stdout)
      _TEXT_RASTER_CACHE[cache_key] = (tw, th, t_rgba)
      return tw, th, t_rgba
  tw = max(1, len(label) * 6 * max(1, int(scale)))
  th = max(1, 8 * max(1, int(scale)))
  fallback = bytearray(tw * th * 4)
  for idx in range(0, len(fallback), 4):
    fallback[idx : idx + 4] = bytes(color)
  _TEXT_RASTER_CACHE[cache_key] = (tw, th, fallback)
  return tw, th, fallback


def draw_text(rgba, width, height, x, y, text, color, scale=1, font_path=None):
  tw, th, t_rgba = _rasterize_text(str(text), color, scale=scale, font_path=font_path)
  blit_rgba(rgba, width, height, t_rgba, tw, th, int(x), int(y))


def draw_label(rgba, width, height, x, y, text, color, scale=2, font_path=None):
  label = str(text).strip()
  if not label:
    raise ValueError("label text must not be empty")
  tw, th, t_rgba = _rasterize_text(
    label, COLORS["white"], scale=scale, font_path=font_path
  )
  pad_x = 3 * scale + 2
  pad_y = 2 * scale + 1
  box_w = tw + pad_x * 2
  box_h = th + pad_y * 2
  fill_rect(rgba, width, height, x, y, box_w, box_h, (17, 24, 39, 238))
  draw_box(rgba, width, height, x, y, box_w, box_h, color, thickness=max(1, scale // 2))
  blit_rgba(rgba, width, height, t_rgba, tw, th, int(x + pad_x), int(y + pad_y))


def draw_callout(
  rgba, width, height, x, y, number, color, label=None, scale=2, font_path=None
):
  badge = str(number).strip()
  if not badge:
    raise ValueError("callout number must not be empty")
  radius = 7 * scale + 3
  border = max(2, scale)
  for dy in range(-radius - border, radius + border + 1):
    for dx in range(-radius - border, radius + border + 1):
      dist = math.hypot(dx, dy)
      if dist <= radius - 1:
        blend_pixel(rgba, width, height, x + dx, y + dy, color)
      elif dist <= radius + border - 1:
        blend_pixel(rgba, width, height, x + dx, y + dy, COLORS["white"])
  tw, th, t_rgba = _rasterize_text(
    badge, COLORS["white"], scale=scale, font_path=font_path
  )
  blit_rgba(rgba, width, height, t_rgba, tw, th, int(x - tw // 2), int(y - th // 2))
  if label:
    _, l_th, _ = _rasterize_text(
      str(label).strip(), COLORS["white"], scale=scale, font_path=font_path
    )
    pad_y = 2 * scale + 1
    box_h = l_th + pad_y * 2
    draw_label(
      rgba,
      width,
      height,
      x + radius + 4 * scale,
      y - box_h // 2,
      label,
      color,
      scale=scale,
      font_path=font_path,
    )


def parse_box(item):
  if isinstance(item, dict):
    return (
      int(item["x"]),
      int(item["y"]),
      int(item["width"]),
      int(item["height"]),
      parse_color(item.get("color")),
    )
  parts = [part.strip() for part in str(item).split(",")]
  if len(parts) not in {4, 5}:
    raise ValueError("box requires x,y,width,height[,color]")
  return (
    int(parts[0]),
    int(parts[1]),
    int(parts[2]),
    int(parts[3]),
    parse_color(parts[4] if len(parts) == 5 else None),
  )


def parse_label(item):
  if isinstance(item, dict):
    return (
      int(item["x"]),
      int(item["y"]),
      str(item["text"]),
      parse_color(item.get("color")),
    )
  parts = [part.strip() for part in str(item).split(",")]
  if len(parts) not in {3, 4}:
    raise ValueError("label requires x,y,text[,color]")
  return (
    int(parts[0]),
    int(parts[1]),
    parts[2],
    parse_color(parts[3] if len(parts) == 4 else None),
  )


def parse_arrow(item):
  if isinstance(item, dict):
    return (
      int(item["x1"]),
      int(item["y1"]),
      int(item["x2"]),
      int(item["y2"]),
      parse_color(item.get("color")),
    )
  parts = [part.strip() for part in str(item).split(",")]
  if len(parts) not in {4, 5}:
    raise ValueError("arrow requires x1,y1,x2,y2[,color]")
  return (
    int(parts[0]),
    int(parts[1]),
    int(parts[2]),
    int(parts[3]),
    parse_color(parts[4] if len(parts) == 5 else None),
  )


def parse_callout(item):
  if isinstance(item, dict):
    return (
      int(item["x"]),
      int(item["y"]),
      str(item["number"]),
      parse_color(item.get("color")),
      item.get("label"),
    )
  parts = [part.strip() for part in str(item).split(",")]
  if len(parts) not in {3, 4, 5}:
    raise ValueError("callout requires x,y,number[,label[,color]]")
  return (
    int(parts[0]),
    int(parts[1]),
    parts[2],
    parse_color(parts[4] if len(parts) == 5 else None),
    parts[3] if len(parts) >= 4 and parts[3] else None,
  )


def parse_legend_callout(item):
  if isinstance(item, dict):
    return (
      str(item["number"]).strip(),
      str(item.get("label") or "").strip(),
      parse_color(item.get("color")),
    )
  parts = [part.strip() for part in str(item).split(",")]
  if len(parts) == 2:
    return parts[0], parts[1], COLORS["red"]
  if len(parts) == 3:
    return parts[0], parts[1], parse_color(parts[2])
  if len(parts) >= 4:
    # Also accept x,y,number,label[,color] and extract number,label,color
    return (
      parts[2],
      parts[3],
      parse_color(parts[4] if len(parts) >= 5 else None),
    )
  raise ValueError("board callout requires number,label[,color]")


def _chromium_render_png(
  html_doc: str,
  width: int,
  height: int,
  output_path: Path,
  selector: str = "#nuu-render-root",
  settle_ms: int = 0,
  executable: str | Path | None = None,
) -> bool:
  mode = resolve_renderer()
  if mode == "bitmap":
    return False
  try:
    from playwright.sync_api import sync_playwright

    launch_kwargs = chromium_launch_kwargs(executable)
  except Exception:
    if mode == "chromium":
      raise
    return False

  vw = max(1600, max(1, int(width)) + 64)
  vh = max(900, max(1, int(height)) + 64)
  try:
    with sync_playwright() as pw:
      browser = pw.chromium.launch(**launch_kwargs)
      try:
        context = browser.new_context(
          viewport={"width": vw, "height": vh},
          device_scale_factor=1,
          color_scheme="dark",
        )
        page = context.new_page()
        page.set_content(html_doc, wait_until="load")
        page.evaluate("() => document.fonts ? document.fonts.ready : Promise.resolve()")
        if settle_ms > 0:
          page.wait_for_timeout(max(0, int(settle_ms)))
        root = page.locator(selector or "#nuu-render-root").first
        rect = root.evaluate(
          """el => {
            const r = el.getBoundingClientRect();
            return {
              width: Math.ceil(Math.max(r.width, el.scrollWidth, el.offsetWidth)),
              height: Math.ceil(Math.max(r.height, el.scrollHeight, el.offsetHeight)),
            };
          }"""
        )
        if rect["width"] + 64 > vw or rect["height"] + 64 > vh:
          page.set_viewport_size(
            {
              "width": max(vw, int(rect["width"]) + 64),
              "height": max(vh, int(rect["height"]) + 64),
            }
          )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        root.screenshot(path=str(output_path), omit_background=True)
        context.close()
      finally:
        browser.close()
  except Exception:
    if mode == "chromium":
      raise
    return False
  return output_path.is_file()


def _overlay_svg_and_nodes(
  width: int,
  height: int,
  boxes,
  arrows,
  labels,
  callouts,
  scale: int,
) -> str:
  parts: list[str] = []
  font_px = max(11, 6 * scale + 1)
  badge_size = max(20, 12 * scale)
  stroke_w = max(2, scale)
  for x, y, w, h, color in boxes:
    if w <= 0 or h <= 0:
      raise ValueError("box width and height must be positive")
    hex_c = css_hex(color)
    parts.append(
      f'<div style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;'
      f"border:{stroke_w}px solid {hex_c};background:none;"
      f'border-radius:4px;box-sizing:border-box;pointer-events:none;"></div>'
    )
  if arrows:
    svg_lines: list[str] = [
      (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'style="position:absolute;inset:0;pointer-events:none;overflow:visible;">'
      )
    ]
    for _idx, (x1, y1, x2, y2, color) in enumerate(arrows):
      hex_c = css_hex(color)
      angle = math.atan2(y2 - y1, x2 - x1)
      head_len = 8 + scale * 2
      hx1 = x2 - head_len * math.cos(angle - 0.5)
      hy1 = y2 - head_len * math.sin(angle - 0.5)
      hx2 = x2 - head_len * math.cos(angle + 0.5)
      hy2 = y2 - head_len * math.sin(angle + 0.5)
      svg_lines.append(
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{hex_c}" '
        f'stroke-width="{stroke_w}" stroke-linecap="round" />'
        f'<polygon points="{x2},{y2} {hx1:.1f},{hy1:.1f} {hx2:.1f},{hy2:.1f}" fill="{hex_c}" />'
      )
    svg_lines.append("</svg>")
    parts.append("".join(svg_lines))
  for x, y, text, color in labels:
    label_txt = str(text).strip()
    if not label_txt:
      raise ValueError("label text must not be empty")
    hex_c = css_hex(color)
    parts.append(
      f'<div style="position:absolute;left:{x}px;top:{y}px;'
      f"background:var(--surface-2);color:var(--text);border:{max(1, scale // 2)}px solid {hex_c};"
      f"border-radius:var(--radius-sm);padding:{max(2, scale)}px {max(6, scale * 3)}px;"
      f"font-family:var(--nuu-font-sans);font-size:{font_px}px;font-weight:600;line-height:1.25;"
      f'white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,0.35);">{html.escape(label_txt)}</div>'
    )
  for x, y, number, color, label in callouts:
    badge = str(number).strip()
    if not badge:
      raise ValueError("callout number must not be empty")
    hex_c = css_hex(color)
    r = badge_size // 2
    esc_badge = html.escape(badge)
    parts.append(
      f'<div style="position:absolute;left:{x - r}px;top:{y - r}px;width:{badge_size}px;height:{badge_size}px;'
      f"border-radius:var(--radius-pill);background:{hex_c};border:2px solid var(--on-accent);color:var(--on-accent);"
      f"display:flex;align-items:center;justify-content:center;font-family:var(--nuu-font-sans);"
      f"font-size:{font_px}px;font-weight:700;line-height:1;box-sizing:border-box;"
      f'box-shadow:0 2px 6px rgba(0,0,0,0.4);">{esc_badge}</div>'
    )
    if label:
      esc_label = html.escape(str(label))
      parts.append(
        f'<div style="position:absolute;left:{x + r + 6}px;top:{y - r}px;min-height:{badge_size}px;'
        f"display:inline-flex;align-items:center;background:var(--surface-2);color:var(--text);"
        f"border:1.5px solid {hex_c};border-radius:var(--radius-sm);padding:2px 8px;"
        f"font-family:var(--nuu-font-sans);font-size:{font_px}px;font-weight:600;line-height:1.2;"
        f'white-space:nowrap;box-sizing:border-box;box-shadow:0 2px 6px rgba(0,0,0,0.35);">{esc_label}</div>'
      )
  return "".join(parts)


def parse_crop(spec) -> tuple[int, int, int, int] | str | None:
  if spec is None or str(spec).strip().lower() == "none":
    return None
  if str(spec).strip().lower() in {"auto", "pad"}:
    return "auto"
  if isinstance(spec, (tuple, list)) and len(spec) == 4:
    cx, cy, cw, ch = (int(v) for v in spec)
  else:
    parts = [p.strip() for p in str(spec).split(",") if p.strip()]
    if len(parts) != 4:
      raise ValueError(f"crop requires x,y,w,h or auto: {spec}")
    cx, cy, cw, ch = (int(p) for p in parts)
  if cw <= 0 or ch <= 0:
    raise ValueError("crop width and height must be positive")
  return cx, cy, cw, ch


def _resolve_annotations_and_crop(
  width: int,
  height: int,
  boxes,
  arrows,
  labels,
  callouts,
  parsed_crop: tuple[int, int, int, int] | str | None,
  scale: int,
):
  font_px = max(11, 6 * scale + 1)
  badge_size = max(20, 12 * scale)
  r = badge_size // 2
  adjusted_callouts = []
  for cx, cy, number, color, label in callouts:
    nx, ny = int(cx), int(cy)
    pill_w = badge_size + (
      6 + int(len(str(label)) * font_px * 0.62) + 20 if label else 0
    )
    for bx, by, bw, bh, _ in boxes:
      if bh <= 120 and bx <= nx <= bx + bw and by <= ny <= by + bh:
        if by + bh + r + 8 <= height - r - 2:
          ny = by + bh + r + 8
        elif by - r - 8 >= r + 2:
          ny = by - r - 8
        nx = max(r + 2, bx + r)
        break
    nx = max(r + 2, min(nx, max(r + 2, width - pill_w + r - 2)))
    ny = max(r + 2, min(ny, max(r + 2, height - r - 2)))
    adjusted_callouts.append((nx, ny, number, color, label))

  if parsed_crop is None:
    return adjusted_callouts, None

  if parsed_crop == "auto":
    min_x, min_y, max_x, max_y = width, height, 0, 0
    for bx, by, bw, bh, _ in boxes:
      min_x, min_y = min(min_x, bx), min(min_y, by)
      max_x, max_y = max(max_x, bx + bw), max(max_y, by + bh)
    for x1, y1, x2, y2, _ in arrows:
      min_x, min_y = min(min_x, x1, x2), min(min_y, y1, y2)
      max_x, max_y = max(max_x, x1, x2), max(max_y, y1, y2)
    for lx, ly, ltxt, _ in labels:
      lw = int(len(str(ltxt)) * font_px * 0.62) + 20
      min_x, min_y = min(min_x, lx), min(min_y, ly)
      max_x, max_y = max(max_x, lx + lw), max(max_y, ly + badge_size)
    for cx, cy, _, _, label in adjusted_callouts:
      pill_w = badge_size + (
        6 + int(len(str(label)) * font_px * 0.62) + 20 if label else 0
      )
      min_x, min_y = min(min_x, cx - r), min(min_y, cy - r)
      max_x, max_y = max(max_x, cx - r + pill_w), max(max_y, cy + r)
    if max_x <= min_x or max_y <= min_y:
      return adjusted_callouts, (0, 0, width, height)
    pad = 16
    cx0 = max(0, min_x - pad)
    cy0 = max(0, min_y - pad)
    cx1 = min(width, max_x + pad)
    cy1 = min(height, max_y + pad)
    return adjusted_callouts, (cx0, cy0, max(1, cx1 - cx0), max(1, cy1 - cy0))

  cx, cy, cw, ch = parsed_crop
  cx0, cy0 = max(0, cx), max(0, cy)
  cx1, cy1 = min(width, cx + cw), min(height, cy + ch)
  for bx, by, bw, bh, _ in boxes:
    if bw <= cw and (bx < cx0 or bx + bw > cx1):
      cx0 = max(0, min(cx0, bx - 8))
      cx1 = min(width, max(cx1, bx + bw + 8))
    if bh <= ch and (by < cy0 or by + bh > cy1):
      cy0 = max(0, min(cy0, by - 8))
      cy1 = min(height, max(cy1, by + bh + 8))
  clamped_callouts = []
  for qx, qy, number, color, label in adjusted_callouts:
    pill_w = badge_size + (
      6 + int(len(str(label)) * font_px * 0.62) + 20 if label else 0
    )
    if qy + r > cy1:
      cy1 = min(height, max(cy1, qy + r + 8))
    if qy - r < cy0:
      cy0 = max(0, min(cy0, qy - r - 8))
    qx = max(cx0 + r + 2, min(qx, max(cx0 + r + 2, cx1 - pill_w + r - 2)))
    qy = max(cy0 + r + 2, min(qy, max(cy0 + r + 2, cy1 - r - 2)))
    clamped_callouts.append((qx, qy, number, color, label))
  return clamped_callouts, (cx0, cy0, max(1, cx1 - cx0), max(1, cy1 - cy0))


BOARD_COLOR_TOKENS = (
  "--bg",
  "--surface",
  "--surface-2",
  "--line",
  "--text",
  "--muted",
  "--accent",
  "--accent-fill",
  "--on-accent",
  "--danger",
  "--warn",
  "--ok",
  "--focus",
)
BOARD_REQUIRED_TOKENS = BOARD_COLOR_TOKENS + (
  "--radius-sm",
  "--radius-md",
  "--radius-lg",
  "--radius-pill",
  "--font-sans",
  "--font-mono",
)
CSS_VAR_RE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*(?:,\s*([^()]*))?\)", re.IGNORECASE)


def _stylesheet_css(path: Path) -> str:
  try:
    source = path.read_text(encoding="utf-8")
  except OSError:
    return ""
  if path.suffix.lower() in {".html", ".htm"}:
    return "\n".join(
      re.findall(r"<style\b[^>]*>(.*?)</style\s*>", source, re.IGNORECASE | re.DOTALL)
    )
  return source


def _css_root_tokens(source: str) -> tuple[dict[str, str], dict[str, str]]:
  from measure import parse_sheet

  source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
  blocks: list[tuple[tuple[str, ...], str, str]] = []
  stack: list[tuple[str, int]] = []
  index = 0
  while index < len(source):
    opening = source.find("{", index)
    closing = source.find("}", index)
    if opening < 0 and closing < 0:
      break
    if opening >= 0 and (closing < 0 or opening < closing):
      boundary = max(source.rfind("{", 0, opening), source.rfind("}", 0, opening)) + 1
      stack.append((source[boundary:opening].strip(), opening + 1))
      index = opening + 1
      continue
    if stack:
      selector, body_start = stack.pop()
      blocks.append(
        (tuple(item[0] for item in stack), selector, source[body_start:closing])
      )
    index = closing + 1

  light: dict[str, str] = {}
  dark: dict[str, str] = {}
  for parents, selector, body in blocks:
    if ":root" not in selector.lower():
      continue
    properties = parse_sheet(body)
    in_dark_scheme = any(
      re.search(r"prefers-color-scheme\s*:\s*dark", parent, re.IGNORECASE)
      for parent in parents
    )
    dark_root = bool(re.search(r"data-theme\s*=\s*['\"]?dark", selector, re.IGNORECASE))
    target = dark if in_dark_scheme or dark_root else light
    target.update({f"--{name}": value for name, value in properties.items()})
  return light, dark


def _resolve_css_value(value: str, properties: dict[str, str], seen=()) -> str:
  def replace(match: re.Match) -> str:
    name = match.group(1)
    fallback = (match.group(2) or "").strip()
    if name in seen:
      return fallback
    candidate = properties.get(name, fallback)
    if not candidate:
      return ""
    return _resolve_css_value(candidate, properties, (*seen, name))

  resolved = str(value).strip()
  for _ in range(8):
    updated = CSS_VAR_RE.sub(replace, resolved)
    if updated == resolved:
      break
    resolved = updated
  return resolved.strip()


def _resolve_board_token_sets(
  project_root: Path | None = None, stylesheet_path: Path | None = None
) -> dict[str, dict[str, str]]:
  from measure import parse_sheet

  root = Path(project_root or Path.cwd()).resolve()
  cfg_info = load_nuu_config(root)
  configured = stylesheet_path or cfg_info.get("stylesheet_path")
  configured_path = Path(configured) if configured else None
  if configured_path is not None and not configured_path.is_absolute():
    configured_path = (root / configured_path).resolve()
  reference_path = DEFAULT_NUU_TOML.parent / "style.css"
  reference_css = _stylesheet_css(reference_path)
  project_css = (
    _stylesheet_css(configured_path)
    if configured_path is not None and configured_path.is_file()
    else ""
  )
  reference_all = {
    f"--{name}": value for name, value in parse_sheet(reference_css).items()
  }
  reference_root, reference_dark = _css_root_tokens(reference_css)
  project_root_tokens, project_dark = _css_root_tokens(project_css)
  project_all = {f"--{name}": value for name, value in parse_sheet(project_css).items()}
  light = {**reference_root, **project_root_tokens}
  dark = {**reference_root, **reference_dark, **project_root_tokens, **project_dark}
  if not reference_root:
    dark = {**reference_all, **project_all}
    light = {**reference_all, **project_root_tokens}
  for values in (light, dark):
    for key in BOARD_REQUIRED_TOKENS:
      values.setdefault(key, reference_all.get(key, ""))
    for key in tuple(values):
      values[key] = _resolve_css_value(values[key], values)
  return {"light": light, "dark": dark}


def _resolve_board_tokens(
  project_root: Path | None = None,
  *,
  theme: str = "dark",
  stylesheet_path: Path | None = None,
) -> dict[str, str]:
  if theme not in {"light", "dark"}:
    raise ValueError("board theme must be light or dark")
  return _resolve_board_token_sets(project_root, stylesheet_path)[theme]


def _board_token_css(tokens: dict[str, str]) -> str:
  return "; ".join(f"{key}: {tokens[key]}" for key in sorted(tokens) if tokens[key])


def _token_rgba(tokens: dict[str, str], name: str) -> tuple[int, int, int, int]:
  value = _resolve_css_value(tokens.get(name, ""), tokens)
  try:
    return parse_color(value)
  except ValueError:
    match = re.fullmatch(r"rgba?\(([^)]+)\)", value.strip(), re.IGNORECASE)
    if match:
      channels = [part.strip() for part in match.group(1).split(",")]
      if len(channels) >= 3:
        rgb = tuple(
          max(
            0,
            min(
              255,
              round(
                float(channel.rstrip("%")) * (2.55 if channel.endswith("%") else 1)
              ),
            ),
          )
          for channel in channels[:3]
        )
        alpha = (
          max(0, min(255, round(float(channels[3]) * 255)))
          if len(channels) > 3
          else 255
        )
        return (*rgb, alpha)
    return COLORS["black"]


def _box_values(box) -> tuple[float, float, float, float]:
  if isinstance(box, dict):
    return (
      float(box.get("x", 0)),
      float(box.get("y", 0)),
      float(box.get("width", box.get("w", 0))),
      float(box.get("height", box.get("h", 0))),
    )
  return tuple(float(value) for value in box[:4])


def _nearest_box_edges(box_a, box_b) -> tuple[tuple[int, int], tuple[int, int]]:
  ax, ay, aw, ah = _box_values(box_a)
  bx, by, bw, bh = _box_values(box_b)
  ar, ab, br, bb = ax + aw, ay + ah, bx + bw, by + bh
  acx, acy = ax + aw / 2, ay + ah / 2
  bcx, bcy = bx + bw / 2, by + bh / 2
  dx, dy = bcx - acx, bcy - acy
  if abs(dx) >= abs(dy):
    start_x, end_x = (ar, bx) if dx >= 0 else (ax, br)
    start, end = (start_x, acy), (end_x, bcy)
  else:
    start_y, end_y = (ab, by) if dy >= 0 else (ay, bb)
    start, end = (acx, start_y), (bcx, end_y)
  return (round(start[0]), round(start[1])), (round(end[0]), round(end[1]))


def _segment_hits_box(start, end, box) -> bool:
  x, y, width, height = _box_values(box)
  left, right, top, bottom = x, x + width, y, y + height
  x1, y1 = start
  x2, y2 = end
  if x1 == x2:
    return left < x1 < right and max(min(y1, y2), top) < min(max(y1, y2), bottom)
  if y1 == y2:
    return top < y1 < bottom and max(min(x1, x2), left) < min(max(x1, x2), right)
  return False


def _orthogonal_route_points(start, end, obstacles=()) -> list[tuple[float, float]]:
  start = tuple(float(value) for value in start)
  end = tuple(float(value) for value in end)
  boxes = [_box_values(obstacle) for obstacle in obstacles]

  def inside(point, box) -> bool:
    x, y, width, height = box
    return x < point[0] < x + width and y < point[1] < y + height

  def find_path(clearance: float) -> list[tuple[float, float]] | None:
    expanded = [
      (x - clearance, y - clearance, width + clearance * 2, height + clearance * 2)
      for x, y, width, height in boxes
    ]
    xs = {start[0], end[0]}
    ys = {start[1], end[1]}
    for x, y, width, height in expanded:
      xs.update((x, x + width))
      ys.update((y, y + height))
    x_values, y_values = sorted(xs), sorted(ys)
    edges: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}

    def available(point) -> bool:
      return point in {start, end} or not any(inside(point, box) for box in expanded)

    def connect(first, second) -> None:
      if not (available(first) and available(second)):
        return
      if any(_segment_hits_box(first, second, box) for box in expanded):
        return
      length = abs(first[0] - second[0]) + abs(first[1] - second[1])
      edges.setdefault(first, []).append((second, length))
      edges.setdefault(second, []).append((first, length))

    for y in y_values:
      row = [(x, y) for x in x_values if available((x, y))]
      for first, second in itertools.pairwise(row):
        connect(first, second)
    for x in x_values:
      column = [(x, y) for y in y_values if available((x, y))]
      for first, second in itertools.pairwise(column):
        connect(first, second)

    distances = {start: 0.0}
    previous: dict[tuple[float, float], tuple[float, float]] = {}
    pending = [(0.0, start)]
    while pending:
      distance, point = heapq.heappop(pending)
      if distance != distances.get(point):
        continue
      if point == end:
        path = [end]
        while path[-1] != start:
          path.append(previous[path[-1]])
        path.reverse()
        simplified = [path[0]]
        for index in range(1, len(path) - 1):
          before, current, after = path[index - 1 : index + 2]
          if not (
            before[0] == current[0] == after[0] or before[1] == current[1] == after[1]
          ):
            simplified.append(current)
        simplified.append(path[-1])
        return simplified
      for neighbor, cost in edges.get(point, []):
        candidate = distance + cost
        if candidate < distances.get(neighbor, math.inf):
          distances[neighbor] = candidate
          previous[neighbor] = point
          heapq.heappush(pending, (candidate, neighbor))
    return None

  for clearance in (8.0, 0.0):
    path = find_path(clearance)
    if path is not None:
      return path
  raise ValueError("no orthogonal route avoids the intervening board frames")


def _route_svg_path(start, end, route="orthogonal", obstacles=()) -> str:
  if route not in {"orthogonal", "scurve", "straight"}:
    raise ValueError(f"unsupported board arrow route: {route}")
  x1, y1 = (float(value) for value in start)
  x2, y2 = (float(value) for value in end)
  if route == "straight":
    return f"M {x1:g} {y1:g} L {x2:g} {y2:g}"
  if route == "scurve":
    bend = max(24.0, abs(x2 - x1) * 0.45, abs(y2 - y1) * 0.45)
    direction = 1 if x2 >= x1 else -1
    return (
      f"M {x1:g} {y1:g} C {x1 + direction * bend:g} {y1:g}, "
      f"{x2 - direction * bend:g} {y2:g}, {x2:g} {y2:g}"
    )
  points = _orthogonal_route_points((x1, y1), (x2, y2), obstacles)
  commands = [f"M {points[0][0]:g} {points[0][1]:g}"]
  for index in range(1, len(points) - 1):
    before, corner, after = points[index - 1], points[index], points[index + 1]
    in_len = math.hypot(corner[0] - before[0], corner[1] - before[1])
    out_len = math.hypot(after[0] - corner[0], after[1] - corner[1])
    radius = min(8.0, in_len / 2, out_len / 2)
    if radius <= 0:
      commands.append(f"L {corner[0]:g} {corner[1]:g}")
      continue
    in_point = (
      corner[0] + (before[0] - corner[0]) * radius / in_len,
      corner[1] + (before[1] - corner[1]) * radius / in_len,
    )
    out_point = (
      corner[0] + (after[0] - corner[0]) * radius / out_len,
      corner[1] + (after[1] - corner[1]) * radius / out_len,
    )
    commands.extend(
      (
        f"L {in_point[0]:g} {in_point[1]:g}",
        f"Q {corner[0]:g} {corner[1]:g} {out_point[0]:g} {out_point[1]:g}",
      )
    )
  commands.append(f"L {x2:g} {y2:g}")
  return " ".join(commands)


def annotate_image(
  input_path: Path,
  output_path: Path,
  boxes=(),
  labels=(),
  arrows=(),
  callouts=(),
  spec_path: Path | None = None,
  scale: int | None = None,
  project: Path | None = None,
  renderer: str = "auto",
  crop=None,
  executable: str | Path | None = None,
):
  src = Path(input_path).resolve()
  dst = Path(output_path).resolve()
  if not src.is_file():
    raise ValueError("annotate input PNG does not exist")
  if src == dst:
    raise ValueError("annotate output must not overwrite input in place")
  if dst.exists():
    raise ValueError("annotate output path already exists")
  spec = {}
  if spec_path is not None:
    spec_file = Path(spec_path)
    if spec_file.suffix.lower() != ".toml":
      raise ValueError("annotate spec must be TOML")
    loaded = tomllib.loads(spec_file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
      raise TypeError("annotate spec must be a TOML table")
    spec = loaded.get("annotate", loaded)
  cfg_info = load_nuu_config(project or src.parent)
  cfg_ann = (
    cfg_info["config"].get("annotate")
    if isinstance(cfg_info["config"].get("annotate"), dict)
    else {}
  )
  resolved_scale = int(
    scale
    if scale is not None
    else spec.get("scale", cfg_ann.get("scale", cfg_info["config"].get("scale", 2)))
  )
  if renderer == "auto" and isinstance(cfg_ann.get("renderer"), str):
    renderer = cfg_ann["renderer"]
  if not 1 <= resolved_scale <= 6:
    raise ValueError("annotate scale must be between 1 and 6")
  all_boxes = [parse_box(item) for item in [*(spec.get("boxes") or []), *(boxes or [])]]
  all_labels = [
    parse_label(item) for item in [*(spec.get("labels") or []), *(labels or [])]
  ]
  all_arrows = [
    parse_arrow(item) for item in [*(spec.get("arrows") or []), *(arrows or [])]
  ]
  all_callouts = [
    parse_callout(item) for item in [*(spec.get("callouts") or []), *(callouts or [])]
  ]
  raw_crop = crop if crop is not None else spec.get("crop")
  parsed_crop = parse_crop(raw_crop) if raw_crop is not None else None
  if not (all_boxes or all_labels or all_arrows or all_callouts or parsed_crop):
    raise ValueError("annotate requires at least one box, label, arrow, or callout")
  raw_bytes = src.read_bytes()
  width, height, rgba = decode_png(raw_bytes)
  all_callouts, resolved_crop = _resolve_annotations_and_crop(
    width,
    height,
    all_boxes,
    all_arrows,
    all_labels,
    all_callouts,
    parsed_crop,
    resolved_scale,
  )
  typography = resolve_project_typography(project or src.parent)
  board_tokens = _resolve_board_tokens(project or src.parent, theme="dark")
  token_css = _board_token_css(board_tokens)
  overlay_html = _overlay_svg_and_nodes(
    width, height, all_boxes, all_arrows, all_labels, all_callouts, resolved_scale
  )
  dst.parent.mkdir(parents=True, exist_ok=True)
  used_renderer = "bitmap"
  need_text = bool(all_labels or all_callouts)
  if renderer in {"auto", "chromium"} and (renderer == "chromium" or need_text):
    b64_img = base64.b64encode(raw_bytes).decode("ascii")
    html_doc = (
      "<!doctype html><html><head><meta charset='utf-8'><style>"
      f"{typography['font_faces_css']}\n"
      f":root {{ color-scheme: dark; {token_css}; "
      f"--nuu-font-sans: {typography['font_sans']}; --nuu-font-mono: {typography['font_mono']}; }}"
      "html, body { margin: 0; padding: 0; background: transparent; }"
      "</style></head><body>"
      f'<div id="nuu-render-root" style="position:relative;width:{width}px;height:{height}px;overflow:hidden;">'
      f'<img src="data:image/png;base64,{b64_img}" width="{width}" height="{height}" style="display:block;" />'
      f"{overlay_html}</div></body></html>"
    )
    if _chromium_render_png(html_doc, width, height, dst, executable=executable):
      used_renderer = "chromium"
      if resolved_crop is not None:
        cw_in, ch_in, c_rgba = decode_png(dst.read_bytes())
        width, height, c_rgba = crop_rgba(cw_in, ch_in, c_rgba, *resolved_crop)
        dst.write_bytes(encode_png(width, height, c_rgba))
  if used_renderer == "bitmap":
    for x, y, w, h, color in all_boxes:
      draw_box(rgba, width, height, x, y, w, h, color, thickness=max(2, resolved_scale))
    for x1, y1, x2, y2, color in all_arrows:
      draw_arrow(rgba, width, height, x1, y1, x2, y2, color)
    for x, y, text, color in all_labels:
      draw_label(rgba, width, height, x, y, text, color, scale=resolved_scale)
    for x, y, number, color, label in all_callouts:
      draw_callout(
        rgba, width, height, x, y, number, color, label, scale=resolved_scale
      )
    if resolved_crop is not None:
      width, height, rgba = crop_rgba(width, height, rgba, *resolved_crop)
    encoded = encode_png(width, height, rgba)
    with dst.open("xb") as stream:
      stream.write(encoded)
  return {
    "status": "annotated",
    "input": str(src),
    "output": str(dst),
    "width": width,
    "height": height,
    "crop": list(resolved_crop) if resolved_crop is not None else None,
    "scale": resolved_scale,
    "renderer": used_renderer,
    "font_family": typography["font_sans"],
    "annotations": {
      "boxes": len(all_boxes),
      "labels": len(all_labels),
      "arrows": len(all_arrows),
      "callouts": len(all_callouts),
    },
    "findings": [],
    "writes": [str(dst)],
  }


def _is_http_url(value: str) -> bool:
  return bool(re.match(r"^https?://", str(value).strip(), re.IGNORECASE))


def _capture_url_png(
  url: str,
  *,
  viewport: dict | None = None,
  is_mobile: bool = False,
  has_touch: bool = False,
  executable: str | None = None,
) -> bytes:
  if not _is_http_url(url):
    raise ValueError(f"board URL must use http or https: {url}")
  from playwright.sync_api import sync_playwright

  size = viewport or {"width": 1280, "height": 800}
  with sync_playwright() as playwright:
    browser = playwright.chromium.launch(**chromium_launch_kwargs(executable))
    try:
      context = browser.new_context(
        viewport={"width": int(size["width"]), "height": int(size["height"])},
        color_scheme="dark",
        is_mobile=bool(is_mobile),
        has_touch=bool(has_touch),
      )
      page = context.new_page()
      page.goto(url, wait_until="domcontentloaded")
      settle_page(page)
      return page.screenshot(full_page=True)
    finally:
      browser.close()


def _parse_panel_entry(entry, base_dir: Path) -> dict:
  if isinstance(entry, dict):
    raw_source = entry.get("url", entry.get("path"))
    if not raw_source:
      raise ValueError("panel requires a PNG path or http(s) URL")
    crop = entry.get("crop")
    if isinstance(crop, str):
      crop = [int(value.strip()) for value in crop.split(",")]
    source = str(raw_source).strip()
    is_url = _is_http_url(source)
    raw_path = Path(source) if not is_url else None
    resolved_path = (
      (base_dir / raw_path).resolve()
      if raw_path is not None and not raw_path.is_absolute()
      else raw_path
    )
    default_label = source.rstrip("/").rsplit("/", 1)[-1] or source
    return {
      "label": str(entry.get("label") or default_label),
      "path": resolved_path,
      "url": source if is_url else None,
      "crop": tuple(int(value) for value in crop) if crop else None,
      "boxes": [parse_box(box) for box in (entry.get("boxes") or [])],
      "arrows": [parse_arrow(arrow) for arrow in (entry.get("arrows") or [])],
      "labels": [parse_label(label) for label in (entry.get("labels") or [])],
      "callouts": [parse_callout(callout) for callout in (entry.get("callouts") or [])],
    }
  text = str(entry).strip()
  if "=" in text:
    label, rest = text.split("=", 1)
    label = label.strip()
  else:
    label, rest = "", text
  crop = None
  if "@" in rest:
    source, crop_part = rest.rsplit("@", 1)
    coords = [int(value.strip()) for value in crop_part.split(",")]
    if len(coords) != 4:
      raise ValueError("panel crop requires @x,y,width,height")
    crop = tuple(coords)
  else:
    source = rest
  source = source.strip()
  is_url = _is_http_url(source)
  raw_path = Path(source) if not is_url else None
  resolved_path = (
    (base_dir / raw_path).resolve()
    if raw_path is not None and not raw_path.is_absolute()
    else raw_path
  )
  default_label = source.rstrip("/").rsplit("/", 1)[-1] or source
  return {
    "label": label or default_label,
    "path": resolved_path,
    "url": source if is_url else None,
    "crop": crop,
    "boxes": [],
    "arrows": [],
    "labels": [],
    "callouts": [],
  }


def build_board(
  output_path: Path,
  title: str | None = None,
  subtitle: str | None = None,
  panels=(),
  callouts=(),
  spec_path: Path | None = None,
  columns: int | None = None,
  scale: int | None = None,
  project: Path | None = None,
  renderer: str = "auto",
  wireframe: bool = False,
  executable: str | None = None,
):
  dst = Path(output_path).resolve()
  if dst.exists():
    raise ValueError("board output path already exists")
  spec = {}
  base_dir = Path(project).resolve() if project else Path.cwd()
  if spec_path is not None:
    spec_file = Path(spec_path).resolve()
    base_dir = spec_file.parent
    if spec_file.suffix.lower() != ".toml":
      raise ValueError("board spec must be TOML")
    loaded = tomllib.loads(spec_file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
      raise TypeError("board spec must be a TOML table")
    spec = loaded.get("board", loaded)
  use_wireframe = bool(wireframe or spec.get("wireframe"))
  wf_dst = dst.with_name(f"{dst.stem}-wireframe{dst.suffix}") if use_wireframe else None
  if wf_dst is not None and wf_dst.exists():
    raise ValueError("board wireframe output path already exists")
  board_title = title if title is not None else spec.get("title")
  board_subtitle = subtitle if subtitle is not None else spec.get("subtitle")
  resolved_scale = int(scale if scale is not None else spec.get("scale", 2))
  raw_panels = [*(spec.get("panels") or []), *(panels or [])]
  if not raw_panels:
    raise ValueError("board requires at least one panel")
  parsed_panels = [_parse_panel_entry(item, base_dir) for item in raw_panels]
  legend_items = [
    parse_legend_callout(item)
    for item in [*(spec.get("callouts") or []), *(callouts or [])]
  ]
  cfg_info = load_nuu_config(project or base_dir)
  default_viewport = cfg_info["config"]["audit"]["viewports"][0]
  board_tokens = _resolve_board_tokens(project or base_dir, theme="dark")
  for panel in parsed_panels:
    if panel["url"]:
      continue
    panel_path = panel["path"]
    if panel_path is None or not panel_path.is_file():
      raise ValueError(f"board panel PNG does not exist: {panel_path}")
    if panel_path.resolve() == dst:
      raise ValueError("board output must not overwrite a panel input in place")

  prepared_panels = []
  for idx, panel in enumerate(parsed_panels, 1):
    if panel["url"]:
      raw_bytes = _capture_url_png(
        panel["url"],
        viewport=default_viewport,
        is_mobile=bool(default_viewport.get("mobile")),
        has_touch=bool(default_viewport.get("touch")),
        executable=executable,
      )
    else:
      raw_bytes = panel["path"].read_bytes()
    w, h, rgba = decode_png(raw_bytes)
    if panel["crop"]:
      cx, cy, cw, ch = panel["crop"]
      w, h, rgba = crop_rgba(w, h, rgba, cx, cy, cw, ch)
      png_bytes = encode_png(w, h, rgba)
    else:
      png_bytes = raw_bytes
    prepared_panels.append(
      {
        **panel,
        "index": idx,
        "width": w,
        "height": h,
        "rgba": rgba,
        "png_bytes": png_bytes,
      }
    )

  cols = int(
    columns
    if columns is not None
    else spec.get("columns") or (1 if len(prepared_panels) == 1 else 2)
  )
  cols = max(1, min(cols, len(prepared_panels)))
  typography = resolve_project_typography(project or base_dir)
  token_css = _board_token_css(board_tokens)
  shell_bg = _token_rgba(board_tokens, "--bg")
  surface = _token_rgba(board_tokens, "--surface")
  line = _token_rgba(board_tokens, "--line")
  text_color = _token_rgba(board_tokens, "--text")
  _token_rgba(board_tokens, "--muted")
  dst.parent.mkdir(parents=True, exist_ok=True)

  pad = 24
  gap = 16
  header_h = 44 if board_title else 0
  footer_h = 44 if legend_items else 0
  panel_header_h = 34
  panel_pad = 12
  rows = math.ceil(len(prepared_panels) / cols)
  col_widths = [0] * cols
  row_heights = [0] * rows
  for i, p in enumerate(prepared_panels):
    r, c = divmod(i, cols)
    col_widths[c] = max(col_widths[c], p["width"] + panel_pad * 2)
    row_heights[r] = max(row_heights[r], p["height"] + panel_header_h + panel_pad * 2)
  total_w = pad * 2 + sum(col_widths) + gap * max(0, cols - 1)
  total_h = (
    pad * 2
    + header_h
    + (12 if header_h else 0)
    + sum(row_heights)
    + gap * max(0, rows - 1)
    + (12 if footer_h else 0)
    + footer_h
  )

  panel_cards_html: list[str] = []
  for p in prepared_panels:
    b64 = base64.b64encode(p["png_bytes"]).decode("ascii")
    overlay = _overlay_svg_and_nodes(
      p["width"],
      p["height"],
      p["boxes"],
      p["arrows"],
      p["labels"],
      p["callouts"],
      resolved_scale,
    )
    panel_cards_html.append(
      '<div style="background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);'
      'padding:12px;display:flex;flex-direction:column;gap:8px;box-sizing:border-box;">'
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">'
      f'<span style="font-size:13px;font-weight:600;color:var(--text);letter-spacing:0.01em;">'
      f"{p['index']}. {html.escape(p['label'])}</span>"
      f'<span style="font-family:var(--nuu-font-mono);font-size:11px;color:var(--muted);">'
      f"{p['width']}×{p['height']} px</span></div>"
      f'<div style="position:relative;width:{p["width"]}px;height:{p["height"]}px;'
      'border-radius:var(--radius-md);overflow:hidden;border:1px solid var(--line);background:var(--bg);">'
      f'<img src="data:image/png;base64,{b64}" width="{p["width"]}" height="{p["height"]}" style="display:block;" />'
      f"{overlay}</div></div>"
    )

  header_html = ""
  if board_title:
    n_panels = len(prepared_panels)
    esc_title = html.escape(str(board_title))
    sub_html = (
      f'<span style="font-size:12px;color:var(--muted);font-weight:400;">{html.escape(str(board_subtitle))}</span>'
      if board_subtitle
      else f'<span style="font-family:var(--nuu-font-mono);font-size:11px;color:var(--muted);">{n_panels} panels</span>'
    )
    header_html = (
      '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:16px;'
      'padding-bottom:10px;border-bottom:1px solid var(--line);">'
      f'<div style="font-size:18px;font-weight:700;color:var(--text);letter-spacing:-0.01em;">{esc_title}</div>'
      f"{sub_html}</div>"
    )

  legend_html = ""
  if legend_items:
    pills = []
    for num, lbl, col in legend_items:
      hex_c = css_hex(col)
      pills.append(
        '<div style="display:inline-flex;align-items:center;gap:8px;background:var(--surface);'
        'border:1px solid var(--line);border-radius:var(--radius-pill);padding:4px 12px 4px 6px;">'
        f'<span style="width:20px;height:20px;border-radius:var(--radius-pill);'
        f"background:{hex_c};color:var(--on-accent);"
        'display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;">'
        f"{html.escape(num)}</span>"
        f'<span style="font-size:12px;font-weight:500;color:var(--text);">{html.escape(lbl)}</span></div>'
      )
    legend_html = (
      '<div style="display:flex;flex-wrap:wrap;gap:10px;padding-top:10px;border-top:1px solid var(--line);">'
      + "".join(pills)
      + "</div>"
    )

  html_doc = (
    "<!doctype html><html><head><meta charset='utf-8'><style>"
    f"{typography['font_faces_css']}\n"
    f":root {{ color-scheme: dark; {token_css}; "
    f"--nuu-font-sans: {typography['font_sans']}; --nuu-font-mono: {typography['font_mono']}; }}"
    "html, body { margin: 0; padding: 0; background: var(--bg); font-family: var(--nuu-font-sans); }"
    "</style></head><body>"
    '<div id="nuu-render-root" style="display:inline-flex;flex-direction:column;gap:14px;'
    "padding:20px;background:var(--bg);border:1px solid var(--line);"
    'border-radius:var(--radius-lg);box-sizing:border-box;">'
    f"{header_html}"
    f'<div style="display:grid;grid-template-columns:repeat({cols}, max-content);gap:16px;align-items:start;">'
    + "".join(panel_cards_html)
    + "</div>"
    f"{legend_html}</div></body></html>"
  )

  used_renderer = "bitmap"
  if renderer in {"auto", "chromium"} and _chromium_render_png(
    html_doc, total_w + 64, total_h + 64, dst, executable=executable
  ):
    used_renderer = "chromium"
    out_w, out_h, _ = decode_png(dst.read_bytes())
  else:
    canvas = bytearray(shell_bg * (total_w * total_h))
    draw_box(canvas, total_w, total_h, 0, 0, total_w, total_h, line, thickness=2)
    y_cursor = pad
    if board_title:
      draw_text(
        canvas,
        total_w,
        total_h,
        pad,
        y_cursor + 8,
        str(board_title),
        text_color,
        scale=2,
      )
      y_cursor += header_h + 12
    for i, p in enumerate(prepared_panels):
      r, c = divmod(i, cols)
      px = pad + sum(col_widths[:c]) + gap * c
      py = y_cursor + sum(row_heights[:r]) + gap * r
      pw = p["width"] + panel_pad * 2
      ph = p["height"] + panel_header_h + panel_pad * 2
      fill_rect(canvas, total_w, total_h, px, py, pw, ph, surface)
      draw_box(canvas, total_w, total_h, px, py, pw, ph, line, thickness=1)
      draw_text(
        canvas,
        total_w,
        total_h,
        px + panel_pad,
        py + 10,
        f"{p['index']}. {p['label']}",
        text_color,
        scale=2,
      )
      p_rgba = bytearray(p["rgba"])
      for bx, by, bw, bh, bcol in p["boxes"]:
        draw_box(
          p_rgba,
          p["width"],
          p["height"],
          bx,
          by,
          bw,
          bh,
          bcol,
          thickness=max(2, resolved_scale),
        )
      for ax1, ay1, ax2, ay2, acol in p["arrows"]:
        draw_arrow(p_rgba, p["width"], p["height"], ax1, ay1, ax2, ay2, acol)
      for lx, ly, ltxt, lcol in p["labels"]:
        draw_label(
          p_rgba, p["width"], p["height"], lx, ly, ltxt, lcol, scale=resolved_scale
        )
      for cx, cy, cnum, ccol, clbl in p["callouts"]:
        draw_callout(
          p_rgba,
          p["width"],
          p["height"],
          cx,
          cy,
          cnum,
          ccol,
          clbl,
          scale=resolved_scale,
        )
      blit_rgba(
        canvas,
        total_w,
        total_h,
        p_rgba,
        p["width"],
        p["height"],
        px + panel_pad,
        py + panel_header_h,
      )
    if legend_items:
      ly = total_h - pad - footer_h + 16
      lx = pad
      for num, lbl, col in legend_items:
        draw_callout(canvas, total_w, total_h, lx + 12, ly, num, col, lbl, scale=1)
        lx += max(140, (len(lbl) + 6) * 8)
    with dst.open("xb") as stream:
      stream.write(encode_png(total_w, total_h, canvas))
    out_w, out_h = total_w, total_h

  writes = [str(dst)]
  if wf_dst is not None:
    wf_w, wf_h, wf_rgba = decode_png(dst.read_bytes())
    draw_box(wf_rgba, wf_w, wf_h, 0, 0, wf_w, wf_h, COLORS["blue"], thickness=2)
    draw_label(
      wf_rgba,
      wf_w,
      wf_h,
      4,
      4,
      f"board {wf_w}x{wf_h}",
      COLORS["blue"],
      scale=1,
    )
    y_base = pad + (header_h + 12 if board_title else 0)
    for i, p in enumerate(prepared_panels):
      r, c = divmod(i, cols)
      px = pad + sum(col_widths[:c]) + gap * c
      py = y_base + sum(row_heights[:r]) + gap * r
      pw = p["width"] + panel_pad * 2
      ph = p["height"] + panel_header_h + panel_pad * 2
      draw_box(
        wf_rgba,
        wf_w,
        wf_h,
        min(px, max(0, wf_w - 2)),
        min(py, max(0, wf_h - 2)),
        min(pw, max(1, wf_w - min(px, wf_w - 1))),
        min(ph, max(1, wf_h - min(py, wf_h - 1))),
        COLORS["amber"],
        thickness=2,
      )
      draw_label(
        wf_rgba,
        wf_w,
        wf_h,
        min(px + 4, max(0, wf_w - 40)),
        min(py + 2, max(0, wf_h - 12)),
        f"panel#{p['index']} {p['width']}x{p['height']}",
        COLORS["amber"],
        scale=1,
      )
    with wf_dst.open("xb") as stream:
      stream.write(encode_png(wf_w, wf_h, wf_rgba))
    writes.append(str(wf_dst))

  result_payload = {
    "status": "composed",
    "output": str(dst),
    "title": board_title,
    "panels": len(prepared_panels),
    "columns": cols,
    "width": out_w,
    "height": out_h,
    "renderer": used_renderer,
    "font_family": typography["font_sans"],
    "embedded_fonts": typography["embedded_fonts"],
    "findings": [],
    "writes": writes,
  }
  if wf_dst is not None:
    result_payload["wireframe_output"] = str(wf_dst)
  return result_payload


DESIGN_BOARD_CSS = """
.board *, .board *::before, .board *::after {
  animation: none !important; transition: none !important; }
.board { position: relative; padding: 32px; background: var(--bg); color: var(--text);
  font-family: var(--font-sans, var(--nuu-font-sans)); width: max-content;
  max-width: 1536px; box-sizing: border-box; isolation: isolate; }
.board.wide { width: 2856px; max-width: 2856px; }
.board h1 { font-size: var(--size-22, 22px); font-weight: 600; margin: 0 0 4px; }
.board .lede { color: var(--muted); font-size: var(--size-14, 14px); margin: 0 0 24px; max-width: 720px; }
.board .row { display: flex; gap: 24px; align-items: flex-start; flex-wrap: wrap; }
.board .col { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.board .frame { border: 1px solid var(--line); border-radius: var(--radius-lg);
  background: var(--surface); padding: 16px; }
.board .lbl { font-size: var(--size-14, 14px); font-weight: 600; margin: 0 0 2px; }
.board .sub, .board .cap { color: var(--muted); font-size: var(--size-12, 12px); }
.board .sub { margin: 0 0 10px; max-width: 320px; line-height: 1.45; }
.board .cap { margin-top: 8px; }
.board img { display: block; border: 1px solid var(--line); border-radius: var(--radius-md); }
.board table.sheet { border-collapse: collapse; font-size: var(--size-14, 14px); }
.board table.sheet td { padding: 6px 12px 6px 0; vertical-align: middle; }
.board .mono { font-family: var(--font-mono, var(--nuu-font-mono));
  font-size: var(--size-12, 12px); color: var(--muted); }
.board .swatch { width: 72px; height: 28px; border-radius: var(--radius-md); border: 1px solid var(--line); }
.board .sbar { height: 10px; background: var(--accent); border-radius: var(--radius-sm); }
.board .rbox { width: 72px; height: 48px; border: 1px solid var(--line); background: var(--surface-2); }
.board .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.board .verdict { display: flex; align-items: center; gap: 6px;
  font-size: var(--size-12, 12px); font-weight: 600; margin-bottom: 8px; }
.board .verdict.do { color: var(--ok); }
.board .verdict.dont { color: var(--danger); }
.board .off { display: flex; gap: 7px; align-items: center; }
.board .glyph { font-size: 15px; line-height: 1; }
.board .narrow { width: 328px; }
.board .phases { list-style: none; margin: 0; padding: 0; }
.board .surface-2 { background: var(--surface-2); }
.board .accent { background: var(--accent-fill); color: var(--on-accent); border-color: transparent; }
.board button { display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: 36px; padding: 8px 12px; border: 1px solid var(--line); border-radius: var(--radius-md);
  background: var(--surface-2); color: var(--text); font: inherit; font-size: var(--size-12, 12px); }
.board button:disabled { color: var(--muted); opacity: 0.65; }
.board .focus-ring { outline: 2px solid var(--focus); outline-offset: 2px; }
.board .pill { display: inline-flex; align-items: center; justify-content: center; min-height: 32px;
  padding: 4px 10px; border: 1px solid var(--line); border-radius: var(--radius-pill);
  background: var(--surface); color: var(--muted); }
.board .component-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.board .component-row input { min-height: 36px; width: 180px; padding: 6px 10px; border: 1px solid var(--line);
  border-radius: var(--radius-md); background: var(--bg); color: var(--text); font: inherit; }
.board .blk { min-height: 14px; margin: 8px 0; border-radius: var(--radius-sm); background: var(--surface-2); }
.board .blk.hi { min-height: 24px; background: var(--accent-fill); }
.board .flow-grid { position: relative; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: stretch; gap: 20px; }
.board .flow-grid > .frame { position: relative; z-index: 1; min-width: 0; }
.board .flow-arrow { position: absolute; z-index: 0; inset: calc(50% - 12px) 0 auto;
  width: 100%; height: 24px; overflow: visible; }
.board .flow-arrow > path { fill: none; stroke: var(--accent); stroke-width: 2; marker-end: url(#board-flow-arrow); }
.board .style-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: start; gap: 16px; }
.board .token-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; }
.board .token-chip { display: flex; align-items: center; gap: 8px; min-width: 0; }
.board .token-chip .swatch { flex: 0 0 36px; width: 36px; height: 20px; }
.board .type-row { padding: 6px 0; border-bottom: 1px solid var(--line); }
.board .icon-grid { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.board .icon-cell { display: flex; align-items: center; gap: 6px; padding: 6px; border: 1px solid var(--line);
  border-radius: var(--radius-md); background: var(--surface-2); }
.board .anatomy-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.board .anatomy-sample { min-height: 220px; }
.board .redline { height: 2px; margin: 24px 0 12px; background: var(--redline-ink); }
.board .pin-row, .board .breakpoint-tags { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.board .callout-pin { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 24px;
  width: 24px; height: 24px; border: 2px solid var(--on-accent); border-radius: var(--radius-pill);
  background: var(--accent-fill); color: var(--on-accent); font-size: var(--size-12, 12px); font-weight: 700; }
.board .responsive-strip { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(180px, 1fr);
  gap: 12px; width: 100%; max-width: 100%; overflow-x: auto; align-items: start; }
.board .viewport-card { min-width: 0; max-width: 320px; }
.board .viewport-card .sub { min-height: 2.9em; }
.board .viewport-screen { position: relative; width: 100%; overflow: hidden; border: 1px solid var(--line);
  border-radius: var(--radius-md); background: var(--surface-2); }
.board .viewport-screen > img { width: 100%; height: 100%; object-fit: cover; object-position: top; }
.board .viewport-mock { display: grid; gap: 8px; padding: 12px; }
.board .focus-flow { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.board .focus-item { display: grid; grid-template-columns: auto 1fr; gap: 8px 12px; align-items: center; }
.board .focus-item .sub { grid-column: 1 / -1; }
.board .focus-target { grid-column: 1 / -1; min-width: 44px; min-height: 44px; }
.board .state-hover { border-color: var(--accent); background: var(--surface-2); }
.board .state-active { background: var(--accent-fill); color: var(--on-accent); }
.board .loading-mark { width: 16px; height: 16px; border: 2px solid var(--line); border-top-color: var(--accent);
  border-radius: var(--radius-pill); }
.board .arrow-layer { position: absolute; inset: 0; z-index: 0; width: 100%; height: 100%;
  overflow: visible; pointer-events: none; }
.board .arrow-layer > g > path, .board .arrow-layer > path {
  fill: none; stroke: var(--accent); stroke-width: 2; }
.board .arrow-layer text { fill: var(--text); font: 12px var(--font-sans, var(--nuu-font-sans)); }
.board > .row, .board > .flow-grid, .board > .style-grid, .board > .anatomy-grid,
.board > .responsive-strip { position: relative; z-index: 1; }
.board .col[data-board-frame] { position: relative; z-index: 1; }
.board .canvas-artboard { position: absolute; z-index: 1; box-sizing: border-box; overflow: hidden;
  border: 1px solid var(--line); border-radius: var(--radius-lg); background: var(--surface); padding: 16px; }
.wf .scr { width: 300px; border: 1px solid var(--line); border-radius: var(--radius-lg);
  background: var(--surface); padding: 10px; display: flex; flex-direction: column; gap: 8px; }
.wf .scr.wide { width: 420px; }
.wf .blk { background: var(--surface-2); border-radius: var(--radius-md); min-height: 12px; }
.wf .blk.hi { background: var(--accent); }
.wf .blk.soft { background: var(--surface-2); border: 1px dashed var(--line); }
.wf .blk.lbl { display: flex; align-items: center; justify-content: center; font-size: var(--size-12, 12px);
  color: var(--muted); padding: 6px 8px; }
.wf .blk.lbl.hi { color: var(--on-accent); }
.wf .bar { display: flex; gap: 6px; align-items: center; }
.wf .wbox { display: inline-flex; width: 12px; height: 12px; margin-right: 6px; align-items: center;
  justify-content: center; border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 10px; }
.wf .wbox.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.wf .down { transform: rotate(90deg); }
.wf .arrow { align-self: center; color: var(--muted); font-size: var(--size-12, 12px);
  display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 0 4px; width: 92px;
  text-align: center; }
.wf .arrow b { font-size: 18px; color: var(--muted); }
"""


def _extract_icon_script(icon_path: Path, table_name: str) -> str | None:
  try:
    text = icon_path.read_text(encoding="utf-8")
  except OSError:
    return None
  fill_loop = (
    'for (const slot of document.querySelectorAll("[data-icon]")) '
    f'slot.innerHTML = ({table_name})[slot.dataset.icon] || "";'
  )
  if icon_path.suffix.lower() == ".json":
    return None
  m_with_svg = re.search(
    rf"(const\s+svg\s*=.*?\n\s*(?:const|let|var)\s+{re.escape(table_name)}\s*=\s*\{{.*?\n\s*\}};)",
    text,
    flags=re.DOTALL,
  )
  if m_with_svg:
    snippet = m_with_svg.group(1)
  else:
    m_table = re.search(
      rf"((?:const|let|var)\s+{re.escape(table_name)}\s*=\s*\{{.*?\}};)",
      text,
      flags=re.DOTALL,
    )
    if not m_table:
      return None
    m_svg = re.search(r"(const\s+svg\s*=\s*[^\n;]+;)", text)
    snippet = f"{m_svg.group(1)}\n{m_table.group(1)}" if m_svg else m_table.group(1)
  return f"{snippet}\n{fill_loop}"


BOARD_PRESET_ALIASES = {
  "0": "panels",
  "1": "lay-of-the-land",
  "2": "deep-dive",
  "3": "example-views",
  "4": "component-states",
  "5": "global-components",
  "6": "style-guide",
  "7": "anatomy",
  "8": "responsive",
  "9": "a11y-focus",
  "10": "panels",
  "canvas": "custom",
  "custom": "custom",
  "lay-of-the-land": "lay-of-the-land",
  "deep-dive": "deep-dive",
  "example-views": "example-views",
  "component-states": "component-states",
  "global-components": "global-components",
  "style-guide": "style-guide",
  "anatomy": "anatomy",
  "responsive": "responsive",
  "a11y-focus": "a11y-focus",
  "panels": "panels",
}


def _canonical_board_type(value) -> str:
  key = str(value or "panels").strip().lower()
  try:
    return BOARD_PRESET_ALIASES[key]
  except KeyError as error:
    raise ValueError(f"unsupported board preset: {value}") from error


def _board_card(label: str, detail: str, body: str = "") -> str:
  return (
    f'<section class="frame"><p class="lbl">{html.escape(label)}</p>'
    f'<p class="sub">{html.escape(detail)}</p>{body}</section>'
  )


def _preset_board_markup(
  preset: str,
  tokens: dict[str, str],
  light_tokens: dict[str, str],
  stylesheet_css: str,
  viewports: list[dict],
  breakpoints: list[str] | None = None,
  redline: str = "",
  responsive_shot_name: str | None = None,
  loaded_shots: dict | None = None,
) -> tuple[str, list[tuple[str, str]]]:
  from measure import contrast_ratio, named_sizes, parse_sheet, select_redline_ink

  presets: dict[str, list[tuple[str, str]]] = {
    "lay-of-the-land": [
      ("Header", "Brand, search, and account controls"),
      ("Navigation", "Product areas and current location"),
      ("Main content", "Primary task and supporting information"),
      ("Actions", "One clear next step and safe secondary actions"),
    ],
    "deep-dive": [
      ("Section header", "Title, scope, and current status"),
      ("Content slots", "Summary, details, and supporting evidence"),
      ("Primary action", "A concrete action with the safest next step"),
      ("Secondary action", "A reversible alternative kept visually quiet"),
    ],
    "example-views": [
      ("Default", "A complete product view with realistic domain copy"),
      ("Empty", "A useful first action when no items exist"),
      ("Error", "A recoverable failure with a clear next step"),
      ("Populated", "Representative content with hierarchy and actions"),
    ],
    "component-states": [
      ("Default", "Resting control with stable dimensions"),
      ("Hover", "Pointer feedback without layout movement"),
      (":focus-visible", "Keyboard focus with a visible two-pixel ring"),
      ("Active", "Pressed state with unchanged control geometry"),
      ("Disabled", "Unavailable state with its reason retained"),
      ("Loading", "Progress feedback without shifting the action"),
    ],
    "global-components": [
      ("Dark scheme", "Holistic buttons, fields, cards, and status chips"),
      ("Light scheme", "The same component inventory in light mode"),
      ("In current use", "Active variants used by the product views"),
    ],
    "style-guide": [
      ("Role colors", "Semantic stylesheet tokens for both schemes"),
      ("Type ramp", "Nine-step stylesheet ramp and family tokens"),
      ("Spacing and radii", "Project spacing and radius tokens"),
      ("Icons", "Consistent icon grid on a shared baseline"),
    ],
    "anatomy": [
      ("1 · Container", "Surface, inset, and outer radius"),
      ("2 · Content", "Aligned title, supporting text, and metadata"),
      ("3 · Control", "Named action with a visible focus target"),
      ("4 · Redline", "Contrast-checked spacing and measurement ink"),
    ],
    "responsive": [
      (
        f"{int(vp['width'])}x{int(vp['height'])}",
        f"mobile={bool(vp.get('mobile'))}, touch={bool(vp.get('touch'))}",
      )
      for vp in viewports
    ],
    "a11y-focus": [
      ("1 · Search", "44 by 44 pixel touch target and visible focus"),
      ("2 · Filter", "Keyboard order follows the visual order"),
      ("3 · Result", "Focus remains visible against both surfaces"),
      ("4 · Save", "Contrast and focus remain clear during action"),
    ],
  }
  rows = presets.get(preset, [("Panels", "Before and after evidence")])
  style_tokens = parse_sheet(stylesheet_css)
  if preset == "lay-of-the-land":
    cards = "".join(
      _board_card(label, detail, '<div class="blk hi"></div>') for label, detail in rows
    )
    markup = (
      '<div class="flow-grid">'
      + cards
      + '<svg class="flow-arrow" viewBox="0 0 1000 24" aria-hidden="true">'
      '<defs><marker id="board-flow-arrow" markerWidth="10" markerHeight="10" '
      'refX="8" refY="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" '
      'fill="var(--accent)"/></marker></defs>'
      '<path d="M 10 12 C 250 12, 250 12, 490 12 S 730 12, 990 12" '
      'fill="none" stroke="var(--accent)" stroke-width="2" marker-end="url(#board-flow-arrow)"/>'
      "</svg></div>"
    )
  elif preset == "global-components":
    dark_html = _board_card(
      "Dark · holistic",
      rows[0][1],
      '<div class="component-row"><button class="accent">Create project</button>'
      '<button class="pill">Cancel</button><input aria-label="Workspace name" value="Northstar">'
      '<span class="pill">Active</span></div>',
    )
    light_style = html.escape(_board_token_css(light_tokens), quote=True)
    light_html = (
      f'<section class="frame theme-light" style="{light_style}">'
      f'<p class="lbl">Light · holistic</p><p class="sub">{html.escape(rows[1][1])}</p>'
      '<div class="component-row"><button class="accent">Create project</button>'
      '<button class="pill">Cancel</button><input aria-label="Workspace name" value="Northstar">'
      '<span class="pill">Active</span></div></section>'
    )
    current_html = _board_card(
      "In current use",
      rows[2][1],
      '<div class="component-row"><button class="accent">Save changes</button>'
      '<span class="pill">Draft</span></div>',
    )
    markup = f'<div class="row">{dark_html}{light_html}{current_html}</div>'
  elif preset == "style-guide":
    color_rows = []
    for token in BOARD_COLOR_TOKENS:
      light_value = light_tokens.get(token, "")
      color_rows.append(
        '<div class="token-chip">'
        f'<span class="swatch" title="Dark {html.escape(token, quote=True)}" '
        f'style="background:var({token})"></span><span class="mono">Dark</span>'
        f'<span class="swatch" title="Light {html.escape(token, quote=True)}" '
        f'style="background:{html.escape(light_value, quote=True)}"></span>'
        f'<span class="mono">Light · {html.escape(token)}</span></div>'
      )
    ramp_sizes = named_sizes(style_tokens, ramp=True)
    type_sizes = []
    for name, value in style_tokens.items():
      match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)px", value.strip())
      if name.startswith("size-") and match and float(match.group(1)) in ramp_sizes:
        type_sizes.append((float(match.group(1)), name, value))
    type_rows = "".join(
      f'<div class="type-row" style="font-size:{html.escape(value)}">'
      f"{html.escape(name)} · Sample heading</div>"
      for _, name, value in sorted(type_sizes)
    )
    spacing_rows = "".join(
      f'<span class="pill mono">--{html.escape(name)} · {html.escape(value)}</span>'
      for name, value in style_tokens.items()
      if name.startswith(("space-", "radius-"))
    )
    icon_grid = "".join(
      '<span class="icon-cell"><svg viewBox="0 0 24 24" width="20" height="20" '
      'fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5v14"/></svg>'
      f'<span class="mono">{size}px</span></span>'
      for size in (16, 20, 24)
    )
    markup = (
      '<div class="style-grid"><section class="frame"><p class="lbl">Role colors</p>'
      f'<div class="token-grid">{"".join(color_rows)}</div></section>'
      f'<section class="frame"><p class="lbl">Type ramp · {len(type_sizes)} steps</p>{type_rows}</section>'
      f'<section class="frame"><p class="lbl">Spacing and radii</p><div class="component-row">{spacing_rows}</div>'
      f'<p class="lbl">Icons</p><div class="icon-grid">{icon_grid}</div></section></div>'
    )
  elif preset == "anatomy":
    surface = _token_rgba(tokens, "--surface-2")
    ink = select_redline_ink([surface[:3]], redline)
    ink_css = css_hex(ink["ink"])
    pins = "".join(
      f'<span class="callout-pin" style="--pin:{ink_css}">{number}</span>'
      for number in range(1, 5)
    )
    markup = (
      '<div class="anatomy-grid"><section class="frame anatomy-sample">'
      '<p class="lbl">Account details</p><p class="sub">Workspace and access controls</p>'
      '<button class="accent">Save changes</button><div class="redline" style="--redline-ink:'
      f'{ink_css}"></div><div class="pin-row">{pins}</div></section>'
      '<section class="frame"><p class="lbl">Slots and tokens</p>'
      '<table class="sheet"><tr><td>Container</td><td class="mono">--surface · --radius-lg</td></tr>'
      '<tr><td>Content</td><td class="mono">--text · --muted</td></tr>'
      '<tr><td>Control</td><td class="mono">--accent-fill · --focus</td></tr>'
      f'<tr><td>Redline</td><td class="mono">{html.escape(ink["hex"])} · {ink["min_contrast"]:.2f}:1</td></tr>'
      "</table></section></div>"
    )
  elif preset == "responsive":
    items = []
    captures = loaded_shots or {}
    for vp, (label, detail) in zip(viewports, rows, strict=False):
      is_mobile = bool(vp.get("mobile"))
      has_touch = bool(vp.get("touch"))
      shot_key = f"{responsive_shot_name}@{vp['width']}x{vp['height']}"
      shot = captures.get(shot_key)
      if shot is not None:
        shot_bytes = base64.b64encode(shot[0]).decode("ascii")
        screen = (
          f'<img class="responsive-shot" src="data:image/png;base64,{shot_bytes}" '
          f'alt="{html.escape(label)} at {vp["width"]} by {vp["height"]}">'
        )
      else:
        screen = '<div class="viewport-mock"><div class="blk hi"></div><div class="blk"></div></div>'
      items.append(
        f'<section class="frame viewport-card" data-mobile="{str(is_mobile).lower()}" '
        f'data-touch="{str(has_touch).lower()}">'
        f'<p class="lbl">{html.escape(label)}</p><p class="sub">{html.escape(detail)}</p>'
        f'<div class="viewport-screen" style="aspect-ratio:{int(vp["width"])} / {int(vp["height"])}">'
        f"{screen}</div></section>"
      )
    breakpoint_tags = "".join(
      f'<span class="pill mono">{html.escape(value)}</span>'
      for value in (breakpoints or [])
    )
    markup = (
      f'<div class="responsive-board" data-breakpoints="{html.escape(",".join(breakpoints or []), quote=True)}">'
      f'<div class="breakpoint-tags">{breakpoint_tags}</div>'
      f'<div class="responsive-strip">{"".join(items)}</div></div>'
    )
  elif preset == "a11y-focus":
    fg = _token_rgba(tokens, "--text")
    bg = _token_rgba(tokens, "--bg")
    surface = _token_rgba(tokens, "--surface")
    focus = _token_rgba(tokens, "--focus")
    text_ratio = min(
      contrast_ratio(fg[:3], background[:3]) for background in (bg, surface)
    )
    focus_ratio = min(
      contrast_ratio(focus[:3], background[:3]) for background in (bg, surface)
    )
    items = "".join(
      f'<section class="frame focus-item" data-tab-order="{index}">'
      f'<span class="callout-pin">{index}</span><p class="lbl">{html.escape(label)}</p>'
      f'<p class="sub">{html.escape(detail)}</p>'
      f'<button type="button" class="focus-target focus-ring{" accent" if index == 1 else ""}" '
      f'tabindex="0" aria-label="{html.escape(label, quote=True)}">Tab {index}</button></section>'
      for index, (label, detail) in enumerate(rows, 1)
    )
    markup = (
      f'<p class="lede">Tab trail · 44×44 px minimum · text {text_ratio:.2f}:1 · '
      f"focus {focus_ratio:.2f}:1 on both surfaces</p>"
      f'<div class="focus-flow">{items}</div>'
    )
  else:
    cards = []
    for label, detail in rows:
      body = '<div class="blk"></div><div class="blk hi"></div>'
      if preset == "component-states":
        state_markup = {
          "Default": '<button class="accent">Continue</button>',
          "Hover": '<button class="state-hover">Continue</button>',
          ":focus-visible": '<button class="accent focus-ring">Continue</button>',
          "Active": '<button class="state-active">Continue</button>',
          "Disabled": "<button disabled>Continue</button>",
          "Loading": '<button disabled aria-busy="true"><span class="loading-mark" '
          'aria-hidden="true"></span>Saving</button>',
        }
        body = state_markup[label]
      elif preset == "example-views":
        body = '<div class="blk"></div><span class="pill">View details</span>'
      elif preset == "deep-dive":
        body = (
          '<div class="blk"></div><div class="blk"></div><div class="component-row">'
        )
        body += '<button class="accent">Open request</button><button class="pill">Cancel</button></div>'
      cards.append(_board_card(label, detail, body))
    if preset == "lay-of-the-land":
      markup = f'<div class="flow-grid">{"".join(cards)}</div>'
    else:
      markup = f'<div class="row">{"".join(cards)}</div>'
  return markup, rows


def _bitmap_route_points(start, end, route: str, obstacles=()) -> list[tuple[int, int]]:
  route = str(route or "orthogonal").strip().lower()
  _route_svg_path(start, end, route, obstacles)
  if route == "straight":
    return [start, end]
  if route == "orthogonal":
    return _orthogonal_route_points(start, end, obstacles)
  dx, dy = end[0] - start[0], end[1] - start[1]
  bend = max(24, abs(dx) * 0.45, abs(dy) * 0.45)
  if abs(dx) >= abs(dy):
    direction = 1 if dx >= 0 else -1
    control_1 = (start[0] + direction * bend, start[1])
    control_2 = (end[0] - direction * bend, end[1])
  else:
    direction = 1 if dy >= 0 else -1
    control_1 = (start[0], start[1] + direction * bend)
    control_2 = (end[0], end[1] - direction * bend)
  points = []
  for step in range(25):
    t = step / 24
    inverse = 1 - t
    x = (
      inverse**3 * start[0]
      + 3 * inverse**2 * t * control_1[0]
      + 3 * inverse * t**2 * control_2[0]
      + t**3 * end[0]
    )
    y = (
      inverse**3 * start[1]
      + 3 * inverse**2 * t * control_1[1]
      + 3 * inverse * t**2 * control_2[1]
      + t**3 * end[1]
    )
    points.append((round(x), round(y)))
  return points


def _bitmap_arrow_label_point(points) -> tuple[int, int]:
  if len(points) < 2:
    return points[0] if points else (0, 0)
  first, second = max(
    itertools.pairwise(points),
    key=lambda segment: math.hypot(
      segment[1][0] - segment[0][0], segment[1][1] - segment[0][1]
    ),
  )
  x = round((first[0] + second[0]) / 2)
  y = round((first[1] + second[1]) / 2)
  if abs(second[0] - first[0]) >= abs(second[1] - first[1]):
    return x, y + 14
  return x + 10, y


def _board_arrow_overlay_markup(board: dict, frames: list[dict]) -> tuple[str, str]:
  arrows = board.get("arrows") or []
  if not arrows:
    return "", ""
  if not isinstance(arrows, list):
    raise TypeError("board arrows must be an array of TOML tables")
  frame_ids = {str(frame["id"]) for frame in frames}
  parts = ['<svg class="arrow-layer" aria-hidden="true">']
  for index, arrow in enumerate(arrows, 1):
    if not isinstance(arrow, dict):
      raise TypeError("board arrows must be TOML tables")
    source_id = str(arrow.get("from", ""))
    target_id = str(arrow.get("to", ""))
    if source_id not in frame_ids or target_id not in frame_ids:
      raise ValueError(
        f"board arrow references an unknown frame: {source_id} -> {target_id}"
      )
    route = str(arrow.get("route") or "orthogonal").strip().lower()
    _route_svg_path((0, 0), (1, 1), route)
    raw_color = arrow.get("color")
    color = css_hex(parse_color(raw_color)) if raw_color else "var(--accent)"
    marker_id = f"board-arrow-{index}"
    parts.append(
      f'<defs><marker id="{marker_id}" markerWidth="10" markerHeight="10" '
      'refX="8" refY="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" '
      f'fill="{html.escape(color, quote=True)}"/></marker></defs>'
      f'<g data-from="{html.escape(source_id, quote=True)}" '
      f'data-to="{html.escape(target_id, quote=True)}" data-route="{route}" '
      f'data-color="{html.escape(color, quote=True)}" data-marker="{marker_id}" '
      f'data-label="{html.escape(str(arrow.get("label", "")), quote=True)}">'
      f'<path marker-end="url(#{marker_id})"/><text></text></g>'
    )
  parts.append("</svg>")
  script = """
<script>
(() => {
  const root = document.querySelector(".board");
  const svg = root && root.querySelector(":scope > .arrow-layer");
  if (!svg) return;
  const frames = new Map([...root.querySelectorAll("[data-board-frame]")]
    .map(node => [node.dataset.boardFrame, node]));
  const keyOf = point => `${point.x}|${point.y}`;
  const inside = (point, rect) => point.x > rect.left && point.x < rect.right
    && point.y > rect.top && point.y < rect.bottom;
  const hits = (a, b, rect) => a.x === b.x
    ? rect.left < a.x && a.x < rect.right
      && Math.max(Math.min(a.y, b.y), rect.top) < Math.min(Math.max(a.y, b.y), rect.bottom)
    : a.y === b.y
      ? rect.top < a.y && a.y < rect.bottom
        && Math.max(Math.min(a.x, b.x), rect.left) < Math.min(Math.max(a.x, b.x), rect.right)
      : true;
  const roundedPath = points => {
    if (points.length < 2) return "";
    let d = `M ${points[0].x} ${points[0].y}`;
    for (let index = 1; index < points.length - 1; index += 1) {
      const before = points[index - 1], corner = points[index], after = points[index + 1];
      const inLength = Math.hypot(corner.x - before.x, corner.y - before.y);
      const outLength = Math.hypot(after.x - corner.x, after.y - corner.y);
      const radius = Math.min(8, inLength / 2, outLength / 2);
      const incoming = {x: corner.x + (before.x - corner.x) * radius / inLength,
        y: corner.y + (before.y - corner.y) * radius / inLength};
      const outgoing = {x: corner.x + (after.x - corner.x) * radius / outLength,
        y: corner.y + (after.y - corner.y) * radius / outLength};
      d += ` L ${incoming.x} ${incoming.y} Q ${corner.x} ${corner.y} ${outgoing.x} ${outgoing.y}`;
    }
    const last = points[points.length - 1];
    return `${d} L ${last.x} ${last.y}`;
  };
  const orthogonalPath = (start, end, obstacles) => {
    for (const clearance of [8, 0]) {
      const expanded = obstacles.map(rect => ({left: rect.left - clearance,
        right: rect.right + clearance, top: rect.top - clearance, bottom: rect.bottom + clearance}));
      const xs = new Set([start.x, end.x]), ys = new Set([start.y, end.y]);
      for (const rect of expanded) {
        xs.add(rect.left); xs.add(rect.right); ys.add(rect.top); ys.add(rect.bottom);
      }
      const xValues = [...xs].sort((a, b) => a - b), yValues = [...ys].sort((a, b) => a - b);
      const graph = new Map(), nodes = new Map([[keyOf(start), start], [keyOf(end), end]]);
      const available = point => (keyOf(point) === keyOf(start) || keyOf(point) === keyOf(end))
        || !expanded.some(rect => inside(point, rect));
      const connect = (a, b) => {
        if (!available(a) || !available(b) || expanded.some(rect => hits(a, b, rect))) return;
        const aKey = keyOf(a), bKey = keyOf(b);
        nodes.set(aKey, a); nodes.set(bKey, b);
        const cost = Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
        if (!graph.has(aKey)) graph.set(aKey, []);
        if (!graph.has(bKey)) graph.set(bKey, []);
        graph.get(aKey).push({key: bKey, point: b, cost});
        graph.get(bKey).push({key: aKey, point: a, cost});
      };
      for (const y of yValues) {
        const row = xValues.map(x => ({x, y})).filter(available);
        for (let index = 0; index < row.length - 1; index += 1) connect(row[index], row[index + 1]);
      }
      for (const x of xValues) {
        const column = yValues.map(y => ({x, y})).filter(available);
        for (let index = 0; index < column.length - 1; index += 1) connect(column[index], column[index + 1]);
      }
      const startKey = keyOf(start), endKey = keyOf(end);
      const distances = new Map([[startKey, 0]]), previous = new Map();
      const pending = [{key: startKey, score: 0}];
      while (pending.length) {
        pending.sort((a, b) => a.score - b.score);
        const current = pending.shift();
        if (current.score !== distances.get(current.key)) continue;
        if (current.key === endKey) {
          const path = [end];
          while (path[path.length - 1] && keyOf(path[path.length - 1]) !== startKey) {
            path.push(previous.get(keyOf(path[path.length - 1])));
          }
          path.reverse();
          return path.filter((point, index) => index === 0 || index === path.length - 1
            || !(path[index - 1].x === point.x && point.x === path[index + 1].x)
              && !(path[index - 1].y === point.y && point.y === path[index + 1].y));
        }
        for (const edge of graph.get(current.key) || []) {
          const score = current.score + edge.cost;
          if (score < (distances.get(edge.key) ?? Infinity)) {
            distances.set(edge.key, score); previous.set(edge.key, nodes.get(current.key));
            pending.push({key: edge.key, score});
          }
        }
      }
    }
    return [start, end];
  };
  const render = () => {
    const rootRect = root.getBoundingClientRect();
    const width = Math.ceil(rootRect.width), height = Math.ceil(rootRect.height);
    svg.setAttribute("width", width); svg.setAttribute("height", height);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    for (const group of svg.querySelectorAll("g[data-from]")) {
      const source = frames.get(group.dataset.from), target = frames.get(group.dataset.to);
      if (!source || !target) continue;
      const box = node => {
        const rect = node.getBoundingClientRect();
        return {left: rect.left - rootRect.left, right: rect.right - rootRect.left,
          top: rect.top - rootRect.top, bottom: rect.bottom - rootRect.top,
          cx: (rect.left + rect.right) / 2 - rootRect.left,
          cy: (rect.top + rect.bottom) / 2 - rootRect.top};
      };
      const a = box(source), b = box(target), dx = b.cx - a.cx, dy = b.cy - a.cy;
      let start, end;
      if (Math.abs(dx) >= Math.abs(dy)) {
        start = {x: dx >= 0 ? a.right : a.left, y: a.cy};
        end = {x: dx >= 0 ? b.left : b.right, y: b.cy};
      } else {
        start = {x: a.cx, y: dy >= 0 ? a.bottom : a.top};
        end = {x: b.cx, y: dy >= 0 ? b.top : b.bottom};
      }
      const obstacles = [...frames.entries()]
        .filter(([id]) => id !== group.dataset.from && id !== group.dataset.to)
        .map(([, node]) => box(node));
      const labelPosition = points => {
        let best = null;
        for (let index = 1; index < points.length; index += 1) {
          const first = points[index - 1], second = points[index];
          const length = Math.hypot(second.x - first.x, second.y - first.y);
          if (!best || length > best.length) best = {first, second, length};
        }
        if (!best) return {x: start.x, y: start.y};
        const x = (best.first.x + best.second.x) / 2;
        const y = (best.first.y + best.second.y) / 2;
        return Math.abs(best.second.x - best.first.x) >= Math.abs(best.second.y - best.first.y)
          ? {x, y: y + 14} : {x: x + 10, y};
      };
      let path, labelPoint;
      if (group.dataset.route === "straight") {
        path = `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
        labelPoint = labelPosition([start, end]);
      } else if (group.dataset.route === "scurve") {
        const bend = Math.max(24, Math.abs(end.x - start.x) * 0.45, Math.abs(end.y - start.y) * 0.45);
        const direction = end.x >= start.x ? 1 : -1;
        path = `M ${start.x} ${start.y} C ${start.x + direction * bend} ${start.y}, `
          + `${end.x - direction * bend} ${end.y}, ${end.x} ${end.y}`;
        labelPoint = {x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 + 14};
      } else {
        const points = orthogonalPath(start, end, obstacles);
        path = roundedPath(points);
        labelPoint = labelPosition(points);
      }
      const line = group.querySelector("path"), label = group.querySelector("text");
      line.setAttribute("d", path); line.setAttribute("stroke", group.dataset.color);
      label.textContent = group.dataset.label;
      label.setAttribute("x", labelPoint.x);
      label.setAttribute("y", labelPoint.y);
    }
  };
  if (document.fonts) document.fonts.ready.then(() => requestAnimationFrame(render));
  if (window.ResizeObserver) new ResizeObserver(render).observe(root);
  window.addEventListener("resize", render, {passive: true});
  render();
})();
</script>
"""
  return "".join(parts), script


BITMAP_DETAILED_PRESETS = frozenset(
  {
    "lay-of-the-land",
    "deep-dive",
    "example-views",
    "component-states",
    "global-components",
    "style-guide",
    "anatomy",
    "a11y-focus",
  }
)
BITMAP_PRESET_HEIGHTS = {
  "lay-of-the-land": 440,
  "deep-dive": 520,
  "example-views": 520,
  "component-states": 480,
  "global-components": 440,
  "style-guide": 680,
  "anatomy": 520,
  "a11y-focus": 480,
}


def _draw_bitmap_preset(
  canvas: bytearray,
  width: int,
  height: int,
  preset: str,
  rows: list[tuple[str, str]],
  tokens: dict[str, str],
  light_tokens: dict[str, str],
  stylesheet_css: str,
  redline: str = "",
) -> None:
  from measure import contrast_ratio, named_sizes, parse_sheet, select_redline_ink

  surface = _token_rgba(tokens, "--surface")
  surface_2 = _token_rgba(tokens, "--surface-2")
  line = _token_rgba(tokens, "--line")
  text = _token_rgba(tokens, "--text")
  muted = _token_rgba(tokens, "--muted")
  accent = _token_rgba(tokens, "--accent-fill")
  on_accent = _token_rgba(tokens, "--on-accent")
  focus = _token_rgba(tokens, "--focus")
  pad, gap, top = 32, 16, 112

  def card(x, y, card_w, card_h, background=surface, border=line):
    fill_rect(canvas, width, height, x, y, card_w, card_h, background)
    draw_box(canvas, width, height, x, y, card_w, card_h, border, thickness=1)

  def button(
    x,
    y,
    label,
    button_w=168,
    background=accent,
    foreground=on_accent,
    border=None,
    ring=False,
  ):
    button_h = 40
    fill_rect(canvas, width, height, x, y, button_w, button_h, background)
    draw_box(
      canvas, width, height, x, y, button_w, button_h, border or background, thickness=1
    )
    if ring:
      draw_box(
        canvas,
        width,
        height,
        x - 4,
        y - 4,
        button_w + 8,
        button_h + 8,
        focus,
        thickness=2,
      )
    draw_text(canvas, width, height, x + 12, y + 12, label, foreground)

  def arrow(source, target):
    start, end = _nearest_box_edges(source, target)
    points = _bitmap_route_points(start, end, "orthogonal")
    for first, second in itertools.pairwise(points):
      draw_line(canvas, width, height, *first, *second, accent)
    if len(points) > 1:
      draw_arrow(canvas, width, height, *points[-2], *points[-1], accent)

  if preset == "lay-of-the-land":
    card_w = (width - 2 * pad - 3 * gap) // 4
    boxes = []
    for index, (label, detail) in enumerate(rows):
      x, y = pad + index * (card_w + gap), top
      card(x, y, card_w, 188)
      draw_text(canvas, width, height, x + 14, y + 16, label, text, scale=2)
      draw_text(canvas, width, height, x + 14, y + 50, detail[:42], muted)
      fill_rect(canvas, width, height, x + 14, y + 92, card_w - 28, 18, surface_2)
      fill_rect(canvas, width, height, x + 14, y + 126, card_w - 70, 18, accent)
      boxes.append({"x": x, "y": y, "width": card_w, "height": 188})
    for source, target in itertools.pairwise(boxes):
      arrow(source, target)
    return

  if preset in {"deep-dive", "example-views"}:
    card_w = (width - 2 * pad - gap) // 2
    card_h = 176
    for index, (label, detail) in enumerate(rows):
      row, column = divmod(index, 2)
      x, y = pad + column * (card_w + gap), top + row * (card_h + gap)
      card(x, y, card_w, card_h)
      draw_text(canvas, width, height, x + 16, y + 14, label, text, scale=2)
      draw_text(canvas, width, height, x + 16, y + 46, detail[:78], muted)
      if preset == "deep-dive":
        fill_rect(canvas, width, height, x + 16, y + 78, card_w - 32, 16, surface_2)
        fill_rect(
          canvas, width, height, x + 16, y + 106, card_w * 2 // 3, 16, surface_2
        )
        button(x + 16, y + 132, "Open request", 150)
        if label == "Secondary action":
          button(x + 184, y + 132, "Cancel", 120, surface_2, text, line)
      elif label == "Default":
        fill_rect(canvas, width, height, x + 16, y + 80, card_w - 32, 34, surface_2)
        draw_text(
          canvas, width, height, x + 28, y + 92, "Request 482 · Submitted", text
        )
        button(x + 16, y + 126, "Open request", 150)
      elif label == "Empty":
        draw_callout(
          canvas, width, height, x + card_w // 2, y + 96, "+", accent, scale=1
        )
        draw_text(canvas, width, height, x + 18, y + 120, "No requests yet", text)
        button(x + card_w - 188, y + 126, "Create request", 172)
      elif label == "Error":
        fill_rect(canvas, width, height, x + 16, y + 82, card_w - 32, 32, surface_2)
        draw_text(
          canvas, width, height, x + 28, y + 92, "Unable to load · Try again", text
        )
        button(x + 16, y + 126, "Retry", 100, _token_rgba(tokens, "--danger"))
      else:
        fill_rect(canvas, width, height, x + 16, y + 78, card_w - 32, 18, surface_2)
        fill_rect(
          canvas, width, height, x + 16, y + 106, card_w * 2 // 3, 18, surface_2
        )
        button(x + 16, y + 126, "View details", 150)
    return

  if preset == "component-states":
    columns = 3
    card_w = (width - 2 * pad - 2 * gap) // columns
    card_h = 144
    for index, (label, detail) in enumerate(rows):
      row, column = divmod(index, columns)
      x, y = pad + column * (card_w + gap), top + row * (card_h + gap)
      card(x, y, card_w, card_h)
      draw_text(canvas, width, height, x + 14, y + 12, label, text, scale=2)
      draw_text(canvas, width, height, x + 14, y + 42, detail[:48], muted)
      bx, by = x + 16, y + 82
      if label == "Active":
        button(bx, by, "Continue")
      elif label == "Hover":
        button(bx, by, "Continue", background=surface_2, foreground=text, border=accent)
      elif label == ":focus-visible":
        button(
          bx,
          by,
          "Continue",
          background=surface_2,
          foreground=text,
          border=line,
          ring=True,
        )
      elif label == "Disabled":
        button(bx, by, "Continue", background=surface_2, foreground=muted, border=line)
      elif label == "Loading":
        button(bx, by, "Saving", background=surface_2, foreground=text, border=line)
        fill_rect(canvas, width, height, bx + 124, by + 14, 12, 12, accent)
      else:
        button(bx, by, "Continue", background=surface_2, foreground=text, border=line)
    return

  if preset == "global-components":
    card_w = (width - 2 * pad - 2 * gap) // 3
    for index, (label, detail) in enumerate(rows):
      x, y = pad + index * (card_w + gap), top
      theme = light_tokens if label.startswith("Light") else tokens
      card_bg = _token_rgba(theme, "--surface")
      card_line = _token_rgba(theme, "--line")
      card_text = _token_rgba(theme, "--text")
      card_muted = _token_rgba(theme, "--muted")
      card_surface_2 = _token_rgba(theme, "--surface-2")
      card_accent = _token_rgba(theme, "--accent-fill")
      card_on_accent = _token_rgba(theme, "--on-accent")
      card(x, y, card_w, 252, card_bg, card_line)
      draw_text(canvas, width, height, x + 16, y + 14, label, card_text, scale=2)
      draw_text(canvas, width, height, x + 16, y + 42, detail[:52], card_muted)
      button(x + 16, y + 80, "Create project", 164, card_accent, card_on_accent)
      button(x + 192, y + 80, "Cancel", 104, card_surface_2, card_text, card_line)
      fill_rect(
        canvas,
        width,
        height,
        x + 16,
        y + 140,
        card_w - 32,
        42,
        _token_rgba(theme, "--bg"),
      )
      draw_box(
        canvas, width, height, x + 16, y + 140, card_w - 32, 42, card_line, thickness=1
      )
      draw_text(canvas, width, height, x + 28, y + 154, "Workspace name", card_muted)
      fill_rect(canvas, width, height, x + 16, y + 202, 96, 28, card_surface_2)
      draw_text(canvas, width, height, x + 26, y + 210, "Active", card_text)
    return

  if preset == "style-guide":
    style_tokens = parse_sheet(stylesheet_css)
    type_ramp = []
    ramp_sizes = named_sizes(style_tokens, ramp=True)
    for name, value in style_tokens.items():
      match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)px", value.strip())
      if name.startswith("size-") and match and float(match.group(1)) in ramp_sizes:
        type_ramp.append((float(match.group(1)), name, value))
    type_ramp.sort()
    spacing = [
      (name, value)
      for name, value in sorted(style_tokens.items())
      if name.startswith(("space-", "radius-"))
    ]
    card_w = (width - 2 * pad - 2 * gap) // 3
    x_positions = [pad + index * (card_w + gap) for index in range(3)]
    for x in x_positions:
      card(x, top, card_w, 520)
    draw_text(
      canvas,
      width,
      height,
      x_positions[0] + 14,
      top + 14,
      "Role colors · dark / light",
      text,
      scale=2,
    )
    for index, token in enumerate(BOARD_COLOR_TOKENS):
      y = top + 52 + index * 27
      dark_color = _token_rgba(tokens, token)
      light_color = _token_rgba(light_tokens, token)
      fill_rect(canvas, width, height, x_positions[0] + 14, y, 22, 16, dark_color)
      draw_box(canvas, width, height, x_positions[0] + 14, y, 22, 16, line, thickness=1)
      fill_rect(canvas, width, height, x_positions[0] + 44, y, 22, 16, light_color)
      draw_box(
        canvas,
        width,
        height,
        x_positions[0] + 44,
        y,
        22,
        16,
        _token_rgba(light_tokens, "--line"),
        thickness=1,
      )
      draw_text(canvas, width, height, x_positions[0] + 76, y + 2, token, muted)
    draw_text(
      canvas,
      width,
      height,
      x_positions[1] + 14,
      top + 14,
      f"Type ramp · {len(type_ramp)} steps",
      text,
      scale=2,
    )
    for index, (size, name, value) in enumerate(type_ramp[:9]):
      y = top + 56 + index * 40
      draw_text(
        canvas, width, height, x_positions[1] + 14, y, f"{name} · {value}", text
      )
      fill_rect(
        canvas,
        width,
        height,
        x_positions[1] + 14,
        y + 20,
        min(card_w - 32, max(12, round(size * 5))),
        7,
        accent,
      )
    draw_text(
      canvas,
      width,
      height,
      x_positions[2] + 14,
      top + 14,
      "Spacing and radii",
      text,
      scale=2,
    )
    for index, (name, value) in enumerate(spacing):
      column, row = index % 2, index // 2
      x = x_positions[2] + 14 + column * (card_w // 2)
      y = top + 54 + row * 32
      draw_text(canvas, width, height, x, y, f"{name} · {value}", muted)
    icon_y = top + 360
    draw_text(
      canvas,
      width,
      height,
      x_positions[2] + 14,
      icon_y,
      "Icons · shared baseline",
      text,
      scale=2,
    )
    for index, size in enumerate((16, 20, 24)):
      x = x_positions[2] + 24 + index * 112
      y = icon_y + 36
      draw_line(canvas, width, height, x, y + size // 2, x + size, y + size // 2, text)
      draw_line(canvas, width, height, x + size // 2, y, x + size // 2, y + size, text)
      draw_text(canvas, width, height, x + size + 8, y + 4, f"{size}px", muted)
    return

  if preset == "anatomy":
    card_w = (width - 2 * pad - gap) // 2
    x1, x2 = pad, pad + card_w + gap
    card(x1, top, card_w, 344)
    card(x2, top, card_w, 344)
    draw_text(
      canvas, width, height, x1 + 16, top + 14, "Account details", text, scale=2
    )
    draw_text(
      canvas, width, height, x1 + 16, top + 46, "Workspace and access controls", muted
    )
    fill_rect(canvas, width, height, x1 + 28, top + 90, card_w - 56, 44, surface_2)
    draw_text(canvas, width, height, x1 + 40, top + 104, "Workspace name", muted)
    button(x1 + 28, top + 164, "Save changes", 168)
    contrast_ink = select_redline_ink([_token_rgba(tokens, "--surface-2")[:3]], redline)
    redline_color = contrast_ink["ink"]
    line_y = top + 270
    draw_arrow(
      canvas, width, height, x1 + 28, line_y, x1 + card_w - 28, line_y, redline_color
    )
    draw_arrow(
      canvas, width, height, x1 + card_w - 28, line_y, x1 + 28, line_y, redline_color
    )
    draw_text(
      canvas,
      width,
      height,
      x1 + 180,
      line_y + 10,
      f"Spacing · {contrast_ink['hex']} · {contrast_ink['min_contrast']:.2f}:1",
      text,
    )
    pins = (
      (x1 + 38, top + 82, "Container"),
      (x1 + card_w - 52, top + 132, "Content"),
      (x1 + 224, top + 178, "Control"),
      (x1 + 48, line_y, "Redline"),
    )
    for number, (x, y, label) in enumerate(pins, 1):
      draw_callout(canvas, width, height, x, y, number, accent, label, scale=1)
    draw_text(
      canvas, width, height, x2 + 16, top + 14, "Slots and tokens", text, scale=2
    )
    table_rows = (
      ("Container", "--surface · --radius-lg"),
      ("Content", "--text · --muted"),
      ("Control", "--accent-fill · --focus"),
      ("Redline", f"{contrast_ink['hex']} · {contrast_ink['min_contrast']:.2f}:1"),
    )
    for index, (label, detail) in enumerate(table_rows):
      y = top + 62 + index * 56
      draw_box(canvas, width, height, x2 + 16, y, card_w - 32, 42, line, thickness=1)
      draw_text(canvas, width, height, x2 + 28, y + 6, label, text)
      draw_text(canvas, width, height, x2 + 28, y + 24, detail, muted)
    return

  if preset == "a11y-focus":
    card_w = (width - 2 * pad - 3 * gap) // 4
    text_ratio = min(
      contrast_ratio(_token_rgba(tokens, "--text")[:3], _token_rgba(tokens, name)[:3])
      for name in ("--bg", "--surface")
    )
    focus_ratio = min(
      contrast_ratio(_token_rgba(tokens, "--focus")[:3], _token_rgba(tokens, name)[:3])
      for name in ("--bg", "--surface")
    )
    draw_text(
      canvas,
      width,
      height,
      pad,
      82,
      f"Tab order · 44x44 px targets · text {text_ratio:.2f}:1 · focus {focus_ratio:.2f}:1",
      muted,
    )
    for index, (label, detail) in enumerate(rows, 1):
      x, y = pad + (index - 1) * (card_w + gap), top
      card(x, y, card_w, 268)
      draw_callout(canvas, width, height, x + 34, y + 34, index, accent, scale=1)
      draw_text(canvas, width, height, x + 56, y + 26, label, text, scale=2)
      draw_text(canvas, width, height, x + 16, y + 66, detail[:42], muted)
      button(
        x + 18,
        y + 122,
        f"Tab {index}",
        132,
        accent if index == 1 else surface_2,
        on_accent if index == 1 else text,
        accent if index == 1 else line,
        ring=True,
      )
      draw_text(canvas, width, height, x + 18, y + 190, "Focus visible · 2 px", muted)


def _canvas_board_markup(
  board: dict, loaded_shots: dict, resolve_rel
) -> tuple[str, tuple[int, int], list[dict]]:
  frames = board.get("frames") or []
  if not isinstance(frames, list) or not frames:
    raise ValueError("canvas board requires at least one artboard frame")
  canvas_spec = board.get("canvas") if isinstance(board.get("canvas"), dict) else {}
  frame_map: dict[str, dict] = {}
  max_right = 0
  max_bottom = 0
  for index, frame in enumerate(frames, 1):
    if not isinstance(frame, dict):
      raise TypeError("canvas frames must be TOML tables")
    frame_id = str(frame.get("id") or f"artboard-{index}")
    if frame_id in frame_map:
      raise ValueError(f"duplicate canvas frame id: {frame_id}")
    x = int(frame.get("x", 0))
    y = int(frame.get("y", 0))
    width = int(frame.get("width", frame.get("w", 640)))
    height = int(frame.get("height", frame.get("h", 420)))
    if x < 0 or y < 0 or width <= 0 or height <= 0:
      raise ValueError(f"invalid canvas artboard geometry: {frame_id}")
    frame_map[frame_id] = {
      **frame,
      "id": frame_id,
      "x": x,
      "y": y,
      "width": width,
      "height": height,
    }
    max_right = max(max_right, x + width)
    max_bottom = max(max_bottom, y + height)
  canvas_width = int(canvas_spec.get("width", board.get("width", max_right + 64)))
  canvas_height = int(canvas_spec.get("height", board.get("height", max_bottom + 64)))
  if not 1 <= canvas_width <= 16384 or not 1 <= canvas_height <= 16384:
    raise ValueError("canvas dimensions must be between 1 and 16384 pixels")
  if max_right > canvas_width or max_bottom > canvas_height:
    raise ValueError("canvas artboard exceeds the declared canvas dimensions")

  artboards: list[dict] = []
  markup: list[str] = []
  for frame_id, frame in frame_map.items():
    title = html.escape(str(frame.get("label") or frame_id))
    subtitle = html.escape(str(frame.get("sub") or ""))
    shot_name = str(frame.get("shot") or "")
    if shot_name:
      if shot_name not in loaded_shots:
        raise ValueError(f"missing shot: {shot_name}")
      shot_bytes = base64.b64encode(loaded_shots[shot_name][0]).decode("ascii")
      body = (
        f'<img src="data:image/png;base64,{shot_bytes}" alt="{title}" '
        'style="display:block;width:100%;height:auto;">'
      )
    elif frame.get("html"):
      raw_html = str(frame["html"])
      fragment_path = resolve_rel(raw_html) if "<" not in raw_html else None
      if fragment_path is not None and fragment_path.is_file():
        body = fragment_path.read_text(encoding="utf-8")
      elif "<" in raw_html and ">" in raw_html:
        body = raw_html
      else:
        raise ValueError(f"missing HTML fragment: {raw_html}")
    else:
      body = '<div class="blk hi"></div><div class="blk"></div>'
    subtitle_html = f'<p class="sub">{subtitle}</p>' if subtitle else ""
    markup.append(
      f'<section class="canvas-artboard" id="artboard-{html.escape(frame_id, quote=True)}" '
      f'style="left:{frame["x"]}px;top:{frame["y"]}px;width:{frame["width"]}px;'
      f'height:{frame["height"]}px"><p class="lbl">{title}</p>'
      f"{subtitle_html}{body}</section>"
    )
    artboard = dict(frame)
    if shot_name:
      _, shot_width, shot_height, shot_rgba = loaded_shots[shot_name]
      artboard["_bitmap_image"] = (shot_width, shot_height, shot_rgba)
    artboards.append(artboard)

  svg_parts = [
    (
      f'<svg class="arrow-layer" width="{canvas_width}" height="{canvas_height}" '
      f'viewBox="0 0 {canvas_width} {canvas_height}" aria-label="Board flow arrows">'
    )
  ]
  arrows = board.get("arrows") or []
  if not isinstance(arrows, list):
    raise TypeError("canvas arrows must be an array of TOML tables")
  for index, arrow in enumerate(arrows, 1):
    if not isinstance(arrow, dict):
      raise TypeError("canvas arrows must be TOML tables")
    source_id = str(arrow.get("from", ""))
    target_id = str(arrow.get("to", ""))
    if source_id not in frame_map or target_id not in frame_map:
      raise ValueError(
        f"canvas arrow references an unknown frame: {source_id} -> {target_id}"
      )
    start, end = _nearest_box_edges(frame_map[source_id], frame_map[target_id])
    route = str(arrow.get("route") or "orthogonal").strip().lower()
    route_obstacles = [
      frame for key, frame in frame_map.items() if key not in {source_id, target_id}
    ]
    route_points = _bitmap_route_points(start, end, route, route_obstacles)
    path = _route_svg_path(start, end, route, route_obstacles)
    raw_color = arrow.get("color")
    color = css_hex(parse_color(raw_color)) if raw_color else "var(--accent)"
    marker_id = f"board-arrow-{index}"
    svg_parts.append(
      f'<defs><marker id="{marker_id}" markerWidth="10" markerHeight="10" '
      'refX="8" refY="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" '
      f'fill="{color}"/></marker></defs><path d="{path}" stroke="{color}" '
      f'marker-end="url(#{marker_id})"/>'
    )
    if arrow.get("label"):
      label = html.escape(str(arrow["label"]))
      label_x, label_y = _bitmap_arrow_label_point(route_points)
      svg_parts.append(f'<text x="{label_x:g}" y="{label_y:g}">{label}</text>')
  svg_parts.append("</svg>")
  content = (
    f'<div class="canvas" style="position:relative;width:{canvas_width}px;height:{canvas_height}px">'
    + "".join(svg_parts)
    + "".join(markup)
    + "</div>"
  )
  return content, (canvas_width, canvas_height), artboards


def build_design_boards(
  output_path: Path,
  spec_path: Path | None = None,
  project: Path | None = None,
  renderer: str = "auto",
  wireframe: bool = False,
  only=(),
  settle_ms: int = 80,
  spec_obj: dict | None = None,
  executable: str | None = None,
) -> dict:
  if spec_obj is not None:
    if not isinstance(spec_obj, dict):
      raise TypeError("board spec must be a table/dict")
    loaded = dict(spec_obj)
    spec_dir = Path(project).resolve() if project else Path.cwd().resolve()
  else:
    if spec_path is None:
      return {
        "status": "failed",
        "output": str(output_path),
        "boards": [],
        "findings": [
          {
            "id": "RUN102",
            "severity": "error",
            "message": "board requires --spec <boards.toml> or [[board.boards]]",
          }
        ],
        "writes": [],
      }
    spec_file = Path(spec_path).resolve()
    if not spec_file.is_file():
      return {
        "status": "failed",
        "output": str(output_path),
        "boards": [],
        "findings": [
          {
            "id": "RUN102",
            "severity": "error",
            "message": f"missing board spec: {spec_path}",
          }
        ],
        "writes": [],
      }
    if spec_file.suffix.lower() != ".toml":
      raise ValueError("board spec must be TOML")
    loaded = tomllib.loads(spec_file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
      raise TypeError("board spec must decode to a TOML table")
    if isinstance(loaded.get("board"), dict):
      loaded = {
        **{key: value for key, value in loaded.items() if key != "board"},
        **loaded["board"],
      }
    spec_dir = spec_file.parent
  project_root = Path(project).resolve() if project else spec_dir
  cfg_info = load_nuu_config(project_root)
  cfg_dir = cfg_info["dir"]
  cfg_board = (
    cfg_info["config"].get("board")
    if isinstance(cfg_info["config"].get("board"), dict)
    else {}
  )

  def resolve_rel(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
      return p
    if cfg_dir is not None:
      cand_cfg = (cfg_dir / p).resolve()
      if cand_cfg.exists():
        return cand_cfg
    cand_proj = (project_root / p).resolve()
    if cand_proj.exists():
      return cand_proj
    return (spec_dir / p).resolve()

  app_css = ""
  sheet_path = None
  sheet_rel = loaded.get("stylesheet")
  if not sheet_rel:
    sheet_rel = cfg_board.get("stylesheet") or cfg_info["config"].get("stylesheet")
  if sheet_rel:
    sheet_path = resolve_rel(str(sheet_rel))
    if not sheet_path.is_file():
      return {
        "status": "failed",
        "output": str(output_path),
        "boards": [],
        "findings": [
          {
            "id": "RUN102",
            "severity": "error",
            "message": f"missing stylesheet: {sheet_rel}",
          }
        ],
        "writes": [],
      }
    sheet_text = sheet_path.read_text(encoding="utf-8")
    if sheet_path.suffix.lower() == ".html":
      style_blocks = re.findall(
        r"<style[^>]*>(.*?)</style>", sheet_text, flags=re.IGNORECASE | re.DOTALL
      )
      if not style_blocks:
        return {
          "status": "failed",
          "output": str(output_path),
          "boards": [],
          "findings": [
            {
              "id": "RUN102",
              "severity": "error",
              "message": f"missing <style> block in stylesheet: {sheet_rel}",
            }
          ],
          "writes": [],
        }
      app_css = "\n".join(style_blocks)
    else:
      app_css = sheet_text

  token_sets = _resolve_board_token_sets(project_root, sheet_path)
  dark_tokens = token_sets["dark"]
  light_tokens = token_sets["light"]
  viewport_specs = cfg_info["config"]["audit"]["viewports"]
  breakpoint_specs = cfg_info["config"]["audit"].get("breakpoints", [])
  redline = cfg_info["config"].get("measure", {}).get("redline", "")
  icon_js = ""
  icons_spec = loaded.get("icons")
  if icons_spec:
    if isinstance(icons_spec, dict):
      icon_rel = icons_spec.get("file", "")
      table_name = icons_spec.get("table", "ICONS")
    else:
      icon_rel = str(icons_spec)
      table_name = "ICONS"
    icon_path = resolve_rel(str(icon_rel))
    if not icon_path.is_file():
      return {
        "status": "failed",
        "output": str(output_path),
        "boards": [],
        "findings": [
          {
            "id": "RUN102",
            "severity": "error",
            "message": f"missing icon file: {icon_rel}",
          }
        ],
        "writes": [],
      }
    extracted = _extract_icon_script(icon_path, str(table_name))
    if not extracted:
      return {
        "status": "failed",
        "output": str(output_path),
        "boards": [],
        "findings": [
          {
            "id": "RUN102",
            "severity": "error",
            "message": f"missing icon table {table_name} in {icon_rel}",
          }
        ],
        "writes": [],
      }
    icon_js = extracted

  shots_spec = loaded.get("shots") or {}
  boards_raw = loaded.get("boards") or []
  if not isinstance(boards_raw, list) or not boards_raw:
    return {
      "status": "failed",
      "output": str(output_path),
      "boards": [],
      "findings": [
        {
          "id": "RUN102",
          "severity": "error",
          "message": "design board spec requires a non-empty boards array",
        }
      ],
      "writes": [],
    }

  only_set = [str(item) for item in (only or ())]
  if only_set:
    known_names = {str(b.get("name")) for b in boards_raw if isinstance(b, dict)}
    missing_only = [name for name in only_set if name not in known_names]
    if missing_only:
      return {
        "status": "failed",
        "output": str(output_path),
        "boards": [],
        "findings": [
          {
            "id": "RUN102",
            "severity": "error",
            "message": f"unknown board in --only: {', '.join(missing_only)}",
          }
        ],
        "writes": [],
      }
    selected_boards = [b for b in boards_raw if str(b.get("name")) in only_set]
  else:
    selected_boards = list(boards_raw)

  shots_spec = dict(shots_spec)
  responsive_requested = any(
    _canonical_board_type(board.get("type") or cfg_board.get("type")) == "responsive"
    for board in selected_boards
    if isinstance(board, dict)
  )
  for board in selected_boards:
    if isinstance(board, dict) and board.get("url"):
      shot_name = str(board.get("shot") or f"responsive-{board.get('name', 'page')}")
      shots_spec.setdefault(shot_name, str(board["url"]))
      board["_responsive_shot"] = shot_name

  loaded_shots: dict[str, tuple[bytes, int, int, bytearray]] = {}
  responsive_shot_names: list[str] = []
  for shot_name, shot_val in shots_spec.items():
    raw_source = (
      shot_val.get("url", shot_val.get("path", ""))
      if isinstance(shot_val, dict)
      else str(shot_val)
    )
    source = str(raw_source).strip()
    if _is_http_url(source):
      responsive_shot_names.append(str(shot_name))
      shot_viewports = viewport_specs if responsive_requested else viewport_specs[:1]
      for viewport in shot_viewports:
        raw_bytes = _capture_url_png(
          source,
          viewport=viewport,
          is_mobile=bool(viewport.get("mobile")),
          has_touch=bool(viewport.get("touch")),
          executable=executable,
        )
        sw, sh, srgba = decode_png(raw_bytes)
        loaded_shots[f"{shot_name}@{viewport['width']}x{viewport['height']}"] = (
          raw_bytes,
          sw,
          sh,
          srgba,
        )
        loaded_shots.setdefault(str(shot_name), (raw_bytes, sw, sh, srgba))
      continue
    shot_path = resolve_rel(source)
    if not shot_path.is_file():
      return {
        "status": "failed",
        "output": str(output_path),
        "boards": [],
        "findings": [
          {
            "id": "RUN102",
            "severity": "error",
            "message": f"missing shot: {shot_name} ({source})",
          }
        ],
        "writes": [],
      }
    raw_bytes = shot_path.read_bytes()
    sw, sh, srgba = decode_png(raw_bytes)
    loaded_shots[str(shot_name)] = (raw_bytes, sw, sh, srgba)

  dst = Path(output_path).resolve()
  single_png = dst.suffix.lower() == ".png" and len(selected_boards) == 1
  out_dir = (
    dst.parent if single_png else (dst.parent if dst.suffix.lower() == ".png" else dst)
  )
  out_dir.mkdir(parents=True, exist_ok=True)
  typography = resolve_project_typography(project_root)
  board_token_css = _board_token_css(dark_tokens)
  shell_bg = _token_rgba(dark_tokens, "--bg")
  surface = _token_rgba(dark_tokens, "--surface")
  line = _token_rgba(dark_tokens, "--line")
  text_color = _token_rgba(dark_tokens, "--text")
  muted = _token_rgba(dark_tokens, "--muted")
  _token_rgba(light_tokens, "--bg")
  light_surface = _token_rgba(light_tokens, "--surface")
  light_line = _token_rgba(light_tokens, "--line")
  light_text = _token_rgba(light_tokens, "--text")
  light_muted = _token_rgba(light_tokens, "--muted")
  cfg_info = load_nuu_config(project_root)
  cfg_board = (
    cfg_info["config"].get("board")
    if isinstance(cfg_info["config"].get("board"), dict)
    else {}
  )
  use_wireframe = bool(
    wireframe
    or loaded.get("wireframe")
    or cfg_board.get("wireframe")
    or cfg_info["config"].get("wireframe")
  )
  if renderer == "auto" and isinstance(cfg_board.get("renderer"), str):
    renderer = cfg_board["renderer"]

  board_entries: list[dict] = []
  writes: list[str] = []
  used_renderer = "bitmap"

  for idx, board in enumerate(selected_boards, 1):
    b_name = str(board.get("name") or f"board-{idx}")
    b_dst = dst if single_png else (out_dir / f"{b_name}.png")
    if b_dst.exists():
      raise ValueError(f"board output path already exists: {b_dst}")
    b_title = board.get("title")
    b_lede = board.get("lede")
    preset = _canonical_board_type(
      board.get("type") or cfg_board.get("type") or "panels"
    )
    responsive_shot_name = board.get("_responsive_shot") or (
      responsive_shot_names[0] if responsive_shot_names else None
    )
    is_wide = bool(board.get("wide"))
    title_html = f"<h1>{html.escape(str(b_title))}</h1>" if b_title else ""
    lede_html = f'<p class="lede">{html.escape(str(b_lede))}</p>' if b_lede else ""

    max_frame_w = 0
    total_frame_h = 240
    frame_cols: list[str] = []
    bitmap_frame_specs: list[dict] = []
    frame_arrow_specs: list[dict] = []
    bitmap_preset_rows: list[tuple[str, str]] = []
    bitmap_responsive_specs: list[dict] = []
    bitmap_artboards: list[dict] = []
    canvas_size: tuple[int, int] | None = None

    if preset == "custom":
      try:
        content_html, canvas_size, bitmap_artboards = _canvas_board_markup(
          board, loaded_shots, resolve_rel
        )
      except (TypeError, ValueError) as error:
        return {
          "status": "failed",
          "output": str(dst),
          "boards": board_entries,
          "findings": [{"id": "RUN102", "severity": "error", "message": str(error)}],
          "writes": writes,
        }
      max_frame_w, total_frame_h = canvas_size
    elif board.get("frames"):
      seen_frame_ids: set[str] = set()
      for frame_index, frame in enumerate(board["frames"], 1):
        if not isinstance(frame, dict):
          return {
            "status": "failed",
            "output": str(dst),
            "boards": board_entries,
            "findings": [
              {
                "id": "RUN102",
                "severity": "error",
                "message": "board frames must be TOML tables",
              }
            ],
            "writes": writes,
          }
        frame_id = str(frame.get("id") or f"frame-{frame_index}")
        if frame_id in seen_frame_ids:
          return {
            "status": "failed",
            "output": str(dst),
            "boards": board_entries,
            "findings": [
              {
                "id": "RUN102",
                "severity": "error",
                "message": f"duplicate board frame id: {frame_id}",
              }
            ],
            "writes": writes,
          }
        seen_frame_ids.add(frame_id)
        frame_arrow_specs.append({"id": frame_id})
        frame_attr = html.escape(frame_id, quote=True)
        f_lbl = frame.get("label")
        f_sub = frame.get("sub")
        lbl_html = f'<p class="lbl">{html.escape(str(f_lbl))}</p>' if f_lbl else ""
        sub_html = f'<p class="sub">{html.escape(str(f_sub))}</p>' if f_sub else ""
        head_html = f"<div>{lbl_html}{sub_html}</div>" if (lbl_html or sub_html) else ""

        if "shot" in frame:
          shot_name = str(frame["shot"])
          if shot_name not in loaded_shots:
            return {
              "status": "failed",
              "output": str(dst),
              "boards": board_entries,
              "findings": [
                {
                  "id": "RUN102",
                  "severity": "error",
                  "message": f"missing shot: {shot_name}",
                }
              ],
              "writes": writes,
            }
          s_bytes, sw, sh, srgba = loaded_shots[shot_name]
          disp_w = int(frame["width"]) if frame.get("width") else sw
          if disp_w <= 0:
            return {
              "status": "failed",
              "output": str(dst),
              "boards": board_entries,
              "findings": [
                {
                  "id": "RUN102",
                  "severity": "error",
                  "message": f"invalid frame width: {disp_w}",
                }
              ],
              "writes": writes,
            }
          disp_h = round(sh * (disp_w / max(1, sw)))
          bitmap_rgba = resize_rgba(sw, sh, srgba, disp_w, disp_h)
          max_frame_w = max(max_frame_w, disp_w)
          total_frame_h = max(total_frame_h, disp_h + 140)
          b64 = base64.b64encode(s_bytes).decode("ascii")
          cap_html = (
            f'<div class="cap">{html.escape(str(frame["cap"]))}</div>'
            if frame.get("cap")
            else ""
          )
          body_html = (
            f'<img src="data:image/png;base64,{b64}" width="{disp_w}" height="{disp_h}" '
            f'style="width:{disp_w}px;height:auto;" alt="{html.escape(shot_name)}">'
            f"{cap_html}"
          )
          bitmap_frame_specs.append(
            {
              "id": frame_id,
              "label": str(f_lbl or shot_name),
              "width": disp_w,
              "height": disp_h,
              "rgba": bitmap_rgba,
            }
          )
        elif "html" in frame:
          h_val = str(frame["html"])
          if "<" in h_val and ">" in h_val:
            frag = h_val
          else:
            try:
              frag_path = resolve_rel(h_val)
              is_frag_file = frag_path.is_file()
            except OSError:
              frag_path = None
              is_frag_file = False
            if is_frag_file and frag_path is not None:
              frag = frag_path.read_text(encoding="utf-8").strip()
            else:
              return {
                "status": "failed",
                "output": str(dst),
                "boards": board_entries,
                "findings": [
                  {
                    "id": "RUN102",
                    "severity": "error",
                    "message": f"missing HTML fragment: {h_val}",
                  }
                ],
                "writes": writes,
              }
          if frame.get("bare") or re.match(
            r'^<div\b[^>]*\bclass=["\'][^"\']*\bframe\b', frag
          ):
            body_html = frag
          else:
            body_html = f'<div class="frame">{frag}</div>'
          max_frame_w = max(max_frame_w, 480)
          total_frame_h = max(total_frame_h, 360)
          bitmap_frame_specs.append(
            {
              "id": frame_id,
              "label": str(f_lbl or frame_id),
              "width": 456,
              "height": 260,
              "rgba": None,
            }
          )
        else:
          body_html = ""
          bitmap_frame_specs.append(
            {
              "id": frame_id,
              "label": str(f_lbl or frame_id),
              "width": 456,
              "height": 260,
              "rgba": None,
            }
          )
        frame_cols.append(
          f'<div class="col" data-board-frame="{frame_attr}">{head_html}{body_html}</div>'
        )
      content_html = f'<div class="row">{"".join(frame_cols)}</div>'
    elif board.get("html"):
      h_val = str(board["html"])
      frag_path = resolve_rel(h_val)
      if frag_path.is_file():
        content_html = frag_path.read_text(encoding="utf-8")
      elif "<" in h_val and ">" in h_val:
        content_html = h_val
      else:
        return {
          "status": "failed",
          "output": str(dst),
          "boards": board_entries,
          "findings": [
            {
              "id": "RUN102",
              "severity": "error",
              "message": f"missing HTML fragment: {h_val}",
            }
          ],
          "writes": writes,
        }
      max_frame_w = 1200
      total_frame_h = 600
    else:
      content_html, bitmap_preset_rows = _preset_board_markup(
        preset,
        dark_tokens,
        light_tokens,
        app_css,
        viewport_specs,
        breakpoint_specs,
        redline,
        responsive_shot_name,
        loaded_shots,
      )
      max_frame_w = 1472
      total_frame_h = max(240, math.ceil(len(bitmap_preset_rows) / 3) * 120 + 160)
      if preset == "responsive":
        for viewport, (label, detail) in zip(
          viewport_specs, bitmap_preset_rows, strict=False
        ):
          shot = (
            loaded_shots.get(
              f"{responsive_shot_name}@{viewport['width']}x{viewport['height']}"
            )
            if responsive_shot_name
            else None
          )
          bitmap_responsive_specs.append(
            {
              "label": label,
              "detail": detail,
              "width": int(viewport["width"]),
              "height": int(viewport["height"]),
              "source_width": shot[1] if shot is not None else 0,
              "source_height": shot[2] if shot is not None else 0,
              "rgba": shot[3] if shot is not None else None,
            }
          )

    frame_arrow_markup = ""
    frame_arrow_script = ""
    if preset != "custom" and board.get("arrows"):
      try:
        frame_arrow_markup, frame_arrow_script = _board_arrow_overlay_markup(
          board, frame_arrow_specs
        )
      except (TypeError, ValueError) as error:
        return {
          "status": "failed",
          "output": str(dst),
          "boards": board_entries,
          "findings": [{"id": "RUN102", "severity": "error", "message": str(error)}],
          "writes": writes,
        }

    board_cls = "board wide" if is_wide else "board"
    style_attr = ""
    if not is_wide and max_frame_w + 64 > 1536:
      style_attr = f' style="max-width:{max_frame_w + 64}px"'

    html_doc = (
      "<!doctype html><html data-theme='dark'><head><meta charset='utf-8'><style>"
      f"{typography['font_faces_css']}\n"
      f":root {{ color-scheme: dark; {board_token_css}; "
      f"--nuu-font-sans: {typography['font_sans']}; "
      f"--nuu-font-mono: {typography['font_mono']}; }}\n"
      f"{app_css}\n"
      f"{DESIGN_BOARD_CSS}\n"
      "html, body { margin: 0; padding: 0; background: var(--bg); }"
      "</style></head><body>"
      f'<div id="nuu-render-root" class="{board_cls}"{style_attr}>'
      f"{frame_arrow_markup}{title_html}{lede_html}{content_html}</div>"
      f"<script>{icon_js}</script>{frame_arrow_script}"
      "</body></html>"
    )

    est_w = 2816 if is_wide else max(1536, max_frame_w + 64)
    est_h = max(600, total_frame_h + 128)
    if renderer in {"auto", "chromium"} and _chromium_render_png(
      html_doc,
      est_w,
      est_h,
      b_dst,
      selector=".board",
      settle_ms=settle_ms,
      executable=executable,
    ):
      used_renderer = "chromium"
      out_w, out_h, _ = decode_png(b_dst.read_bytes())
    else:
      if canvas_size is not None:
        out_w, out_h = canvas_size[0] + 64, canvas_size[1] + 112
      else:
        out_w = 2816 if is_wide else max(960, max_frame_w + 64)
        out_h = max(360, total_frame_h)
      if bitmap_preset_rows and not bitmap_responsive_specs:
        if preset in BITMAP_DETAILED_PRESETS:
          out_h = max(out_h, BITMAP_PRESET_HEIGHTS[preset])
        else:
          preset_columns = min(3, len(bitmap_preset_rows))
          preset_rows = math.ceil(len(bitmap_preset_rows) / preset_columns)
          out_h = max(out_h, 160 + preset_rows * 124)
      if bitmap_responsive_specs:
        card_gap = 12
        card_width = (
          out_w - 64 - card_gap * (len(bitmap_responsive_specs) - 1)
        ) // len(bitmap_responsive_specs)
        screen_width = max(1, card_width - 24)
        screen_heights = [
          round(screen_width * item["height"] / max(1, item["width"]))
          for item in bitmap_responsive_specs
        ]
        out_h = max(out_h, 112 + 112 + max(screen_heights, default=1))
      if bitmap_frame_specs:
        frame_gap = 24
        frame_width = sum(frame["width"] + 24 for frame in bitmap_frame_specs)
        frame_width += frame_gap * max(0, len(bitmap_frame_specs) - 1)
        out_w = max(out_w, frame_width + 64)
        out_h = max(out_h, 192 + max(frame["height"] for frame in bitmap_frame_specs))
      canvas = bytearray(shell_bg * (out_w * out_h))
      draw_box(canvas, out_w, out_h, 0, 0, out_w, out_h, line, thickness=2)
      if b_title:
        draw_text(canvas, out_w, out_h, 32, 32, str(b_title), text_color, scale=2)
      if b_lede:
        draw_text(canvas, out_w, out_h, 32, 60, str(b_lede)[:80], muted, scale=1)
      if (
        bitmap_preset_rows
        and not bitmap_responsive_specs
        and preset not in BITMAP_DETAILED_PRESETS
      ):
        columns = min(3, len(bitmap_preset_rows))
        card_gap = 16
        card_width = (out_w - 64 - card_gap * (columns - 1)) // columns
        card_height = 108
        for card_index, (label, detail) in enumerate(bitmap_preset_rows):
          row_index, column_index = divmod(card_index, columns)
          card_x = 32 + column_index * (card_width + card_gap)
          card_y = 112 + row_index * (card_height + card_gap)
          card_bg, border, ink, secondary = (
            (light_surface, light_line, light_text, light_muted)
            if preset == "global-components" and label.startswith("Light")
            else (surface, line, text_color, muted)
          )
          fill_rect(
            canvas, out_w, out_h, card_x, card_y, card_width, card_height, card_bg
          )
          draw_box(
            canvas,
            out_w,
            out_h,
            card_x,
            card_y,
            card_width,
            card_height,
            border,
            thickness=1,
          )
          draw_text(canvas, out_w, out_h, card_x + 12, card_y + 10, label, ink, scale=2)
          draw_text(
            canvas,
            out_w,
            out_h,
            card_x + 12,
            card_y + 38,
            detail[:52],
            secondary,
            scale=1,
          )
          if preset == "global-components":
            fill_rect(
              canvas,
              out_w,
              out_h,
              card_x + 12,
              card_y + 72,
              84,
              22,
              _token_rgba(
                light_tokens if label.startswith("Light") else dark_tokens,
                "--accent-fill",
              ),
            )
      if bitmap_responsive_specs:
        columns = len(bitmap_responsive_specs)
        card_gap = 12
        card_width = (out_w - 64 - card_gap * (columns - 1)) // columns
        tag_x = 32
        for breakpoint in breakpoint_specs:
          draw_text(canvas, out_w, out_h, tag_x, 84, str(breakpoint), muted, scale=1)
          tag_x += (len(str(breakpoint)) + 2) * 8
        for index, item in enumerate(bitmap_responsive_specs):
          card_x = 32 + index * (card_width + card_gap)
          card_y = 112
          screen_width = max(1, card_width - 24)
          screen_height = max(
            1, round(screen_width * item["height"] / max(1, item["width"]))
          )
          screen_y = card_y + 56
          fill_rect(
            canvas,
            out_w,
            out_h,
            card_x,
            card_y,
            card_width,
            screen_height + 80,
            surface,
          )
          draw_box(
            canvas,
            out_w,
            out_h,
            card_x,
            card_y,
            card_width,
            screen_height + 80,
            line,
            thickness=1,
          )
          draw_text(
            canvas,
            out_w,
            out_h,
            card_x + 12,
            card_y + 10,
            item["label"],
            text_color,
            scale=1,
          )
          draw_text(
            canvas,
            out_w,
            out_h,
            card_x + 12,
            card_y + 32,
            item["detail"][:32],
            muted,
            scale=1,
          )
          screen_x = card_x + 12
          fill_rect(
            canvas,
            out_w,
            out_h,
            screen_x,
            screen_y,
            screen_width,
            screen_height,
            _token_rgba(dark_tokens, "--surface-2"),
          )
          if item["rgba"] is not None:
            resized = resize_rgba(
              item["source_width"],
              item["source_height"],
              item["rgba"],
              screen_width,
              screen_height,
            )
            blit_rgba(
              canvas,
              out_w,
              out_h,
              resized,
              screen_width,
              screen_height,
              screen_x,
              screen_y,
            )
          else:
            fill_rect(
              canvas,
              out_w,
              out_h,
              screen_x + 8,
              screen_y + 12,
              max(1, screen_width - 16),
              max(1, min(24, screen_height - 16)),
              _token_rgba(dark_tokens, "--accent-fill"),
            )
          draw_box(
            canvas,
            out_w,
            out_h,
            screen_x,
            screen_y,
            screen_width,
            screen_height,
            line,
            thickness=1,
          )
      if bitmap_preset_rows and preset in BITMAP_DETAILED_PRESETS:
        _draw_bitmap_preset(
          canvas,
          out_w,
          out_h,
          preset,
          bitmap_preset_rows,
          dark_tokens,
          light_tokens,
          app_css,
          redline,
        )
      if bitmap_frame_specs:
        frame_positions = {}
        cursor_x = 32
        frame_y = 112
        for frame in bitmap_frame_specs:
          frame_width = frame["width"]
          frame_height = frame["height"]
          card_width = frame_width + 24
          card_height = frame_height + 48
          fill_rect(
            canvas, out_w, out_h, cursor_x, frame_y, card_width, card_height, surface
          )
          draw_box(
            canvas,
            out_w,
            out_h,
            cursor_x,
            frame_y,
            card_width,
            card_height,
            line,
            thickness=1,
          )
          draw_text(
            canvas,
            out_w,
            out_h,
            cursor_x + 12,
            frame_y + 10,
            frame["label"],
            text_color,
            scale=1,
          )
          image_x, image_y = cursor_x + 12, frame_y + 36
          if frame["rgba"] is not None:
            blit_rgba(
              canvas,
              out_w,
              out_h,
              frame["rgba"],
              frame_width,
              frame_height,
              image_x,
              image_y,
            )
          else:
            fill_rect(
              canvas,
              out_w,
              out_h,
              image_x,
              image_y,
              max(1, frame_width - 24),
              16,
              _token_rgba(dark_tokens, "--surface-2"),
            )
            fill_rect(
              canvas,
              out_w,
              out_h,
              image_x,
              image_y + 28,
              max(1, (frame_width - 24) // 2),
              16,
              _token_rgba(dark_tokens, "--accent-fill"),
            )
          frame_positions[frame["id"]] = {
            "x": cursor_x,
            "y": frame_y,
            "width": card_width,
            "height": card_height,
          }
          cursor_x += card_width + frame_gap
        for arrow in board.get("arrows") or []:
          source = frame_positions.get(str(arrow.get("from", "")))
          target = frame_positions.get(str(arrow.get("to", "")))
          if source is None or target is None:
            continue
          start, end = _nearest_box_edges(source, target)
          route = str(arrow.get("route") or "orthogonal").lower()
          obstacles = [
            frame
            for frame_id, frame in frame_positions.items()
            if frame_id not in {str(arrow.get("from", "")), str(arrow.get("to", ""))}
          ]
          points = _bitmap_route_points(start, end, route, obstacles)
          color = (
            parse_color(arrow["color"])
            if arrow.get("color")
            else _token_rgba(dark_tokens, "--accent")
          )
          for first, second in itertools.pairwise(points):
            draw_line(
              canvas,
              out_w,
              out_h,
              round(first[0]),
              round(first[1]),
              round(second[0]),
              round(second[1]),
              color,
            )
          if len(points) > 1:
            first, second = points[-2], points[-1]
            draw_arrow(
              canvas,
              out_w,
              out_h,
              round(first[0]),
              round(first[1]),
              round(second[0]),
              round(second[1]),
              color,
            )
          if arrow.get("label"):
            label_x, label_y = _bitmap_arrow_label_point(points)
            draw_text(
              canvas,
              out_w,
              out_h,
              label_x,
              label_y,
              str(arrow["label"]),
              text_color,
              scale=1,
            )
      if bitmap_artboards:
        offset_y = 112
        frame_map = {}
        for frame in bitmap_artboards:
          x = 32 + frame["x"]
          y = offset_y + frame["y"]
          width = frame["width"]
          height = frame["height"]
          position = {
            **frame,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
          }
          frame_map[str(frame["id"])] = position
          fill_rect(canvas, out_w, out_h, x, y, width, height, surface)
          draw_box(canvas, out_w, out_h, x, y, width, height, line, thickness=1)
          draw_text(
            canvas,
            out_w,
            out_h,
            x + 12,
            y + 12,
            str(frame.get("label") or frame["id"]),
            text_color,
            scale=2,
          )
          image = frame.get("_bitmap_image")
          if image is not None:
            source_width, source_height, source_rgba = image
            max_width, max_height = max(1, width - 24), max(1, height - 56)
            factor = min(max_width / source_width, max_height / source_height)
            image_width = max(1, round(source_width * factor))
            image_height = max(1, round(source_height * factor))
            resized = resize_rgba(
              source_width, source_height, source_rgba, image_width, image_height
            )
            blit_rgba(
              canvas, out_w, out_h, resized, image_width, image_height, x + 12, y + 40
            )
          else:
            fill_rect(
              canvas,
              out_w,
              out_h,
              x + 12,
              y + 44,
              max(1, width - 24),
              16,
              _token_rgba(dark_tokens, "--surface-2"),
            )
            fill_rect(
              canvas,
              out_w,
              out_h,
              x + 12,
              y + 72,
              max(1, (width - 24) // 2),
              16,
              _token_rgba(dark_tokens, "--accent-fill"),
            )
        for arrow in board.get("arrows") or []:
          source = frame_map.get(str(arrow.get("from", "")))
          target = frame_map.get(str(arrow.get("to", "")))
          if source is None or target is None:
            continue
          start, end = _nearest_box_edges(source, target)
          route = str(arrow.get("route") or "orthogonal").lower()
          obstacles = [
            frame
            for frame_id, frame in frame_map.items()
            if frame_id not in {str(arrow.get("from", "")), str(arrow.get("to", ""))}
          ]
          points = _bitmap_route_points(start, end, route, obstacles)
          color = (
            parse_color(arrow["color"])
            if arrow.get("color")
            else _token_rgba(dark_tokens, "--accent")
          )
          for first_point, second_point in itertools.pairwise(points):
            draw_line(
              canvas,
              out_w,
              out_h,
              round(first_point[0]),
              round(first_point[1]),
              round(second_point[0]),
              round(second_point[1]),
              color,
            )
          if len(points) > 1:
            first_point, second_point = points[-2], points[-1]
            draw_arrow(
              canvas,
              out_w,
              out_h,
              round(first_point[0]),
              round(first_point[1]),
              round(second_point[0]),
              round(second_point[1]),
              color,
            )
          if arrow.get("label"):
            label_x, label_y = _bitmap_arrow_label_point(points)
            draw_text(
              canvas,
              out_w,
              out_h,
              label_x,
              label_y,
              str(arrow["label"]),
              text_color,
              scale=1,
            )
      with b_dst.open("xb") as stream:
        stream.write(encode_png(out_w, out_h, canvas))

    writes.append(str(b_dst))
    entry = {
      "name": b_name,
      "title": b_title,
      "output": str(b_dst),
      "width": out_w,
      "height": out_h,
    }
    if use_wireframe:
      wf_dst = b_dst.with_name(f"{b_dst.stem}-wireframe{b_dst.suffix}")
      wf_w, wf_h, wf_rgba = decode_png(b_dst.read_bytes())
      draw_box(wf_rgba, wf_w, wf_h, 0, 0, wf_w, wf_h, COLORS["blue"], thickness=2)
      draw_label(
        wf_rgba,
        wf_w,
        wf_h,
        4,
        4,
        f"design {b_name} {wf_w}x{wf_h}",
        COLORS["blue"],
        scale=1,
      )
      with wf_dst.open("xb") as stream:
        stream.write(encode_png(wf_w, wf_h, wf_rgba))
      writes.append(str(wf_dst))
      entry["wireframe_output"] = str(wf_dst)
    board_entries.append(entry)

  first = board_entries[0]
  return {
    "status": "composed",
    "type": "design",
    "output": first["output"] if single_png else str(out_dir),
    "boards": board_entries,
    "width": first["width"],
    "height": first["height"],
    "renderer": used_renderer,
    "font_family": typography["font_sans"],
    "embedded_fonts": typography["embedded_fonts"],
    "findings": [],
    "writes": writes,
  }


def run_annotate_cli(args, cfg_info: dict) -> int:
  project_dir = cfg_info["dir"]
  cfg_ann = cfg_info["config"]["annotate"]
  scale = args.scale if args.scale is not None else int(cfg_ann.get("scale", 2))
  crop = args.crop if args.crop is not None else cfg_ann.get("crop", "none")
  if crop == "none":
    crop = None
  if (args.input is None) == (args.url is None):
    raise ValueError("annotate requires exactly one of -i/--input or -u/--url")
  if args.input is not None:
    if Path(args.input).resolve() == Path(args.output).resolve():
      raise ValueError("annotate -o/--output must not overwrite -i/--input")
    res = annotate_image(
      args.input,
      args.output,
      boxes=args.box,
      labels=args.label,
      arrows=args.arrow,
      callouts=args.callout,
      scale=scale,
      project=project_dir,
      crop=crop,
      renderer=resolve_renderer(),
      executable=args.executable,
    )
  else:
    import tempfile

    from playwright.sync_api import sync_playwright

    vp = parse_viewport_spec(args.viewport, cfg_info["config"]["audit"]["viewports"])
    launch_kwargs = chromium_launch_kwargs(args.executable)
    resolved_boxes: list[str] = []
    resolved_labels: list[str] = []
    resolved_arrows: list[str] = []
    resolved_callouts: list[str] = []
    resolved_crop = crop
    with tempfile.TemporaryDirectory(prefix="nuu-ann-") as tmp_str:
      tmp_png = Path(tmp_str) / "live-shot.png"
      with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        try:
          ctx = browser.new_context(
            viewport={"width": int(vp["width"]), "height": int(vp["height"])},
            color_scheme="dark",
            is_mobile=bool(vp.get("mobile")),
            has_touch=bool(vp.get("touch")),
          )
          page = ctx.new_page()
          page.goto(args.url, wait_until="domcontentloaded")
          settle_page(page)

          def _bbox(sel: str) -> dict[str, int]:
            box = page.locator(sel).first.bounding_box()
            if not box or box["width"] <= 0 or box["height"] <= 0:
              raise ValueError(f"Selector has no visible bounding box: {sel}")
            return {
              "x": round(box["x"]),
              "y": round(box["y"]),
              "w": max(1, round(box["width"])),
              "h": max(1, round(box["height"])),
            }

          for sel in args.selector:
            b = _bbox(sel)
            resolved_boxes.append(f"{b['x']},{b['y']},{b['w']},{b['h']},red")
          for bspec in args.box:
            parts = [p.strip() for p in bspec.rsplit(",", 1)]
            if len(parts) == 2 and (
              parts[1].lower() in COLORS or parts[1].startswith("#")
            ):
              sel_part, col_part = parts[0], parts[1]
            else:
              sel_part, col_part = bspec.strip(), "red"
            if re.fullmatch(r"-?\d+,-?\d+,\d+,\d+", sel_part):
              resolved_boxes.append(f"{sel_part},{col_part}")
            else:
              b = _bbox(sel_part)
              resolved_boxes.append(f"{b['x']},{b['y']},{b['w']},{b['h']},{col_part}")
          for lspec in args.label:
            if re.match(r"^-?\d+,-?\d+,", lspec):
              resolved_labels.append(lspec)
            else:
              tokens = [p.strip() for p in lspec.split(",")]
              if len(tokens) >= 3 and (
                tokens[-1].lower() in COLORS or tokens[-1].startswith("#")
              ):
                sel_part, txt_part, col_part = (
                  tokens[0],
                  ",".join(tokens[1:-1]),
                  tokens[-1],
                )
              elif len(tokens) >= 2:
                sel_part, txt_part, col_part = tokens[0], ",".join(tokens[1:]), "red"
              else:
                raise ValueError(f"Invalid live label spec: {lspec}")
              b = _bbox(sel_part)
              font_px = max(11, 6 * scale + 1)
              pad_y = max(2, scale)
              border = max(1, scale // 2)
              label_height = math.ceil(font_px * 1.25 + 2 * pad_y + 2 * border)
              label_y = max(0, b["y"] - label_height - 8)
              resolved_labels.append(f"{b['x']},{label_y},{txt_part},{col_part}")
          for aspec in args.arrow:
            if re.match(r"^-?\d+,-?\d+,-?\d+,-?\d+", aspec):
              resolved_arrows.append(aspec)
            else:
              tokens = [p.strip() for p in aspec.split(",")]
              if len(tokens) == 3 and (
                tokens[2].lower() in COLORS or tokens[2].startswith("#")
              ):
                s1, s2, col_part = tokens[0], tokens[1], tokens[2]
              elif len(tokens) == 2:
                s1, s2, col_part = tokens[0], tokens[1], "red"
              else:
                raise ValueError(f"Invalid live arrow spec: {aspec}")
              b1, b2 = _bbox(s1), _bbox(s2)
              (x1, y1), (x2, y2) = _nearest_box_edges(b1, b2)
              resolved_arrows.append(f"{x1},{y1},{x2},{y2},{col_part}")
          for cspec in args.callout:
            if re.match(r"^-?\d+,-?\d+,\d+", cspec):
              resolved_callouts.append(cspec)
            else:
              tokens = [p.strip() for p in cspec.split(",")]
              if len(tokens) < 2:
                raise ValueError(f"Invalid live callout spec: {cspec}")
              b = _bbox(tokens[0])
              rest = ",".join(tokens[1:])
              resolved_callouts.append(f"{b['x']},{b['y']},{rest}")
          if (
            isinstance(crop, str)
            and crop not in {"auto", "pad", "none"}
            and not re.fullmatch(r"\d+,\d+,\d+,\d+", crop)
          ):
            c_parts = [p.strip() for p in crop.rsplit(",", 1)]
            if len(c_parts) == 2 and c_parts[1].isdigit():
              c_sel, pad_px = c_parts[0], int(c_parts[1])
            else:
              c_sel, pad_px = crop.strip(), 16
            b = _bbox(c_sel)
            cx = max(0, b["x"] - pad_px)
            cy = max(0, b["y"] - pad_px)
            cw = min(int(vp["width"]) - cx, b["w"] + 2 * pad_px)
            ch = min(int(vp["height"]) - cy, b["h"] + 2 * pad_px)
            resolved_crop = f"{cx},{cy},{cw},{ch}"

          page.screenshot(path=str(tmp_png))
          ctx.close()
        finally:
          browser.close()
      res = annotate_image(
        tmp_png,
        args.output,
        boxes=resolved_boxes,
        labels=resolved_labels,
        arrows=resolved_arrows,
        callouts=resolved_callouts,
        scale=scale,
        project=project_dir,
        crop=resolved_crop,
        renderer=resolve_renderer(),
        executable=args.executable,
      )
  print(
    f"# Annotation Complete\n\n"
    f"- Output: `{res['output']}`\n"
    f"- Dimensions: `{res['width']}x{res['height']}`\n"
    f"- Renderer: `{res['renderer']}`\n"
    f"- Scale: `{res['scale']}`\n",
    end="",
  )
  return 0


def run_board_cli(args, cfg_info: dict) -> int:
  project_dir = cfg_info["dir"]
  cfg_board = cfg_info["config"]["board"]
  board_type = args.type or cfg_board.get("type", "panels")
  columns = (
    args.columns if args.columns is not None else int(cfg_board.get("columns", 2))
  )
  scale = args.scale if args.scale is not None else int(cfg_board.get("scale", 2))
  wireframe = (
    bool(args.wireframe)
    if args.wireframe is not None
    else bool(cfg_board.get("wireframe", False))
  )
  spec_data: dict | None = None
  spec_base_dir = project_dir
  if args.spec is not None:
    spec_file = Path(args.spec).resolve()
    if not spec_file.is_file():
      raise FileNotFoundError(f"RUN102: Board spec file not found: {args.spec}")
    spec_data = tomllib.loads(spec_file.read_text(encoding="utf-8"))
    spec_base_dir = spec_file.parent
  elif cfg_board.get("boards"):
    spec_data = {"board": cfg_board}

  if spec_data is not None and (
    "boards" in spec_data
    or (isinstance(spec_data.get("board"), dict) and spec_data["board"].get("boards"))
  ):
    b_root = (
      spec_data.get("board") if isinstance(spec_data.get("board"), dict) else spec_data
    )
    shots_map = b_root.get("shots") or spec_data.get("shots") or {}
    boards_list = b_root.get("boards") or spec_data.get("boards") or []
    sheet_override = b_root.get("stylesheet") or spec_data.get("stylesheet")
    icons_override = b_root.get("icons") or spec_data.get("icons")
    spec_payload: dict = {
      "stylesheet": str(sheet_override)
      if sheet_override
      else str(cfg_info["stylesheet_path"]),
      "shots": shots_map,
      "boards": boards_list,
    }
    if icons_override:
      spec_payload["icons"] = icons_override
    res = build_design_boards(
      args.output,
      spec_path=None,
      spec_obj=spec_payload,
      project=spec_base_dir,
      wireframe=wireframe,
      only=args.only,
      renderer=resolve_renderer(),
      executable=args.executable,
    )
  else:
    if str(board_type) not in {"panels", "0", "10"} and not args.panel:
      res = build_design_boards(
        args.output,
        spec_path=None,
        spec_obj={
          "stylesheet": str(cfg_info["stylesheet_path"]),
          "shots": cfg_board.get("shots") or {},
          "boards": [
            {
              "name": Path(args.output).stem or f"board-{board_type}",
              "type": str(board_type),
              "title": args.title or f"Board ({board_type})",
              "frames": [],
            }
          ],
        },
        project=project_dir,
        wireframe=wireframe,
        only=args.only,
        renderer=resolve_renderer(),
        executable=args.executable,
      )
    else:
      res = build_board(
        args.output,
        title=args.title,
        panels=args.panel,
        callouts=args.callout,
        spec_path=None,
        columns=columns,
        scale=scale,
        project=project_dir,
        wireframe=wireframe,
        renderer=resolve_renderer(),
        executable=args.executable,
      )
  if res.get("status") == "failed":
    lines = ["# Board Failed\n"]
    for item in res.get("findings") or []:
      lines.append(
        f"- `{item.get('id', 'RUN102')}` ({item.get('severity', 'error')}): {item.get('message', '')}"
      )
    print("\n".join(lines) + "\n", end="")
    return 1
  print(
    f"# Board Composed\n\n"
    f"- Output: `{res['output']}`\n"
    f"- Dimensions: `{res['width']}x{res['height']}`\n"
    f"- Renderer: `{res['renderer']}`\n"
    f"- Font: `{res.get('font_family', '')}`\n",
    end="",
  )
  return 0
