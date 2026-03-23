#!/bin/bash
# Capture Analogue Pocket output and perform OCR
# Usage: ./capture_ocr.sh [--image-only]

SCREENSHOT="/tmp/pocket_ocr.png"
PROCESSED="/tmp/pocket_ocr_processed.png"

# Capture frame from video device (skip frames for sync)
ffmpeg -f v4l2 -i /dev/video0 -vf "select=gte(n\,30)" -frames:v 1 -update 1 "$SCREENSHOT" -y 2>/dev/null

if [ ! -f "$SCREENSHOT" ]; then
    echo "ERROR: Capture failed - check /dev/video0"
    exit 1
fi

# If --image-only, just output the path
if [ "$1" = "--image-only" ]; then
    echo "$SCREENSHOT"
    exit 0
fi

# Pre-process image for better OCR (scale up, increase contrast for terminal text)
convert "$SCREENSHOT" -resize 200% -sharpen 0x1 -normalize "$PROCESSED" 2>/dev/null

# Use processed image if available, otherwise original
OCR_INPUT="$PROCESSED"
[ ! -f "$PROCESSED" ] && OCR_INPUT="$SCREENSHOT"

# OCR with settings optimized for terminal/console text
# PSM 6 = Assume uniform block of text
tesseract "$OCR_INPUT" stdout --psm 6 -c tessedit_char_whitelist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz .:=_-+*/%()[]{}@#$!?,<>|&^~\n' 2>/dev/null || tesseract "$OCR_INPUT" stdout --psm 6 2>/dev/null || tesseract "$OCR_INPUT" stdout 2>/dev/null
