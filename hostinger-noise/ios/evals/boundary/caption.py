"""The local caption check: is the account and caption text of the screen now the same as in the reference frame?

Pillow only (no numpy), so the server can use the same code: proposal.patch adds this function to server.py unchanged.
The phone frame is 540 px wide; the box is the account name and up to 2 caption lines of the TikTok For You screen, left of
the right rail. Text counts only where it is white with a dark shadow 2 px down and right: the app's overlay text, not a
bright area of the video. Returns the overlap of the two text masks (0 to 1), or 0.0 when the box has almost no such text
(another app, a blurred screen, a scroll): then the model decides.
"""

import io

from PIL import Image, ImageChops, ImageFilter

CAPTION_BOX = (8, 925, 430, 1062)


def _caption_mask(jpeg):
    img = Image.open(io.BytesIO(jpeg)).convert("L")
    if img.width != 540:
        img = img.resize((540, round(img.height * 540 / img.width)))
    g = img.crop(CAPTION_BOX)
    white = g.point(lambda v: 255 if v > 215 else 0)
    shadow = ImageChops.offset(g, -2, -2).point(lambda v: 255 if v < 170 else 0)
    return ImageChops.multiply(white, shadow)


def caption_overlap(jpeg, ref):
    a, b = _caption_mask(jpeg), _caption_mask(ref)
    na, nb = a.histogram()[255], b.histogram()[255]
    if na + nb < 40:
        return 0.0
    # 1 px of JPEG jitter is allowed: each mask is widened before the overlap.
    hit = ImageChops.multiply(a, b.filter(ImageFilter.MaxFilter(3))).histogram()[255] + \
        ImageChops.multiply(b, a.filter(ImageFilter.MaxFilter(3))).histogram()[255]
    return hit / (na + nb)
