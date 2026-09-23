"""生成应用图标 icon.ico（书本 + 放大镜，代表文件检索助手）。

运行一次即可，生成 assets/icon.ico（多尺寸，Windows 兼容）。
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
OUT = Path(__file__).resolve().parent / "assets" / "icon.ico"


def lerp(a, b, t):
    return int(a + (b - a) * t)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角渐变背景（左上蓝 -> 右下紫）
    top = (37, 99, 235)      # 蓝
    bottom = (124, 58, 237)  # 紫
    for y in range(size):
        t = y / size
        r = lerp(top[0], bottom[0], t)
        g = lerp(top[1], bottom[1], t)
        b = lerp(top[2], bottom[2], t)
        d.line([(0, y), (size, y)], fill=(r, g, b, 255))

    radius = int(size * 0.22)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size, size], radius=radius, fill=255)
    img.putalpha(mask)

    cx = size / 2
    book_top_y = size * 0.52
    book_bot_y = size * 0.86
    book_left = size * 0.16
    book_right = size * 0.84
    spine = size * 0.035

    # 左页
    d.polygon(
        [(book_left, size * 0.62), (cx - spine, book_top_y), (cx - spine, book_bot_y), (book_left, book_bot_y)],
        fill=(255, 255, 255, 255),
    )
    # 右页
    d.polygon(
        [(book_right, size * 0.62), (cx + spine, book_top_y), (cx + spine, book_bot_y), (book_right, book_bot_y)],
        fill=(240, 246, 255, 255),
    )

    # 书页横线
    line_color = (180, 200, 240, 255)
    for i in range(1, 3):
        yy = size * 0.62 + i * size * 0.06
        d.line([(size * 0.21, yy), (cx - spine - size * 0.02, yy)], fill=line_color, width=int(size * 0.012))
        d.line([(size * 0.79, yy), (cx + spine + size * 0.02, yy)], fill=line_color, width=int(size * 0.012))

    # 放大镜
    lx = cx
    ly = size * 0.42
    r = size * 0.15
    ring_w = int(size * 0.035)
    d.ellipse([lx - r, ly - r, lx + r, ly + r], fill=(255, 255, 255, 60))
    d.ellipse([lx - r, ly - r, lx + r, ly + r], outline=(255, 255, 255, 255), width=ring_w)
    hx = lx + r * 0.72
    hy = ly + r * 0.72
    d.line([(hx, hy), (hx + size * 0.14, hy + size * 0.14)], fill=(255, 255, 255, 255), width=ring_w)
    d.ellipse(
        [hx + size * 0.14 - ring_w / 2, hy + size * 0.14 - ring_w / 2,
         hx + size * 0.14 + ring_w / 2, hy + size * 0.14 + ring_w / 2],
        fill=(255, 255, 255, 255),
    )
    return img


def build_ico(base: Image.Image, sizes: list[int]) -> bytes:
    """手动构造多尺寸 ICO（全部用 PNG 存储，兼容 Windows Vista+）。"""
    entries = []
    png_datas = []
    offset = 6 + 16 * len(sizes)
    for s in sizes:
        img = base.resize((s, s), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        png_datas.append(data)
        # width/height 用 0 表示 256
        w = s if s < 256 else 0
        h = s if s < 256 else 0
        entries.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    return header + b"".join(entries) + b"".join(png_datas)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base = draw_icon(SIZE)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    data = build_ico(base, sizes)
    OUT.write_bytes(data)
    print(f"图标已生成：{OUT}（{len(sizes)} 个尺寸，{len(data)} 字节）")


if __name__ == "__main__":
    main()
