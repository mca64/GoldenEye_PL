# -*- coding: utf-8 -*-
"""
Nintendo 64 Font De-swizzler and Atlas Generator

This script analyzes and visualizes font data from Nintendo 64 C source files,
specifically from the game GoldenEye 007. It parses raw, hardware-swizzled
pixel data, de-swizzles it, and generates two composite PNG image files
(a "font atlas") containing all characters from the font set.
"""

# =============================================================================
# Section 1: Imports
# =============================================================================
import re
import os
import math
import statistics
from PIL import Image


# =============================================================================
# Section 2: Core Image Processing and De-swizzling Functions
# =============================================================================

def _create_grayscale_palette():
    """
    2.1. Creates a 256-color grayscale palette for Color-Indexed (CI) images.

    N64 CI textures use an index to look up a color in a palette. This function
    creates a simple linear grayscale palette where index 0 is black, 255 is
    white, and all values in between are shades of gray.

    Returns:
        list: A list of 768 integers (R, G, B for each of the 256 colors).
    """
    palette = []
    for i in range(256):
        palette.extend([i, i, i])
    return palette


def _deswizzle_ci8_8x2_microblocks(width, height, src_bytes):
    """
    2.2. De-swizzles raw CI8 pixel data from the N64's 8x2 microblock format.

    N64 textures are not stored linearly. They are arranged in small blocks to
    optimize for the hardware's texture mapping unit. This function reverses
    that process.

    Args:
        width (int): The padded width of the image canvas (must be a multiple of 8).
        height (int): The padded height of the image canvas (must be a multiple of 2).
        src_bytes (bytes): The raw, swizzled byte data for the texture.

    Returns:
        list: A 1D list of pixel values in linear (left-to-right, top-to-bottom) order.
    """
    # 2.2.1. Define the dimensions of the hardware microblocks.
    block_width, block_height = 8, 2

    # 2.2.2. The calling function provides already-padded dimensions.
    padded_width = width
    padded_height = height
    total_pixels_needed = padded_width * padded_height

    # 2.2.3. Ensure the source data is not smaller than the canvas. If it is,
    # pad it with null bytes to prevent read errors.
    if len(src_bytes) < total_pixels_needed:
        src_bytes += b'\x00' * (total_pixels_needed - len(src_bytes))

    # 2.2.4. Create the destination canvas for the de-swizzled pixels.
    pixel_map_deswizzled = [0] * total_pixels_needed
    pixels_per_block = block_width * block_height  # 16 pixels per 8x2 block
    blocks_per_row = padded_width // block_width

    # 2.2.5. Iterate through each block in the order it appears in the source data stream.
    num_blocks = blocks_per_row * (padded_height // block_height)
    for block_index in range(num_blocks):
        block_start_offset = block_index * pixels_per_block

        # 2.2.6. Calculate the block's 2D coordinate on the final image grid.
        block_y = block_index // blocks_per_row
        block_x = block_index % blocks_per_row

        # 2.2.7. Iterate through each pixel within the 8x2 microblock.
        for y_in_block in range(block_height):
            for x_in_block in range(block_width):
                # 2.2.8. Get the pixel from the source data stream.
                src_pixel_offset = block_start_offset + (y_in_block * block_width + x_in_block)
                pixel_value = src_bytes[src_pixel_offset] if src_pixel_offset < len(src_bytes) else 0
                
                # 2.2.9. Calculate the final (x, y) coordinate in the linear output image.
                final_x = block_x * block_width + x_in_block
                final_y = block_y * block_height + y_in_block
                
                # 2.2.10. Place the pixel in the correct position in the 1D destination list.
                dest_idx = final_y * padded_width + final_x
                if dest_idx < total_pixels_needed:
                    pixel_map_deswizzled[dest_idx] = pixel_value
    
    return pixel_map_deswizzled

def _generate_char_images(width, height, char_fontbytes_u32, palette):
    """
    2.3. Generates grayscale and binary PIL Image objects for a single character.

    This function takes the raw data for one character, de-swizzles it, and creates
    two in-memory image representations without saving them to disk.

    Args:
        width (int): The logical (unpadded) width of the character.
        height (int): The logical (unpadded) height of the character.
        char_fontbytes_u32 (list): The raw u32 data values for this character.
        palette (list): The grayscale palette to use for the CI8 image.

    Returns:
        tuple: A tuple containing (grayscale_image, binary_image), or (None, None) on error.
    """
    # 2.3.1. Basic validation.
    if len(char_fontbytes_u32) < 2:
        return None, None

    # 2.3.2. Convert the list of u32 integers into a single byte string.
    full_data_bytes = b''.join([val.to_bytes(4, 'big') for val in char_fontbytes_u32])
    
    # 2.3.3. Find and skip the standard 8-byte texture header (0xB8000000 0x00000000).
    header = b'\xb8\x00\x00\x00\x00\x00\x00\x00'
    header_start_index = full_data_bytes.find(header)
    start_offset = header_start_index + len(header) if header_start_index != -1 else 8
    raw_pixel_bytes = full_data_bytes[start_offset:]
    
    # 2.3.4. Calculate padded dimensions required for de-swizzling.
    # Width must be a multiple of 8; height must be a multiple of 2.
    padded_width = math.ceil(width / 8) * 8
    padded_height = math.ceil(height / 2) * 2

    # 2.3.5. De-swizzle the raw pixel data onto a padded canvas.
    pixel_map_deswizzled = _deswizzle_ci8_8x2_microblocks(padded_width, padded_height, raw_pixel_bytes)

    # 2.3.6. Create the grayscale image.
    # First, create the full padded image, then crop it to the true logical dimensions.
    img_padded = Image.new('P', (padded_width, padded_height))
    img_padded.putpalette(palette)
    img_padded.putdata(pixel_map_deswizzled)
    grayscale_img = img_padded.crop((0, 0, width, height))

    # 2.3.7. Create the binarized (black & white) image.
    # This is done by finding the median of non-black pixels and using it as a threshold.
    non_zero_pixels = [p for p in pixel_map_deswizzled if p > 0]
    if not non_zero_pixels:
        binary_img = Image.new('L', (width, height), 0) # All black
    else:
        median_value = statistics.median(non_zero_pixels)
        binary_pixels = [255 if p > median_value else 0 for p in pixel_map_deswizzled]
        binary_img_padded = Image.new('L', (padded_width, padded_height))
        binary_img_padded.putdata(binary_pixels)
        binary_img = binary_img_padded.crop((0, 0, width, height))

    return grayscale_img, binary_img


# =============================================================================
# Section 3: Font Atlas Generation
# =============================================================================

def _stitch_and_save_font_atlas(char_images, base_filename):
    """
    3.1. Stitches a list of individual character images into a single font atlas.

    This function takes all the generated character images, arranges them
    horizontally, and saves the result as two final PNG files.

    Args:
        char_images (list): A list of dictionaries, where each contains image objects.
        base_filename (str): The base name for the output files (e.g., "fontBankGothic").
    """
    if not char_images:
        print("No images to stitch.")
        return

    # 3.1.1. Separate the images from the input data structure.
    grayscale_images = [item['grayscale'] for item in char_images]
    binary_images = [item['binary'] for item in char_images]

    # 3.1.2. Calculate the dimensions of the final composite image (the atlas).
    # Total width is the sum of all character widths.
    # Height is the height of the tallest character in the set.
    total_width = sum(img.width for img in grayscale_images)
    max_height = max(img.height for img in grayscale_images)

    # 3.1.3. Create the grayscale font atlas.
    stitched_grayscale = Image.new('L', (total_width, max_height), 0)
    current_x = 0
    for img in grayscale_images:
        stitched_grayscale.paste(img, (current_x, 0))
        current_x += img.width
    
    grayscale_filename = f"{base_filename}_grayscale.png"
    stitched_grayscale.save(grayscale_filename)
    print(f"Successfully saved stitched grayscale atlas: {grayscale_filename}")

    # 3.1.4. Create the binary font atlas.
    stitched_binary = Image.new('L', (total_width, max_height), 0)
    current_x = 0
    for img in binary_images:
        stitched_binary.paste(img, (current_x, 0))
        current_x += img.width

    binary_filename = f"{base_filename}_binary.png"
    stitched_binary.save(binary_filename)
    print(f"Successfully saved stitched binary atlas: {binary_filename}")


# =============================================================================
# Section 4: C File Parsing Logic
# =============================================================================

def _parse_c_font_file(file_path):
    """
    4.1. Parses the C source file to extract font data tables.

    This function reads the specified C file and uses regular expressions and
    string searching to find and extract the `fontchartable` (character metadata)
    and `fontbytes` (raw pixel data) arrays.

    Args:
        file_path (str): The full path to the C source file.

    Returns:
        tuple: A tuple containing (char_table_data, font_pixel_data, base_offset, total_pixels).
               Returns (None, None, 0, 0) on failure.
    """
    try:
        with open(file_path, 'r') as f:
            file_content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        return None, None, 0, 0

    # 4.1.1. Define markers to locate the character metadata array.
    start_marker = "u32 fontBankGothic_fontchartable[] = \n{"
    end_marker = "};\n\nu32 fontBankGothic_fontbytes[] = "
    start_index = file_content.find(start_marker)
    end_index = file_content.find(end_marker)

    if start_index == -1 or end_index == -1:
        print("Error: Could not find fontBankGothic_fontchartable array.")
        return None, None, 0, 0

    # 4.1.2. Extract and parse the hexadecimal values from the metadata table.
    start_index += len(start_marker)
    chartable_data_str = file_content[start_index:end_index].strip()
    hex_values_str = re.findall(r'0x[0-9a-fA-F]+', chartable_data_str)
    char_table_data = [int(h, 16) for h in hex_values_str]

    # 4.1.3. Define markers to locate the raw pixel data array.
    start_marker_bytes = "u32 fontBankGothic_fontbytes[] = \n{"
    end_marker_bytes = "};"
    start_index_bytes = file_content.find(start_marker_bytes, end_index)
    end_index_bytes = file_content.find(end_marker_bytes, start_index_bytes)

    if start_index_bytes == -1 or end_index_bytes == -1:
        print("Error: Could not find fontBankGothic_fontbytes array.")
        return None, None, 0, 0

    # 4.1.4. Extract and parse the hexadecimal values from the pixel data array.
    start_index_bytes += len(start_marker_bytes)
    fontbytes_data_str = file_content[start_index_bytes:end_index_bytes].strip()
    fontbytes_hex_values_str = re.findall(r'0x[0-9a-fA-F]+', fontbytes_data_str)
    font_pixel_data = [int(h, 16) for h in fontbytes_hex_values_str]

    # 4.1.5. Determine the base offset. The offsets in the char table are absolute,
    # so we find the first character's offset to make them relative.
    base_offset = 0
    for i in range(0, len(char_table_data), 6):
        if (i + 5) < len(char_table_data):
            if char_table_data[i + 3] > 0: # Find first char with width > 0
                base_offset = char_table_data[i + 5]
                break
    
    # 4.1.6. Calculate the total logical pixels for a summary message.
    total_pixels = sum(char_table_data[i + 2] * char_table_data[i + 3] for i in range(0, len(char_table_data), 6) if (i + 5) < len(char_table_data))

    return char_table_data, font_pixel_data, base_offset, total_pixels


# =============================================================================
# Section 5: Main Execution Block
# =============================================================================

def main():
    """5.1. Main function to orchestrate the entire process."""
    # 5.1.1. Define the path to the input C file.
    font_file_path = "/home/mca64/007/fontBankGothic.c"
    
    # 5.1.2. Extract the base name of the file for naming the output atlases.
    base_name = os.path.splitext(os.path.basename(font_file_path))[0]

    # 5.1.3. Parse the C file to get character and pixel data.
    char_table_data, font_pixel_data, base_offset, total_pixels = _parse_c_font_file(font_file_path)

    if char_table_data is None:
        print("Exiting due to parsing error.")
        return

    print(f"Found {len(char_table_data) // 6} characters. Total logical pixels: {total_pixels}")

    # 5.1.4. Define constants for accessing the character metadata.
    # Each entry in the character table is 6 u32 values.
    ENTRY_SIZE = 6
    HEIGHT_IDX = 2
    WIDTH_IDX = 3
    IMAGE_OFFSET_IDX = 5

    # 5.1.5. Initialize helper objects.
    grayscale_palette = _create_grayscale_palette()
    all_char_images = []

    # 5.1.6. Loop through every character defined in the metadata table.
    for i in range(0, len(char_table_data), ENTRY_SIZE):
        if (i + ENTRY_SIZE - 1) < len(char_table_data):
            height = char_table_data[i + HEIGHT_IDX]
            width = char_table_data[i + WIDTH_IDX]
            image_offset = char_table_data[i + IMAGE_OFFSET_IDX]
            
            # 5.1.7. Skip characters with no dimensions (e.g., space character).
            if width == 0 or height == 0:
                continue

            # 5.1.8. Calculate the start and end slice for this character's pixel data.
            start_index_u32 = (image_offset - base_offset) // 4
            
            data_length_bytes = 0
            if (i + ENTRY_SIZE) < len(char_table_data):
                # Calculate data length from the difference to the next character's offset.
                next_image_offset = char_table_data[i + ENTRY_SIZE + IMAGE_OFFSET_IDX]
                data_length_bytes = next_image_offset - image_offset
            else:
                # For the last character, estimate data length based on padded dimensions.
                padded_w = math.ceil(width / 8) * 8
                padded_h = math.ceil(height / 2) * 2
                data_length_bytes = padded_w * padded_h

            num_u32_entries = data_length_bytes // 4
            end_index_u32 = start_index_u32 + num_u32_entries
            
            if end_index_u32 > len(font_pixel_data):
                print(f"Error: Data for character {i//ENTRY_SIZE} exceeds fontbytes array. Skipping.")
                continue

            char_fontbytes_u32 = font_pixel_data[start_index_u32 : end_index_u32]
            
            # 5.1.9. Generate the in-memory images for the current character.
            grayscale_img, binary_img = _generate_char_images(width, height, char_fontbytes_u32, grayscale_palette)
            
            # 5.1.10. If images were created successfully, add them to the list for stitching.
            if grayscale_img and binary_img:
                all_char_images.append({
                    'grayscale': grayscale_img,
                    'binary': binary_img
                })

    # 5.1.11. After processing all characters, stitch the collected images into the final atlases.
    if all_char_images:
        _stitch_and_save_font_atlas(all_char_images, base_name)

if __name__ == "__main__":
    # 5.2. Entry point of the script.
    main()
