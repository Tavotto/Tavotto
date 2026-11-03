#!/bin/sh
# demo(.zh).webm → demo(.zh).gif: 1280 px wide, 12 fps, one global palette.
# Usage: sh assets/readme/demo-to-gif.sh [en-US|zh-CN]
set -e
cd "$(dirname "$0")"
case "${1:-en-US}" in zh-CN) s=.zh ;; *) s= ;; esac
pal=$(mktemp -t demo-palette).png
vf="fps=12,scale=1280:-1:flags=lanczos"
ffmpeg -v error -y -i "demo$s.webm" -vf "$vf,palettegen=max_colors=128:stats_mode=diff" "$pal"
ffmpeg -v error -y -i "demo$s.webm" -i "$pal" -lavfi "$vf [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" "demo$s.gif"
rm -f "$pal"
ls -l "demo$s.gif"
