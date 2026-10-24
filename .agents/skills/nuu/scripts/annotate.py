"""Draw bounding boxes, labels, arrows, callouts, and composite boards onto PNG images."""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
COLORS = {
  "red": (225, 29, 72, 255),
  "blue": (37, 99, 235, 255),
  "green": (22, 163, 74, 255),
  "amber": (217, 119, 6, 255),
  "white": (255, 255, 255, 255),
  "black": (17, 24, 39, 255),
}

DEFAULT_FONT_SANS = 'system-ui, -apple-system, "Segoe UI", sans-serif'
DEFAULT_FONT_MONO = 'ui-monospace, "SFMono-Regular", monospace'
SKIP_DIRS = {
  ".git",
  ".agents",
  "node_modules",
  "arena-state",
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

# Compact 5x7 bitmap glyphs (rows encoded as 5-bit integers) for digits, uppercase, and punctuation.
GLYPHS = {
  " ": (0, 0, 0, 0, 0, 0, 0),
  "!": (4, 4, 4, 4, 4, 0, 4),
  "-": (0, 0, 0, 31, 0, 0, 0),
  ".": (0, 0, 0, 0, 0, 0, 4),
  ":": (0, 4, 0, 0, 0, 4, 0),
  "/": (1, 1, 2, 4, 8, 16, 16),
  "0": (14, 17, 19, 21, 25, 17, 14),
  "1": (4, 12, 4, 4, 4, 4, 14),
  "2": (14, 17, 1, 2, 4, 8, 31),
  "3": (30, 1, 1, 14, 1, 1, 30),
  "4": (2, 6, 10, 18, 31, 2, 2),
  "5": (31, 16, 30, 1, 1, 17, 14),
  "6": (6, 8, 16, 30, 17, 17, 14),
  "7": (31, 1, 2, 4, 8, 8, 8),
  "8": (14, 17, 17, 14, 17, 17, 14),
  "9": (14, 17, 17, 15, 1, 2, 12),
  "A": (14, 17, 17, 31, 17, 17, 17),
  "B": (30, 17, 17, 30, 17, 17, 30),
  "C": (14, 17, 16, 16, 16, 17, 14),
  "D": (28, 18, 17, 17, 17, 18, 28),
  "E": (31, 16, 16, 30, 16, 16, 31),
  "F": (31, 16, 16, 30, 16, 16, 16),
  "G": (14, 17, 16, 23, 17, 17, 15),
  "H": (17, 17, 17, 31, 17, 17, 17),
  "I": (14, 4, 4, 4, 4, 4, 14),
  "J": (7, 2, 2, 2, 18, 18, 12),
  "K": (17, 18, 20, 24, 20, 18, 17),
  "L": (16, 16, 16, 16, 16, 16, 31),
  "M": (17, 27, 21, 21, 17, 17, 17),
  "N": (17, 25, 21, 19, 17, 17, 17),
  "O": (14, 17, 17, 17, 17, 17, 14),
  "P": (30, 17, 17, 30, 16, 16, 16),
  "Q": (14, 17, 17, 17, 21, 18, 13),
  "R": (30, 17, 17, 30, 20, 18, 17),
  "S": (15, 16, 16, 14, 1, 1, 30),
  "T": (31, 4, 4, 4, 4, 4, 4),
  "U": (17, 17, 17, 17, 17, 17, 14),
  "V": (17, 17, 17, 17, 17, 10, 4),
  "W": (17, 17, 17, 21, 21, 27, 17),
  "X": (17, 17, 10, 4, 10, 17, 17),
  "Y": (17, 17, 10, 4, 4, 4, 4),
  "Z": (31, 1, 2, 4, 8, 16, 31),
}


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


def resolve_project_typography(project_root: Path | None = None) -> dict:
  root = (Path(project_root) if project_root else Path.cwd()).resolve()
  css_files = _iter_project_files(root, {".css"})
  font_Index: dict[str, Path] = {}
  for font_path in _iter_project_files(root, set(FONT_MIMES)):
    font_Index.setdefault(font_path.name, font_path)

  # Prefer product CSS with @font-face declarations over reference fallback sheets.
  def css_priority(path: Path) -> tuple[int, str]:
    rel = path.relative_to(root).as_posix() if root in path.parents else path.as_posix()
    is_nuu_ref = "nuu/references/style.css" in rel
    return (1 if is_nuu_ref else 0, rel)

  css_files.sort(key=css_priority)
  font_sans = None
  font_mono = None
  source_css = None
  font_face_rules: list[str] = []
  seen_faces: set[tuple[str, str, str, str]] = set()

  for css_path in css_files:
    try:
      text = css_path.read_text(encoding="utf-8")
    except OSError:
      continue
    clean = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    if font_sans is None:
      m_sans = re.search(r"--font-sans\s*:\s*([^;{}]+)", clean)
      if m_sans:
        font_sans = m_sans.group(1).strip()
        source_css = str(css_path)
      else:
        m_body = re.search(
          r"\bbody\s*\{[^}]*?\bfont-family\s*:\s*([^;{}]+)", clean, flags=re.DOTALL
        )
        if m_body and "var(" not in m_body.group(1):
          font_sans = m_body.group(1).strip()
          source_css = str(css_path)
    if font_mono is None:
      m_mono = re.search(r"--font-mono\s*:\s*([^;{}]+)", clean)
      if m_mono:
        font_mono = m_mono.group(1).strip()
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
      ext = candidate.suffix.lower()
      mime = FONT_MIMES.get(ext)
      if not mime:
        continue
      key = (family, weight, fstyle, candidate.name)
      if key in seen_faces:
        continue
      seen_faces.add(key)
      b64 = base64.b64encode(candidate.read_bytes()).decode("ascii")
      fmt = "woff2" if ext == ".woff2" else "woff" if ext == ".woff" else "truetype"
      font_face_rules.append(
        f'@font-face {{ font-family: "{family}"; src: url("data:{mime};base64,{b64}") format("{fmt}"); '
        f"font-weight: {weight}; font-style: {fstyle}; font-display: block; }}"
      )

  return {
    "font_sans": font_sans or DEFAULT_FONT_SANS,
    "font_mono": font_mono or DEFAULT_FONT_MONO,
    "font_faces_css": "\n".join(font_face_rules),
    "source_css": source_css,
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
      d_idx = (ty * dst_w + tx) * 4
      dst[d_idx : d_idx + 4] = src[s_idx : s_idx + 4]


def blend_pixel(rgba: bytearray, width: int, height: int, x: int, y: int, color):
  if not (0 <= x < width and 0 <= y < height):
    return
  idx = (y * width + x) * 4
  sr, sg, sb, sa = color
  if sa >= 255:
    rgba[idx : idx + 4] = bytes((sr, sg, sb, 255))
    return
  dr, dg, db, da = rgba[idx : idx + 4]
  alpha = sa / 255.0
  inv = 1.0 - alpha
  rgba[idx] = int(sr * alpha + dr * inv)
  rgba[idx + 1] = int(sg * alpha + dg * inv)
  rgba[idx + 2] = int(sb * alpha + db * inv)
  rgba[idx + 3] = min(255, int(sa + da * inv))


def fill_rect(rgba, width, height, x, y, w, h, color):
  for py in range(max(0, y), min(height, y + h)):
    for px in range(max(0, x), min(width, x + w)):
      blend_pixel(rgba, width, height, px, py, color)


def draw_box(rgba, width, height, x, y, w, h, color, thickness=2):
  if w <= 0 or h <= 0:
    raise ValueError("box width and height must be positive")
  fill = (color[0], color[1], color[2], 32)
  fill_rect(rgba, width, height, x, y, w, h, fill)
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


def draw_text(rgba, width, height, x, y, text, color, scale=1):
  cursor = x
  for ch in str(text).upper():
    glyph = GLYPHS.get(ch, GLYPHS[" "])
    for row_idx, row_bits in enumerate(glyph):
      for col_idx in range(5):
        if (row_bits >> (4 - col_idx)) & 1:
          for sy in range(scale):
            for sx in range(scale):
              blend_pixel(
                rgba,
                width,
                height,
                cursor + col_idx * scale + sx,
                y + row_idx * scale + sy,
                color,
              )
    cursor += 6 * scale


def draw_label(rgba, width, height, x, y, text, color, scale=2):
  label = str(text).strip()
  if not label:
    raise ValueError("label text must not be empty")
  pad = 3 * scale + 1
  box_w = (len(label) * 6 - 1) * scale + pad * 2
  box_h = 7 * scale + pad * 2
  fill_rect(rgba, width, height, x, y, box_w, box_h, (17, 24, 39, 230))
  draw_box(rgba, width, height, x, y, box_w, box_h, color, thickness=max(1, scale // 2))
  draw_text(rgba, width, height, x + pad, y + pad, label, COLORS["white"], scale=scale)


def draw_callout(rgba, width, height, x, y, number, color, label=None, scale=2):
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
  text_w = (len(badge) * 6 - 1) * scale
  text_h = 7 * scale
  draw_text(
    rgba,
    width,
    height,
    x - text_w // 2,
    y - text_h // 2,
    badge,
    COLORS["white"],
    scale=scale,
  )
  if label:
    pad = 3 * scale + 1
    box_h = 7 * scale + pad * 2
    draw_label(
      rgba,
      width,
      height,
      x + radius + 4 * scale,
      y - box_h // 2,
      label,
      color,
      scale=scale,
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
  html_doc: str, width: int, height: int, output_path: Path
) -> bool:
  if os.environ.get("NUU_RENDERER") == "bitmap" or not shutil.which("node"):
    return False
  chromium_bin = os.environ.get("CHROMIUM_PATH") or (
    "/tmp/chromium" if Path("/tmp/chromium").is_file() else ""
  )
  env = dict(os.environ)
  if Path("/tmp/al2023/lib").is_dir():
    existing_ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = (
      f"/tmp/al2023/lib:{existing_ld}" if existing_ld else "/tmp/al2023/lib"
    )
  node_paths = [
    str(Path(__file__).resolve().parent),
    "/home/user/node_modules",
  ]
  if env.get("NODE_PATH"):
    node_paths.append(env["NODE_PATH"])
  env["NODE_PATH"] = ":".join(node_paths)
  script = """
const fs = require("node:fs");
let pw = null;
for (const mod of ["playwright-core", "playwright"]) {
  try { pw = require(mod); break; } catch {}
}
if (!pw || !pw.chromium) process.exit(2);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
(async () => {
  const pwExec = typeof pw.chromium.executablePath === "function" ? pw.chromium.executablePath() : "";
  const execPath = payload.executable || pwExec;
  if (!execPath || !fs.existsSync(execPath)) process.exit(3);
  const browser = await pw.chromium.launch({
    executablePath: execPath,
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--single-process"]
  });
  try {
    const vw = Math.max(2400, payload.width + 256);
    const vh = Math.max(1600, payload.height + 256);
    const context = await browser.newContext({
      viewport: { width: vw, height: vh },
      deviceScaleFactor: 1
    });
    const page = await context.newPage();
    await page.setContent(payload.html, { waitUntil: "load" });
    await page.evaluate(() => document.fonts ? document.fonts.ready : Promise.resolve());
    const root = page.locator("#nuu-render-root");
    await root.screenshot({ path: payload.output });
  } finally {
    await browser.close();
  }
})().catch(() => process.exit(4));
"""
  proc = subprocess.run(
    ["node", "-e", script],
    input=json.dumps(
      {
        "html": html_doc,
        "width": max(1, width),
        "height": max(1, height),
        "output": str(output_path),
        "executable": chromium_bin,
      }
    ),
    text=True,
    capture_output=True,
    env=env,
    timeout=25,
    check=False,
  )
  return proc.returncode == 0 and output_path.is_file()


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
      f"border:{stroke_w}px solid {hex_c};background:rgba({color[0]},{color[1]},{color[2]},0.14);"
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
      f"background:rgba(17,24,39,0.92);color:#ffffff;border:{max(1, scale // 2)}px solid {hex_c};"
      f"border-radius:6px;padding:{max(2, scale)}px {max(6, scale * 3)}px;"
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
      f"border-radius:9999px;background:{hex_c};border:2px solid #ffffff;color:#ffffff;"
      f"display:flex;align-items:center;justify-content:center;font-family:var(--nuu-font-sans);"
      f"font-size:{font_px}px;font-weight:700;line-height:1;box-sizing:border-box;"
      f'box-shadow:0 2px 6px rgba(0,0,0,0.4);">{esc_badge}</div>'
    )
    if label:
      esc_label = html.escape(str(label))
      parts.append(
        f'<div style="position:absolute;left:{x + r + 6}px;top:{y - r}px;min-height:{badge_size}px;'
        f"display:inline-flex;align-items:center;background:rgba(17,24,39,0.92);color:#ffffff;"
        f"border:1.5px solid {hex_c};border-radius:6px;padding:2px 8px;"
        f"font-family:var(--nuu-font-sans);font-size:{font_px}px;font-weight:600;line-height:1.2;"
        f'white-space:nowrap;box-sizing:border-box;box-shadow:0 2px 6px rgba(0,0,0,0.35);">{esc_label}</div>'
      )
  return "".join(parts)


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
    loaded = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
      raise TypeError("annotate spec must be a JSON object")
    spec = loaded
  resolved_scale = int(scale if scale is not None else spec.get("scale", 2))
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
  if not (all_boxes or all_labels or all_arrows or all_callouts):
    raise ValueError("annotate requires at least one box, label, arrow, or callout")
  raw_bytes = src.read_bytes()
  width, height, rgba = decode_png(raw_bytes)
  typography = resolve_project_typography(project or src.parent)
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
      f":root {{ --nuu-font-sans: {typography['font_sans']}; --nuu-font-mono: {typography['font_mono']}; }}"
      "html, body { margin: 0; padding: 0; background: transparent; }"
      "</style></head><body>"
      f'<div id="nuu-render-root" style="position:relative;width:{width}px;height:{height}px;overflow:hidden;">'
      f'<img src="data:image/png;base64,{b64_img}" width="{width}" height="{height}" style="display:block;" />'
      f"{overlay_html}</div></body></html>"
    )
    if _chromium_render_png(html_doc, width, height, dst):
      used_renderer = "chromium"
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
    encoded = encode_png(width, height, rgba)
    with dst.open("xb") as stream:
      stream.write(encoded)
  return {
    "status": "annotated",
    "input": str(src),
    "output": str(dst),
    "width": width,
    "height": height,
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


def _parse_panel_entry(entry, base_dir: Path) -> dict:
  if isinstance(entry, dict):
    raw_path = Path(entry["path"])
    src = (base_dir / raw_path).resolve() if not raw_path.is_absolute() else raw_path
    crop = entry.get("crop")
    if isinstance(crop, str):
      crop = [int(v.strip()) for v in crop.split(",")]
    return {
      "label": str(entry.get("label") or src.stem),
      "path": src,
      "crop": tuple(int(v) for v in crop) if crop else None,
      "boxes": [parse_box(b) for b in (entry.get("boxes") or [])],
      "arrows": [parse_arrow(a) for a in (entry.get("arrows") or [])],
      "labels": [parse_label(l) for l in (entry.get("labels") or [])],
      "callouts": [parse_callout(c) for c in (entry.get("callouts") or [])],
    }
  text = str(entry).strip()
  if "=" in text:
    label, rest = text.split("=", 1)
    label = label.strip()
  else:
    label, rest = "", text
  crop = None
  if "@" in rest:
    path_part, crop_part = rest.rsplit("@", 1)
    coords = [int(v.strip()) for v in crop_part.split(",")]
    if len(coords) != 4:
      raise ValueError("panel crop requires @x,y,width,height")
    crop = tuple(coords)
  else:
    path_part = rest
  raw_path = Path(path_part.strip())
  src = (base_dir / raw_path).resolve() if not raw_path.is_absolute() else raw_path
  return {
    "label": label or src.stem,
    "path": src,
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
):
  dst = Path(output_path).resolve()
  if dst.exists():
    raise ValueError("board output path already exists")
  spec = {}
  base_dir = Path(project).resolve() if project else Path.cwd()
  if spec_path is not None:
    spec_file = Path(spec_path).resolve()
    base_dir = spec_file.parent
    loaded = json.loads(spec_file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
      raise TypeError("board spec must be a JSON object")
    spec = loaded
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
  for panel in parsed_panels:
    if not panel["path"].is_file():
      raise ValueError(f"board panel PNG does not exist: {panel['path']}")
    if panel["path"].resolve() == dst:
      raise ValueError("board output must not overwrite a panel input in place")

  prepared_panels = []
  for idx, panel in enumerate(parsed_panels, 1):
    w, h, rgba = decode_png(panel["path"].read_bytes())
    if panel["crop"]:
      cx, cy, cw, ch = panel["crop"]
      w, h, rgba = crop_rgba(w, h, rgba, cx, cy, cw, ch)
    png_bytes = encode_png(w, h, rgba)
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
      '<div style="background:#181714;border:1px solid #2b2926;border-radius:10px;'
      'padding:12px;display:flex;flex-direction:column;gap:8px;box-sizing:border-box;">'
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">'
      f'<span style="font-size:13px;font-weight:600;color:#ece9e2;letter-spacing:0.01em;">'
      f"{p['index']}. {html.escape(p['label'])}</span>"
      f'<span style="font-family:var(--nuu-font-mono);font-size:11px;color:#9c968c;">'
      f"{p['width']}×{p['height']} px</span></div>"
      f'<div style="position:relative;width:{p["width"]}px;height:{p["height"]}px;'
      'border-radius:6px;overflow:hidden;border:1px solid #2b2926;background:#100f0d;">'
      f'<img src="data:image/png;base64,{b64}" width="{p["width"]}" height="{p["height"]}" style="display:block;" />'
      f"{overlay}</div></div>"
    )

  header_html = ""
  if board_title:
    n_panels = len(prepared_panels)
    esc_title = html.escape(str(board_title))
    sub_html = (
      f'<span style="font-size:12px;color:#9c968c;font-weight:400;">{html.escape(str(board_subtitle))}</span>'
      if board_subtitle
      else f'<span style="font-family:var(--nuu-font-mono);font-size:11px;color:#9c968c;">{n_panels} panels</span>'
    )
    header_html = (
      '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:16px;'
      'padding-bottom:10px;border-bottom:1px solid #2b2926;">'
      f'<div style="font-size:18px;font-weight:700;color:#ece9e2;letter-spacing:-0.01em;">{esc_title}</div>'
      f"{sub_html}</div>"
    )

  legend_html = ""
  if legend_items:
    pills = []
    for num, lbl, col in legend_items:
      hex_c = css_hex(col)
      pills.append(
        '<div style="display:inline-flex;align-items:center;gap:8px;background:#181714;'
        'border:1px solid #2b2926;border-radius:9999px;padding:4px 12px 4px 6px;">'
        f'<span style="width:20px;height:20px;border-radius:9999px;background:{hex_c};color:#fff;'
        'display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;">'
        f"{html.escape(num)}</span>"
        f'<span style="font-size:12px;font-weight:500;color:#ece9e2;">{html.escape(lbl)}</span></div>'
      )
    legend_html = (
      '<div style="display:flex;flex-wrap:wrap;gap:10px;padding-top:10px;border-top:1px solid #2b2926;">'
      + "".join(pills)
      + "</div>"
    )

  html_doc = (
    "<!doctype html><html><head><meta charset='utf-8'><style>"
    f"{typography['font_faces_css']}\n"
    f":root {{ --nuu-font-sans: {typography['font_sans']}; --nuu-font-mono: {typography['font_mono']}; }}"
    "html, body { margin: 0; padding: 0; background: #100f0d; font-family: var(--nuu-font-sans); }"
    "</style></head><body>"
    '<div id="nuu-render-root" style="display:inline-flex;flex-direction:column;gap:14px;'
    'padding:20px;background:#100f0d;border:1px solid #2b2926;border-radius:12px;box-sizing:border-box;">'
    f"{header_html}"
    f'<div style="display:grid;grid-template-columns:repeat({cols}, max-content);gap:16px;align-items:start;">'
    + "".join(panel_cards_html)
    + "</div>"
    f"{legend_html}</div></body></html>"
  )

  used_renderer = "bitmap"
  if renderer in {"auto", "chromium"} and _chromium_render_png(
    html_doc, total_w + 64, total_h + 64, dst
  ):
    used_renderer = "chromium"
    out_w, out_h, _ = decode_png(dst.read_bytes())
  else:
    canvas = bytearray([16, 15, 13, 255] * (total_w * total_h))
    draw_box(
      canvas, total_w, total_h, 0, 0, total_w, total_h, (43, 41, 38, 255), thickness=2
    )
    y_cursor = pad
    if board_title:
      draw_text(
        canvas,
        total_w,
        total_h,
        pad,
        y_cursor + 8,
        str(board_title),
        COLORS["white"],
        scale=2,
      )
      y_cursor += header_h + 12
    for i, p in enumerate(prepared_panels):
      r, c = divmod(i, cols)
      px = pad + sum(col_widths[:c]) + gap * c
      py = y_cursor + sum(row_heights[:r]) + gap * r
      pw = p["width"] + panel_pad * 2
      ph = p["height"] + panel_header_h + panel_pad * 2
      fill_rect(canvas, total_w, total_h, px, py, pw, ph, (24, 23, 20, 255))
      draw_box(canvas, total_w, total_h, px, py, pw, ph, (43, 41, 38, 255), thickness=1)
      draw_text(
        canvas,
        total_w,
        total_h,
        px + panel_pad,
        py + 10,
        f"{p['index']}. {p['label']}",
        COLORS["white"],
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
    "findings": [],
    "writes": writes,
  }
  if wf_dst is not None:
    result_payload["wireframe_output"] = str(wf_dst)
  return result_payload


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("-i", "--input", type=Path, required=True)
  parser.add_argument("-o", "--output", type=Path, required=True)
  parser.add_argument("-b", "--box", action="append", default=[])
  parser.add_argument("-l", "--label", action="append", default=[])
  parser.add_argument("-a", "--arrow", action="append", default=[])
  parser.add_argument("-c", "--callout", action="append", default=[])
  parser.add_argument("-s", "--spec", type=Path)
  parser.add_argument("-S", "--scale", type=int)
  parser.add_argument("-p", "--project", type=Path)
  args = parser.parse_args(argv)
  try:
    result = annotate_image(
      args.input,
      args.output,
      boxes=args.box,
      labels=args.label,
      arrows=args.arrow,
      callouts=args.callout,
      spec_path=args.spec,
      scale=args.scale,
      project=args.project,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
  except (OSError, ValueError, TypeError) as error:
    print(json.dumps({"tool": "annotate", "status": "error", "error": str(error)}))
    return 2


if __name__ == "__main__":
  sys.exit(main())
