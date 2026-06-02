#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Capture a frame from video device and save as PNG
ffmpeg -f v4l2 -video_size 1920x1080 -i /dev/video0 -vframes 1 -y /tmp/capture.png 2>/dev/null
if [ -f /tmp/capture.png ]; then
    echo "Captured frame to /tmp/capture.png"
    file /tmp/capture.png
else
    echo "Failed to capture"
fi
