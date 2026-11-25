#!/bin/sh
# Rebuild the README pictures from made-up demo agents: badge PNGs, then the banner ad.
set -e
cd "$(dirname "$0")/.."
python3 pocock_watch.py --render docs/badge
chrome=${CHROME:-$(ls ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | tail -1)}
"$chrome" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --force-device-scale-factor=1.5 \
  --window-size=1280,720 --virtual-time-budget=3000 --screenshot=docs/.banner.png "file://$PWD/docs/ad.html"
python3 -c "from PIL import Image; Image.open('docs/.banner.png').convert('RGB').save('docs/banner.jpg', quality=88, optimize=True)"
rm docs/.banner.png
