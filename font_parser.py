#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# glyphs_64_67_binary_grayscale.py 
#
# This script generates a preview of glyphs 64 and 67 (from fontBankGothic.c) using the 8×2 microblock layout.
# It produces two visualizations for each glyph:
#   - Binary (black and white) view
#   - Grayscale view
#
# The output is saved as a single PNG image containing both glyphs stacked vertically.

import struct
import math
from PIL import Image

# ==========================
# CONFIGURATION
# ==========================
SCALE = 20  # Scaling factor for enlarging the glyphs in the output image
OUT_PATH = "glyphs_64_67_binary_grayscale.png"  # Output file path

# Debug glyph data (u32 big-endian format)
# Each glyph entry contains:
#   - width (w)
#   - height (h)
#   - u32: list of 32-bit unsigned integers representing raw glyph data
GLYPHS = {
    64: {
        "w": 7, "h": 7,
        "u32": [
            0x48484848, 0xB8000000, 0x00000000, 0x00000B1F, 0x04000000,
            0x00006AC1, 0x49000000, 0x0033A975, 0xAA1A0000, 0x099A8829,
            0xAC7C0000, 0x64CAA29A, 0xACCA4343, 0xA2500D12, 0x0F759292, 0x1B020000
        ]
    },
    67: {
        "w": 6, "h": 7,
        "u32": [
            0x1F0B0B0B, 0xB8000000, 0x00000000, 0x26292B23, 0x0B000000,
            0xCFA7A1AA, 0x9F343434, 0xD531101D, 0x8BABABAB, 0xD1260000,
            0x4BC9C9C9, 0xD5381827, 0x8FA2A2A2, 0xC7A7A1A2, 0x8B282828, 0x1F222214
        ]
    }
}

# ==========================
# HELPER FUNCTIONS
# ==========================

def u32list_to_bytes(u32list):
    """
    Convert a list of 32-bit unsigned integers (big-endian) into a bytes object.
    Each integer is packed into 4 bytes in big-endian order.
    """
    b = bytearray()
    for v in u32list:
        b.extend(struct.pack(">I", v))
    return bytes(b)

def find_header_start(raw):
    """
    Locate the start of the actual glyph pixel data by skipping the header.
    The header pattern is:
        0xB8000000 0x00000000
    Returns the index where pixel data begins.
    """
    hdr = struct.pack(">I", 0xB8000000) + struct.pack(">I", 0x00000000)
    idx = raw.find(hdr)
    if idx != -1:
        return idx + len(hdr)
    return 8 if len(raw) > 8 else 0

def deswizzle_8x2(stream_bytes, w, h):
    """
    Deswizzle pixel data stored in 8×2 microblocks.
    This layout stores pixels in blocks of 8 columns × 2 rows (16 bytes per block).
    The function reconstructs the original 2D pixel array from the swizzled byte stream.

    Parameters:
        stream_bytes (bytes): Raw pixel data after header removal.
        w (int): Width of the glyph in pixels.
        h (int): Height of the glyph in pixels.

    Returns:
        list[list[int]]: 2D array of pixel intensity values (0–255).
    """
    blocks_x = math.ceil(w / 8)  # Number of horizontal blocks
    blocks_y = math.ceil(h / 2)  # Number of vertical blocks
    need = blocks_x * blocks_y * 16  # Total bytes needed for all blocks

    # Pad with zeros if the stream is shorter than expected
    if len(stream_bytes) < need:
        stream_bytes = stream_bytes + b'\x00' * (need - len(stream_bytes))

    # Initialize empty pixel grid
    out = [[0] * w for _ in range(h)]
    idx = 0  # Index in the byte stream

    for by in range(blocks_y):
        for bx in range(blocks_x):
            for yy in range(2):       # Rows within the block
                for xx in range(8):   # Columns within the block
                    x = bx * 8 + xx
                    y = by * 2 + yy
                    if x < w and y < h:
                        out[y][x] = stream_bytes[idx + yy * 8 + xx]
            idx += 16  # Move to the next block
    return out

def make_binary_image(pixels, scale=20):
    """
    Convert a 2D pixel array into a black-and-white (binary) PIL image.
    Any non-zero pixel value is treated as black (0), zero is white (255).
    The image is then scaled up for visibility.
    """
    h = len(pixels)
    w = len(pixels[0])
    img = Image.new("L", (w, h), color=255)  # "L" mode = 8-bit grayscale
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), 0 if pixels[y][x] else 255)
    return img.resize((w * scale, h * scale), Image.NEAREST)

def make_grayscale_image(pixels, scale=20):
    """
    Convert a 2D pixel array into a grayscale PIL image.
    Pixel values are inverted (255 - value) for better visibility.
    The image is then scaled up for visibility.
    """
    h = len(pixels)
    w = len(pixels[0])
    img = Image.new("L", (w, h), color=255)
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), 255 - pixels[y][x])
    return img.resize((w * scale, h * scale), Image.NEAREST)

# ==========================
# MAIN SCRIPT
# ==========================

images = []
for gid in (64, 67):
    info = GLYPHS[gid]

    # Convert u32 list to raw bytes
    raw = u32list_to_bytes(info["u32"])

    # Find where the actual pixel data starts (skip header)
    start = find_header_start(raw)
    stream = raw[start:]

    # Deswizzle using only the 8×2 method
    pixels_8x2 = deswizzle_8x2(stream, info["w"], info["h"])

    # Create binary and grayscale images
    bin_img_8x2 = make_binary_image(pixels_8x2, SCALE)
    gray_img_8x2 = make_grayscale_image(pixels_8x2, SCALE)

    # Combine binary and grayscale images side-by-side
    total_w = bin_img_8x2.width * 2 + SCALE // 2
    combined = Image.new("L", (total_w, bin_img_8x2.height), color=255)

    offset = 0
    for img in [bin_img_8x2, gray_img_8x2]:
        combined.paste(img, (offset, 0))
        offset += img.width + SCALE // 2

    images.append(combined)

# Stack glyphs vertically with a gap
gap = SCALE // 2
total_h = sum(img.height for img in images) + gap * (len(images) - 1)
max_w = max(img.width for img in images)
atlas = Image.new("L", (max_w, total_h), color=255)

y = 0
for img in images:
    atlas.paste(img, (0, y))
    y += img.height + gap

# Save the final combined image
atlas.save(OUT_PATH)
print(f"Comparison atlas saved: {OUT_PATH}")
