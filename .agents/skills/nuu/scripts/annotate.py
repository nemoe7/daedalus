"""Draw bounding boxes, labels, arrows, and callouts onto a PNG image."""

from __future__ import annotations

import argparse
import json
import math
import struct
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


def annotate_image(
  input_path: Path,
  output_path: Path,
  boxes=(),
  labels=(),
  arrows=(),
  callouts=(),
  spec_path: Path | None = None,
  scale: int | None = None,
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
  width, height, rgba = decode_png(src.read_bytes())
  for x, y, w, h, color in all_boxes:
    draw_box(rgba, width, height, x, y, w, h, color, thickness=max(2, resolved_scale))
  for x1, y1, x2, y2, color in all_arrows:
    draw_arrow(rgba, width, height, x1, y1, x2, y2, color)
  for x, y, text, color in all_labels:
    draw_label(rgba, width, height, x, y, text, color, scale=resolved_scale)
  for x, y, number, color, label in all_callouts:
    draw_callout(rgba, width, height, x, y, number, color, label, scale=resolved_scale)
  encoded = encode_png(width, height, rgba)
  dst.parent.mkdir(parents=True, exist_ok=True)
  with dst.open("xb") as stream:
    stream.write(encoded)
  return {
    "status": "annotated",
    "input": str(src),
    "output": str(dst),
    "width": width,
    "height": height,
    "scale": resolved_scale,
    "annotations": {
      "boxes": len(all_boxes),
      "labels": len(all_labels),
      "arrows": len(all_arrows),
      "callouts": len(all_callouts),
    },
    "findings": [],
    "writes": [str(dst)],
  }


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
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
  except (OSError, ValueError, TypeError) as error:
    print(json.dumps({"tool": "annotate", "status": "error", "error": str(error)}))
    return 2


if __name__ == "__main__":
  sys.exit(main())
