import re
from PIL import Image
import math


# ======================================================================
# (1) Parse a C array from source code
# ----------------------------------------------------------------------
# In the GoldenEye decompilation, fonts are stored in `.c` files as
# arrays of 32-bit integers (u32). This function extracts those arrays.
#
# Example C snippet:
#   u32 fontBankGothic_fontbytes[] = {
#       0x12345678, 0x9ABCDEF0, ...
#   };
#
# This function returns a list of integers from the array.
# ======================================================================
def parse_c_array(c_source, array_name):
    regex = r"u32\s+" + re.escape(array_name) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};"
    match = re.search(regex, c_source, re.S)
    if not match:
        return []
    content = match.group(1).replace("\n", "").strip()
    values = [v.strip() for v in content.split(',') if v.strip()]
    return [int(v, 16) for v in values]


# ======================================================================
# (2) Decode IA4 format (N64 texture format) into RGBA pixels
# ----------------------------------------------------------------------
# IA4 = Intensity + Alpha, 4 bits total per pixel
# - 3 bits store grayscale intensity (0-7)
# - 1 bit stores alpha transparency (0 or 1)
#
# Each byte contains 2 pixels:
#   - High nibble (upper 4 bits) = first pixel
#   - Low nibble (lower 4 bits) = second pixel
#
# Intensity is expanded from 3 bits → 8 bits
# Alpha is expanded from 1 bit → 8 bits (0 or 255)
# ======================================================================
def decode_ia4_to_rgba(data: bytes, width: int, height: int) -> Image:
    output_pixels = bytearray(width * height * 4)  # 4 bytes per pixel (RGBA)
    numpixels = width * height

    for i in range(numpixels):
        byte_index = i // 2  # 2 pixels per byte
        if byte_index >= len(data):
            break

        # Select nibble (upper 4 bits for even pixel, lower 4 bits for odd pixel)
        nibble = (data[byte_index] >> 4) & 0x0F if i % 2 == 0 else data[byte_index] & 0x0F

        # Extract intensity (3 bits) and alpha (1 bit)
        intensity_3bit = (nibble >> 1) & 0x07
        alpha_1bit = nibble & 0x01

        # Scale values up to full 8-bit range
        intensity_8bit = intensity_3bit * 32       # 0–224 in steps of 32
        alpha_8bit = alpha_1bit * 255             # 0 or 255

        # Write RGBA values
        pixel_index = i * 4
        output_pixels[pixel_index:pixel_index+4] = [
            intensity_8bit, intensity_8bit, intensity_8bit, alpha_8bit
        ]

    return Image.frombytes('RGBA', (width, height), bytes(output_pixels))


# ======================================================================
# (3) Deswizzling N64 font tiles
# ----------------------------------------------------------------------
# The font data is stored in **8x8 pixel tiles**, packed in a swizzled
# (tiled) order to optimize for the RDP (N64 GPU).
#
# This function:
#   - Expands bytes into 4-bit pixels (nibbles)
#   - Reorders pixels from tiled order → linear order
#   - Packs back into bytes (2 pixels per byte)
#
# Without deswizzling, the image would look scrambled.
# ======================================================================
def deswizzle_simple_tiled(tiled_data: bytes, width: int, height: int) -> bytes:
    # Expand bytes into 4-bit pixels
    tiled_pixels = []
    for byte in tiled_data:
        tiled_pixels.append((byte >> 4) & 0xF)  # high nibble
        tiled_pixels.append(byte & 0xF)         # low nibble

    linear_pixels = [0] * (width * height)
    tile_width, tile_height = 8, 8
    width_in_tiles = math.ceil(width / tile_width)

    # Reconstruct pixels row by row
    for y in range(height):
        for x in range(width):
            # Locate tile and pixel inside tile
            tile_x, in_tile_x = divmod(x, tile_width)
            tile_y, in_tile_y = divmod(y, tile_height)
            
            tile_index = tile_y * width_in_tiles + tile_x
            in_tile_offset = in_tile_y * tile_width + in_tile_x
            
            swizzled_pixel_offset = tile_index * (tile_width * tile_height) + in_tile_offset
            linear_pixel_offset = y * width + x

            if swizzled_pixel_offset < len(tiled_pixels):
                linear_pixels[linear_pixel_offset] = tiled_pixels[swizzled_pixel_offset]

    # Pack back into bytes (2 pixels per byte)
    linear_bytes = bytearray()
    for i in range(0, len(linear_pixels), 2):
        p1 = linear_pixels[i]
        p2 = linear_pixels[i+1] if i + 1 < len(linear_pixels) else 0
        linear_bytes.append((p1 << 4) | p2)
        
    return bytes(linear_bytes)


# ======================================================================
# (4) Main workflow
# ----------------------------------------------------------------------
# Steps:
#   1. Load the C source file containing font data
#   2. Parse metadata (chartable) and font texture data (fontbytes)
#   3. Identify the character 'A' (ASCII ID = 65)
#   4. Extract and deswizzle its tile data
#   5. Decode IA4 → RGBA image
#   6. Save as PNG
# ======================================================================
def main():
    font_name = "fontBankGothic"   # Font array name inside the .c file
    c_file_path = f"{font_name}.c"
    print(f"Reading C source file: {c_file_path}...")
    
    with open(c_file_path, 'r') as f:
        c_source = f.read()

    print("Parsing font data arrays...")
    chartable = parse_c_array(c_source, f"{font_name}_fontchartable")
    fontbytes_u32 = parse_c_array(c_source, f"{font_name}_fontbytes")

    # Convert list of 32-bit integers into raw byte stream
    font_byte_array = b''.join(val.to_bytes(4, 'big') for val in fontbytes_u32)

    # (5) Reconstruct list of characters
    characters = []
    for i in range(0, len(chartable), 6):
        entry = chartable[i:i+6]
        if len(entry) == 6:
            characters.append({
                'id': entry[0],        # Character ID (usually ASCII code)
                'v_offset': entry[1],  # Vertical offset for placement
                'height': entry[2],    # Glyph height
                'width': entry[3],     # Glyph width
                'unknown': entry[4],   # Unused / unknown field
                'data_offset': entry[5]# Offset into fontbytes
            })

    print(f"Found {len(characters)} character definitions.")

    # (6) Compute base offset (lowest offset in the font data)
    sorted_chars_by_offset = sorted(
        [c for c in characters if c['width'] > 0],
        key=lambda c: c['data_offset']
    )
    if not sorted_chars_by_offset:
        return
    
    base_offset = sorted_chars_by_offset[0]['data_offset']
    print(f"Base offset: {base_offset:#08x}")

    # (7) Extract the character 'A' (ASCII ID 65)
    char_A_info = next((c for c in characters if c['id'] == 65), None)
    if not char_A_info:
        print("Character 'A' (ID 65) not found in font data.")
        return

    atlas = Image.new('RGBA', (64, 64), (0, 0, 0, 255))  # debug atlas
    x, y = 0, 0

    print("Generating single character 'A' PNG...")
    char = char_A_info
    w, h = char['width'], char['height']
    if w == 0 or h == 0: 
        print("Character 'A' has zero width or height.")
        return

    # (8) Align to full 8x8 tiles
    padded_w = math.ceil(w / 8) * 8
    padded_h = math.ceil(h / 8) * 8
    total_tiled_bytes = (padded_w * padded_h) // 2

    # (9) Extract raw font bytes for 'A'
    relative_offset_bytes = char['data_offset'] - base_offset
    tiled_data = font_byte_array[
        relative_offset_bytes : relative_offset_bytes + total_tiled_bytes
    ]

    # (10) Deswizzle to linear pixels
    linear_data_bytes = deswizzle_simple_tiled(tiled_data, padded_w, padded_h)
    
    # (11) Debug print of 4-bit pixel values
    print("\n--- Raw 4-bit Linear Pixel Data for 'A' (ID 65) ---")
    linear_pixels_4bit = []
    for byte in linear_data_bytes:
        linear_pixels_4bit.append((byte >> 4) & 0xF)
        linear_pixels_4bit.append(byte & 0xF)
    
    for i in range(h):
        row_str = ''.join([f'{p:X}' for p in linear_pixels_4bit[i*w : (i+1)*w]])
        print(f"Row {i:2d}: {row_str}")
    print("--------------------------------------------------")

    # (12) Decode IA4 → RGBA
    full_glyph_img = decode_ia4_to_rgba(linear_data_bytes, padded_w, padded_h)

    # Crop to real size (remove padding)
    final_char_img = full_glyph_img.crop((0, 0, w, h))

    # Paste into atlas (for testing)
    atlas.paste(final_char_img, (x, y))

    # (13) Save as PNG
    output_filename = f"{font_name}_A.png"
    atlas.save(output_filename)
    print(f"Success! Character 'A' saved to '{output_filename}'")


if __name__ == "__main__":
    main()