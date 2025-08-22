import re
from PIL import Image
import os
import math

# ==========================================================
# Function 1: Parse C array from a source file
# ----------------------------------------------------------
# This function finds and extracts an array definition from
# the .c font file. It looks for "u32 <array_name>[] = {...};"
# and converts the hexadecimal values to integers.
# ==========================================================
def parse_c_array(c_source, array_name):
    # Regex to capture array content inside { ... }
    regex = r"u32\s+" + re.escape(array_name) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};"
    match = re.search(regex, c_source, re.S)
    if not match: return []
    content = match.group(1).replace("\n", "").strip()
    values = [v.strip() for v in content.split(',') if v.strip()]
    return [int(v, 16) for v in values]


# ==========================================================
# Function 2: Decode IA4 (Intensity + Alpha, 4 bits per pixel)
# ----------------------------------------------------------
# Each nibble (4 bits) encodes:
#   - bits 1..3: intensity (0-7 range, scaled to 0-255)
#   - bit 0: alpha (1 = visible, 0 = transparent)
#
# This is expanded into a standard 32-bit RGBA pixel buffer
# for Pillow (Python Imaging Library).
# ==========================================================
def decode_ia4_to_rgba(data: bytes, width: int, height: int) -> Image:
    output_pixels = bytearray(width * height * 4)
    numpixels = width * height

    for i in range(numpixels):
        byte_index = i // 2
        if byte_index >= len(data):
            break

        # Extract nibble (4 bits)
        nibble = (data[byte_index] >> 4) & 0x0F if i % 2 == 0 else data[byte_index] & 0x0F

        # Split nibble into intensity and alpha
        intensity_3bit = (nibble >> 1) & 0x07   # 3-bit intensity
        alpha_1bit = nibble & 0x01              # 1-bit alpha

        # Scale to 8-bit
        intensity_8bit = intensity_3bit * 32
        alpha_8bit = alpha_1bit * 255

        # Store RGBA
        pixel_index = i * 4
        output_pixels[pixel_index:pixel_index+4] = [intensity_8bit, intensity_8bit, intensity_8bit, alpha_8bit]

    return Image.frombytes('RGBA', (width, height), bytes(output_pixels))


# ==========================================================
# Function 3: Deswizzle font graphics from N64 tile layout
# ----------------------------------------------------------
# Nintendo 64 graphics are stored in 8x8 tiles (swizzled).
# This function rearranges (deswizzles) them into a normal
# linear order so they can be decoded properly.
# ==========================================================
def deswizzle_simple_tiled(tiled_data: bytes, width: int, height: int) -> bytes:
    # Step 1: expand bytes into individual 4-bit pixels
    tiled_pixels = []
    for byte in tiled_data:
        tiled_pixels.append((byte >> 4) & 0xF)
        tiled_pixels.append(byte & 0xF)

    # Step 2: prepare linear storage
    linear_pixels = [0] * (width * height)
    tile_width, tile_height = 8, 8
    width_in_tiles = math.ceil(width / tile_width)

    # Step 3: iterate through every pixel coordinate
    for y in range(height):
        for x in range(width):
            tile_x, in_tile_x = divmod(x, tile_width)
            tile_y, in_tile_y = divmod(y, tile_height)
            
            tile_index = tile_y * width_in_tiles + tile_x
            in_tile_offset = in_tile_y * tile_width + in_tile_x
            
            swizzled_pixel_offset = tile_index * (tile_width * tile_height) + in_tile_offset
            linear_pixel_offset = y * width + x

            if swizzled_pixel_offset < len(tiled_pixels):
                linear_pixels[linear_pixel_offset] = tiled_pixels[swizzled_pixel_offset]

    # Step 4: pack back into bytes (two pixels per byte)
    linear_bytes = bytearray()
    for i in range(0, len(linear_pixels), 2):
        p1 = linear_pixels[i]
        p2 = linear_pixels[i+1] if i + 1 < len(linear_pixels) else 0
        linear_bytes.append((p1 << 4) | p2)
        
    return bytes(linear_bytes)


# ==========================================================
# MAIN SCRIPT
# ----------------------------------------------------------
# Purpose: Load GoldenEye 007 N64 font data from .c file,
# extract character metadata + graphics, decode them, and
# export as PNG image.
# ==========================================================
def main():
    # Step 1: Define font name + source file
    font_name = "fontBankGothic"
    c_file_path = f"{font_name}.c"
    print(f"Reading C source file: {c_file_path}...")
    with open(c_file_path, 'r') as f: c_source = f.read()

    # Step 2: Parse font arrays from .c
    print("Parsing font data arrays...")
    chartable = parse_c_array(c_source, f"{font_name}_fontchartable")
    fontbytes_u32 = parse_c_array(c_source, f"{font_name}_fontbytes")
    font_byte_array = b''.join(val.to_bytes(4, 'big') for val in fontbytes_u32)

    # ======================================================
    # FONT CHARACTER TABLE STRUCTURE (GoldenEye N64)
    # Each entry in "fontchartable" is 6 words (u32):
    #   0x00: character ID (ASCII code)
    #   0x04: vertical offset (y offset for drawing)
    #   0x08: height (in pixels)
    #   0x0C: width  (in pixels)
    #   0x10: unknown / flags / padding (purpose unclear)
    #   0x14: data offset (points to fontbytes data)
    # ======================================================

    # Step 3: Build list of characters from chartable
    characters = []
    for i in range(0, len(chartable), 6):
        entry = chartable[i:i+6]
        if len(entry) == 6: characters.append({
            'id': entry[0], 'v_offset': entry[1], 'height': entry[2],
            'width': entry[3], 'unknown': entry[4], 'data_offset': entry[5]
        })

    print(f"Found {len(characters)} character definitions.")

    # Step 4: Find base offset (first valid glyph)
    sorted_chars_by_offset = sorted([c for c in characters if c['width']>0], key=lambda c: c['data_offset'])
    if not sorted_chars_by_offset: return
    base_offset = sorted_chars_by_offset[0]['data_offset']
    print(f"Base offset: {base_offset:#08x}")

    # Step 5: Find specific character "A" (ASCII 65)
    char_A_info = next((c for c in characters if c['id'] == 65), None)
    if not char_A_info:
        print("Character 'A' (ID 65) not found in font data.")
        return

    # Step 6: Prepare output image (atlas for preview)
    atlas = Image.new('RGBA', (64, 64), (0, 0, 0, 255))
    x, y, max_h = 0, 0, 0

    print("Generating single character 'A' PNG...")
    char = char_A_info
    w, h = char['width'], char['height']
    if w == 0 or h == 0: 
        print("Character 'A' has zero width or height.")
        return

    # Step 7: Ensure glyph is padded to full 8x8 tiles
    padded_w = math.ceil(w / 8) * 8
    padded_h = math.ceil(h / 8) * 8
    total_tiled_bytes = (padded_w * padded_h) // 2

    # Step 8: Slice out the glyph bitmap from font bytes
    relative_offset_bytes = char['data_offset'] - base_offset
    tiled_data = font_byte_array[relative_offset_bytes : relative_offset_bytes + total_tiled_bytes]

    # Step 9: Deswizzle N64 tile layout into linear order
    linear_data_bytes = deswizzle_simple_tiled(tiled_data, padded_w, padded_h)
    
    # Step 10: Debug print of raw pixel values (4-bit hex dump)
    print("\n--- Raw 4-bit Linear Pixel Data for 'A' (ID 65) ---")
    linear_pixels_4bit = []
    for byte in linear_data_bytes:
        linear_pixels_4bit.append((byte >> 4) & 0xF)
        linear_pixels_4bit.append(byte & 0xF)
    
    for i in range(h):
        row_str = ''.join([f'{p:X}' for p in linear_pixels_4bit[i*w : (i+1)*w]])
        print(f"Row {i:2d}: {row_str}")
    print("--------------------------------------------------")

    # Step 11: Decode IA4 → RGBA image
    full_glyph_img = decode_ia4_to_rgba(linear_data_bytes, padded_w, padded_h)
    final_char_img = full_glyph_img.crop((0, 0, w, h))

    # Step 12: Place glyph into atlas
    atlas.paste(final_char_img, (x, y))

    # Step 13: Save as PNG
    output_filename = f"{font_name}_A.png"
    atlas.save(output_filename)
    print(f"Success! Character 'A' saved to '{output_filename}'")


# Entry point
if __name__ == "__main__":
    main()