# 🇵🇱 GoldenEye 007 – Polish Translation Project

Welcome to the official repository for the Polish language translation of *GoldenEye 007*, the legendary first-person shooter originally released for the Nintendo 64. 
This fan-driven project aims to localize the game for Polish-speaking players while preserving its iconic style and gameplay.

## 🎯 Project Goals

- Translate all in-game text, menus, and mission briefings into Polish
- Maintain the original tone and atmosphere of the game
- Ensure compatibility with emulators and flash cartridges
- Provide clear documentation for installation and patching




## 1. font_parser.py

This project consists of a Python script designed to parse, de-swizzle, and visualize font texture data from the Nintendo 64 game **GoldenEye 007**. The primary goal is to extract character glyph data from the game's C source files, where it is stored in a hardware-specific "swizzled" format (CI8 with 8x2 microblocks), and convert it into standard, viewable PNG images. This repository is specifically configured to process the `fontBankGothic.c` file.

The script processes an input `.c` file containing font definitions and generates two composite PNG images that display all characters from the font set arranged horizontally:
- A grayscale image showing the raw pixel values.
- A binarized (black and white) image for clear visualization of the glyph shapes.

<img width="526" height="14" alt="fontBankGothic_grayscale" src="https://github.com/user-attachments/assets/11868894-2f22-4315-9757-8b56f6950d8c" />

<img width="526" height="14" alt="fontBankGothic_binary" src="https://github.com/user-attachments/assets/8f88f083-f39a-4804-a155-9bbef82738ec" />
