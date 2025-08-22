#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GoldenEye 007 (N64) font extractor → PNG

Goal
-----
[0] Read a C source that defines a GoldenEye font as two arrays:
    - `<name>_fontchartable` : u32 entries describing each glyph (6 u32 per glyph)
    - `<name>_fontbytes`     : u32-packed raw IA4 image data (tiled 8×8)
[1] Parse those arrays from the C file.
[2] Deswizzle/de-tile IA4 nibble data into linear scanline order.
[3] Decode IA4 (3 bits intensity, 1 bit alpha) → RGBA8.
[4] Rebuild glyph bitmaps and save:
    - a single 'A' sample PNG
    - (optionally) a full atlas PNG packing all glyphs.

Background (formats)
--------------------
• IA4 texel = 4 bits total: I(3 bits) + A(1 bit). Intensity is replicated to RGB, alpha is 1-bit.
  Source: Nintendo 64 Programming Manual (IA4 = 3/1).  # see refs in the chat body.
• Texture data in GE fonts is stored as 8×8 tiles, packed nibbles (two pixels per byte),
  in simple row-major tile ordering. We "deswizzle" back to linear.

C Structures (reverse-engineered from decomp)
---------------------------------------------
Each glyph descriptor is 6 × u32 (24 bytes):
    0x00: id           # codepoint (e.g., 65 for 'A')
    0x04: v_offset     # vertical offset from baseline (pixels; positive moves down)
    0x08: height       # glyph height in pixels
    0x0C: width        # glyph width in pixels
    0x10: flags        # unknown/flags/advance (often unused for decoding)
    0x14: data_offset  # byte offset to IA4 tiled data (relocated to pointer at runtime)

Notes
-----
• We assume the first glyph with a valid `width>0` has the lowest `data_offset`;
  we treat that as the "base" and address subsequent glyphs relative to it
  (matching how assets are commonly stored/relocated in engine memory).
• IA4 intensity expansion uses exact scaling I(0..7) → 0..255 as (I*255)//7
  (visually smoother than I*32). Alpha uses 0 or 255.

Usage
-----
Place this script next to `<font_name>.c` (e.g., `fontBankGothic.c`) and run it.
"""

import math
import os
import re
from dataclasses import dataclass
from typing import List, Tuple, Iterable, Optional

from PIL import Image


# [1] ---------- Parsing helpers -------------------------------------------------

def parse_c_array(c_source: str, array_name: str) -> List[int]:
    """
    [1.1] Extract a `u32` C array by name and return it as a list of integers.

    Expected C fragment:
        u32 fontBankGothic_fontchartable[] = {
            0x00000041, 0x00000004, ...
        };

    We:
      • capture content between braces {...}
      • split by commas
      • parse each as hex int
    """
    # [1.1.a] Regex captures `u32 <arr>[...] = { ... };` with non-greedy body.
    regex = r"u32\s+" + re.escape(array_name) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};"
    m = re.search(regex, c_source, re.S)
    if not m:
        return []
    # [1.1.b] Normalize whitespace, then split into tokens.
    content = m.group(1).replace("\n", " ").strip()
    values = [v.strip() for v in content.split(",") if v.strip()]
    # [1.1.c] Parse tokens as hex (e.g., 0x1234ABCD).
    out: List[int] = []
    for v in values:
        # Accept formats like 0x..., decimal, or macro-like constants if any slipped in.
        v_clean = v
        # Drop possible trailing comments.
        v_clean = v_clean.split("/*")[0].split("//")[0].strip()
        if not v_clean:
            continue
        # Try hex first
        if v_clean.lower().startswith("0x"):
            out.append(int(v_clean, 16))
        else:
            # Fallback to decimal
            out.append(int(v_clean, 10))
    return out


# [2] ---------- IA4 decoding + tile deswizzle ----------------------------------

def ia4_nibbles_from_bytes(tiled_data: bytes) -> List[int]:
    """
    [2.1] Expand packed bytes to a list of 4-bit values (one per pixel).
          Byte b = [hi:pixel0 (4b)] [lo:pixel1 (4b)].
    """
    nibbles: List[int] = []
    for b in tiled_data:
        nibbles.append((b >> 4) & 0xF)
        nibbles.append(b & 0xF)
    return nibbles


def deswizzle_simple_tiled(tiled_data: bytes, width: int, height: int) -> bytes:
    """
    [2.2] Convert 8×8-tiled IA4 data → linear row-major bytes (two pixels per byte).

    Assumptions:
      • Tiles are 8×8 pixels.
      • Tiles are stored row-major across the image (left→right, then top→bottom).
      • Each pixel is a 4-bit nibble. We repack back to bytes after linearization.

    Returned bytes are packed as: hi-nibble = pixel0, lo-nibble = pixel1.
    """
    # [2.2.a] Split to nibbles for easier addressing.
    tiled_nibbles = ia4_nibbles_from_bytes(tiled_data)

    tile_w, tile_h = 8, 8
    tiles_per_row = math.ceil(width / tile_w)

    total_pixels = width * height
    linear_pixels_4b = [0] * total_pixels

    # [2.2.b] For each output pixel (x, y) compute its source (tile-major) index.
    for y in range(height):
        for x in range(width):
            tile_x, in_tile_x = divmod(x, tile_w)
            tile_y, in_tile_y = divmod(y, tile_h)

            tile_index = tile_y * tiles_per_row + tile_x
            in_tile_offset = in_tile_y * tile_w + in_tile_x

            src_idx = tile_index * (tile_w * tile_h) + in_tile_offset
            dst_idx = y * width + x

            if src_idx < len(tiled_nibbles):
                linear_pixels_4b[dst_idx] = tiled_nibbles[src_idx]
            else:
                # [2.2.c] Safety: out-of-range reads clamp to 0 (transparent black).
                linear_pixels_4b[dst_idx] = 0

    # [2.2.d] Repack 4-bit pixels back to bytes.
    out = bytearray()
    for i in range(0, len(linear_pixels_4b), 2):
        p0 = linear_pixels_4b[i]
        p1 = linear_pixels_4b[i + 1] if i + 1 < len(linear_pixels_4b) else 0
        out.append(((p0 & 0xF) << 4) | (p1 & 0xF))
    return bytes(out)


def decode_ia4_to_rgba(data: bytes, width: int, height: int) -> Image.Image:
    """
    [2.3] Decode IA4 linear bytes → RGBA8 Pillow image.

    IA4 nibble layout (per pixel):
        bits 3..1 = intensity (I: 0..7)
        bit  0    = alpha     (A: 0 or 1)

    RGB = I expanded to 0..255, A = 0 or 255.
    We use exact scaling: I8 = (I * 255) // 7
    """
    # [2.3.a] Unpack to nibbles first (two pixels per byte).
    nibbles = ia4_nibbles_from_bytes(data)
    num_pixels = width * height
    # [2.3.b] Prepare a flat RGBA buffer.
    out = bytearray(num_pixels * 4)

    for i in range(num_pixels):
        nib = nibbles[i] if i < len(nibbles) else 0
        intensity_3 = (nib >> 1) & 0x07
        alpha_1 = nib & 0x01

        # [2.3.c] Exact 3-bit → 8-bit expansion.
        i8 = (intensity_3 * 255) // 7
        a8 = 255 if alpha_1 else 0

        j = i * 4
        out[j:j + 4] = bytes((i8, i8, i8, a8))

    return Image.frombytes("RGBA", (width, height), bytes(out))


# [3] ---------- Glyph model & C-table parsing ----------------------------------

@dataclass
class Glyph:
    """[3.1] One glyph entry as defined by the 6×u32 C table (24 bytes)."""
    codepoint: int      # 0x00
    v_offset: int       # 0x04
    height: int         # 0x08
    width: int          # 0x0C
    flags: int          # 0x10 (unknown/advance/flags)
    data_offset: int    # 0x14 (byte offset from base; becomes a pointer at runtime)


def parse_glyphs_from_chartable(chartable: List[int]) -> List[Glyph]:
    """
    [3.2] Convert raw chartable u32 list → list[Glyph].
    Each glyph occupies 6 u32 entries: see struct above.
    """
    glyphs: List[Glyph] = []
    for i in range(0, len(chartable), 6):
        chunk = chartable[i:i + 6]
        if len(chunk) < 6:
            continue
        g = Glyph(
            codepoint=chunk[0],
            v_offset=chunk[1],
            height=chunk[2],
            width=chunk[3],
            flags=chunk[4],
            data_offset=chunk[5],
        )
        glyphs.append(g)
    return glyphs


# [4] ---------- High-level extraction pipeline ---------------------------------

def load_font_arrays_from_c(c_path: str, font_name: str) -> Tuple[List[Glyph], bytes]:
    """
    [4.1] Parse the two arrays from `<font_name>.c`:
          `<font_name>_fontchartable`  (glyph metadata)
          `<font_name>_fontbytes`      (raw IA4 bytes packed as u32 words)

    Returns:
        glyphs: parsed list of Glyph entries
        font_bytes: raw bytes stream (big-endian reassembled from u32 words)
    """
    with open(c_path, "r", encoding="utf-8", errors="ignore") as f:
        c_src = f.read()

    chartable_name = f"{font_name}_fontchartable"
    fontbytes_name = f"{font_name}_fontbytes"

    # [4.1.a] Parse C arrays.
    chartable_u32 = parse_c_array(c_src, chartable_name)
    fontbytes_u32 = parse_c_array(c_src, fontbytes_name)

    if not chartable_u32:
        raise RuntimeError(f"Array '{chartable_name}' not found or empty in {c_path}")
    if not fontbytes_u32:
        raise RuntimeError(f"Array '{fontbytes_name}' not found or empty in {c_path}")

    # [4.1.b] Reassemble raw bytes from big-endian u32 words.
    font_bytes = b"".join(v.to_bytes(4, "big") for v in fontbytes_u32)

    # [4.1.c] Interpret chartable as Glyphs.
    glyphs = parse_glyphs_from_chartable(chartable_u32)

    return glyphs, font_bytes


def compute_base_offset(glyphs: List[Glyph]) -> int:
    """
    [4.2] Determine a "base" data offset.
         Convention: use the minimum `data_offset` among glyphs with width>0.
    """
    valid = [g.data_offset for g in glyphs if g.width > 0 and g.height > 0]
    if not valid:
        raise RuntimeError("No valid glyphs with non-zero dimensions found.")
    return min(valid)


def extract_glyph_bitmap(g: Glyph, base_offset: int, font_bytes: bytes) -> Image.Image:
    """
    [4.3] Read, deswizzle and decode one glyph image.

    Steps:
      [4.3.1] Pad width/height up to 8 for tile alignment to compute byte span.
      [4.3.2] Slice glyph's tiled bytes from the big blob using (g.data_offset - base).
      [4.3.3] Deswizzle 8×8 tiles → linear IA4 stream.
      [4.3.4] Decode IA4 → RGBA8.
      [4.3.5] Crop back to (g.width, g.height) (top-left).
    """
    if g.width == 0 or g.height == 0:
        # [4.3.a] Empty glyph (e.g., space)
        return Image.new("RGBA", (max(1, g.width), max(1, g.height)), (0, 0, 0, 0))

    padded_w = math.ceil(g.width / 8) * 8
    padded_h = math.ceil(g.height / 8) * 8
    tiled_bytes_len = (padded_w * padded_h) // 2  # 2 pixels per byte (IA4)

    rel = g.data_offset - base_offset
    if rel < 0:
        raise ValueError(f"Glyph {g.codepoint} has negative relative offset.")

    # [4.3.b] Slice from the font blob; clamp if the source is short.
    end = min(rel + tiled_bytes_len, len(font_bytes))
    tiled_slice = font_bytes[rel:end]
    if len(tiled_slice) < tiled_bytes_len:
        # Pad with 0 if needed to avoid index errors.
        tiled_slice = tiled_slice + bytes(tiled_bytes_len - len(tiled_slice))

    # [4.3.c] Deswizzle and decode.
    linear_ia4 = deswizzle_simple_tiled(tiled_slice, padded_w, padded_h)
    img_full = decode_ia4_to_rgba(linear_ia4, padded_w, padded_h)
    # [4.3.d] Crop to the real glyph bounds (origin = top-left).
    return img_full.crop((0, 0, g.width, g.height))


# [5] ---------- Atlas packing (simple row wrap) --------------------------------

def pack_glyphs_into_atlas(glyphs: List[Glyph], images: List[Image.Image],
                           max_row_width: int = 1024, padding: int = 1) -> Tuple[Image.Image, List[Tuple[int, int]]]:
    """
    [5.1] Very simple atlas packer: upload glyphs left→right until row full, then wrap.

    Returns:
        atlas_image
        placements: list of (x, y) top-left for each glyph image (same order as `images`)
    """
    # [5.1.a] Compute rows.
    placements: List[Tuple[int, int]] = []
    x = y = 0
    row_h = 0
    used_w = 0
    max_w = 0

    # First pass to compute atlas bounds.
    rows: List[List[int]] = [[]]
    for idx, im in enumerate(images):
        w, h = im.size
        if w == 0 or h == 0:
            w = h = 1  # keep something in atlas for placeholder
        if x + w > max_row_width and x > 0:
            # wrap
            rows.append([])
            y += row_h + padding
            x = 0
            row_h = 0
        rows[-1].append(idx)
        placements.append((x, y))
        x += w + padding
        row_h = max(row_h, h)
        used_w = max(used_w, x)
        max_w = max(max_w, used_w)

    atlas_w = min(max_row_width, max_w)
    atlas_h = (placements[-1][1] + row_h) if images else 0
    atlas = Image.new("RGBA", (max(1, atlas_w), max(1, atlas_h)), (0, 0, 0, 0))

    # [5.1.b] Second pass: paste.
    x = y = 0
    row_h = 0
    placements = []
    for row in rows:
        x = 0
        row_h = 0
        for idx in row:
            im = images[idx]
            w, h = im.size
            if w == 0 or h == 0:
                im = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                w, h = im.size
            atlas.paste(im, (x, y))
            placements.append((x, y))
            x += w + padding
            row_h = max(row_h, h)
        y += row_h + padding

    return atlas, placements


# [6] ---------- Debug printing (optional) --------------------------------------

def debug_print_linear_rows(linear_bytes: bytes, w: int, h: int) -> None:
    """
    [6.1] Print a hex nibble dump of the top-left w×h region of linear IA4 pixels.
          Useful to compare against expectations for a given glyph.
    """
    nibbles = ia4_nibbles_from_bytes(linear_bytes)
    for row in range(h):
        start = row * w
        end = start + w
        row_vals = nibbles[start:end]
        print(f"Row {row:02d}: {''.join(f'{p:X}' for p in row_vals)}")


# [7] ---------- Main CLI -------------------------------------------------------

def main() -> None:
    """
    [7.1] Drive the extraction:
         • Read C file
         • Build one-glyph PNG (A)
         • Build full-atlas PNG (optional)
    """
    font_name = "fontBankGothic"  # change if needed
    c_file_path = f"{font_name}.c"

    print(f"[INFO] Reading C source file: {c_file_path}")
    glyphs, font_bytes = load_font_arrays_from_c(c_file_path, font_name)
    print(f"[INFO] Parsed {len(glyphs)} glyph definitions.")

    # [7.1.a] Determine base offset for relative addressing of tiled data.
    base_offset = compute_base_offset(glyphs)
    print(f"[INFO] Base data offset: 0x{base_offset:08X}")

    # [7.1.b] Extract a single example: 'A' (ASCII 65).
    glyph_A: Optional[Glyph] = next((g for g in glyphs if g.codepoint == 65), None)
    if glyph_A is None:
        print("[WARN] Glyph 'A' (65) not found — skipping single-glyph export.")
    else:
        print("[INFO] Generating single character 'A' PNG…")
        g_img = extract_glyph_bitmap(glyph_A, base_offset, font_bytes)

        # (Optional) low-level debug: show the nibbles before IA4 decode.
        padded_w = math.ceil(glyph_A.width / 8) * 8
        padded_h = math.ceil(glyph_A.height / 8) * 8
        tiled_len = (padded_w * padded_h) // 2
        rel = glyph_A.data_offset - base_offset
        linear_ia4 = deswizzle_simple_tiled(font_bytes[rel:rel + tiled_len], padded_w, padded_h)

        print("\n--- Raw 4-bit Linear Pixel Data for 'A' (ID 65) ---")
        debug_print_linear_rows(linear_ia4, glyph_A.width, glyph_A.height)
        print("---------------------------------------------------\n")

        # Compose onto a tiny atlas (here just 64×64).
        atlas = Image.new("RGBA", (max(64, g_img.width), max(64, g_img.height)), (0, 0, 0, 0))
        atlas.paste(g_img, (0, 0))
        out_A = f"{font_name}_A.png"
        atlas.save(out_A)
        print(f"[OK] Saved single glyph: {out_A}")

    # [7.1.c] Build a full atlas for all non-empty glyphs.
    print("[INFO] Building full atlas for all glyphs…")
    non_empty = [g for g in glyphs if g.width > 0 and g.height > 0]
    images = [extract_glyph_bitmap(g, base_offset, font_bytes) for g in non_empty]
    atlas, placements = pack_glyphs_into_atlas(non_empty, images, max_row_width=1024, padding=1)
    out_atlas = f"{font_name}_atlas.png"
    atlas.save(out_atlas)
    print(f"[OK] Saved atlas: {out_atlas}  (glyphs: {len(non_empty)})")


if __name__ == "__main__":
    main()