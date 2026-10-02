"""生成 C 端 PWA 图标（`apps/web/public/icons/`）。

为什么要一个**脚本**而不是几张图片：图标有 4 个尺寸 × 2 种用途（`any` / `maskable`），
而 `maskable` 的**安全区**是一个"看起来对、实际会被裁"的地方
（Android 会把图标裁成圆形/水滴/方形，只保证中心 **80% 直径**的圆内可见）。
手画一次容易，**改动 logo 时要重画四张且保证一致**就一定会漂移 ⇒ 脚本化。

## 设计口径（2026-10-02 用户确认）
- 底色 = **`#205CE9`**（= `apps/web/src/app/globals.css` 的 `--brand: 222 82% 52%`，**本脚本算出来的**）。
- 前景 = 白字 **"建"**。
- ★ **`any` 与 `maskable` 分开出图**（`manifest.ts` 里也分开写两条 `icons`）：
  - `any`：字高 ≈ **62%** 画布（好看，视觉重量足）；
  - `maskable`：字高 ≈ **50%** 画布 —— 安全圆半径 = 画布 40%，而 CJK 字的**外接圆**
    半径 ≈ 0.707 × 字高 ⇒ 0.707 × 50% = **35.4% < 40%** ✅ 留了余量。
    用户原话：「"建"字不能画到边缘（Android 会裁切）」。
- `apple-touch-icon`（180）走 `any` 口径：iOS **不读 `manifest` 的 icon**，只认这个
  `<link rel="apple-touch-icon">`，而且它**自己**加圆角 ⇒ 我们出**满幅方图**。

## 跑法
    PYTHONUTF8=1 python tools/local-verify/gen-pwa-icons.py

它**自己会验**（不靠"看起来对"）：写完重新打开，读 **PNG 的 IHDR 头**里的宽高，
和期望不一致就退出码非 0；并把每张图的**实际墨迹外接尺寸**打出来（好核对安全区）。

⚠️ 需要 Pillow（本机 10.4 已装）+ 一个中文字体（Windows 自带微软雅黑）。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "apps" / "web" / "public" / "icons"

GLYPH = "建"

#: 品牌色 —— 由 `--brand: 222 82% 52%` 换算（不是抄来的 hex）。
BRAND = (0x20, 0x5C, 0xE9)
WHITE = (0xFF, 0xFF, 0xFF)

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),  # 微软雅黑 Bold
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
]

#: (文件名, 边长, 字高 / 画布) —— `maskable` 的字明显更小，因为会被裁。
TARGETS: list[tuple[str, int, float]] = [
    ("icon-192.png", 192, 0.62),
    ("icon-512.png", 512, 0.62),
    ("maskable-512.png", 512, 0.50),
    ("apple-touch-icon.png", 180, 0.62),
]

#: Android maskable 的**保证可见区**：直径 = 画布 80% 的圆（半径 40%）。
SAFE_RADIUS_RATIO = 0.40


def say(msg: str) -> None:
    print(msg, flush=True)


def pick_font() -> Path:
    for p in FONT_CANDIDATES:
        if p.is_file():
            return p
    raise SystemExit(f"[icons] ✗ 找不到中文字体，试过：{[str(p) for p in FONT_CANDIDATES]}")


def png_size(path: Path) -> tuple[int, int]:
    """**自己读 PNG 的 IHDR**（不引第三方库、也不看文件名）。

    判据：文件名可以撒谎（"icon-192.png" 里放一张 512 的图），
    而 IHDR 在第 16~24 字节写死了真实宽高 ⇒ **只信字节**。
    这个函数与 `check-invariants.py` 不变量 10-b 用的是同一套读法。
    """
    with path.open("rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} 不是 PNG（头部魔数不对）")
    w, h = struct.unpack(">II", head[16:24])
    return int(w), int(h)


def render(font_path: Path, size: int, glyph_ratio: float, out: Path) -> tuple[int, int]:
    """画一张：**满幅底色 + 居中白字**，返回墨迹的 (宽, 高)。

    ⚠️ 为什么先量 `textbbox` 再定字号：`ImageFont.truetype(size=N)` 的 `N` 是 **em 尺寸**，
      而 CJK 字的**墨迹**比 em 小一圈（还受字体 metrics 影响）⇒ 直接按 em 推算，
      同一份代码换字体就会偏。这里**按墨迹反推字号**，所以"字高占比"在任何字体下都成立。
    ⚠️ 居中也要用**墨迹** bbox（含 `bbox[0]` / `bbox[1]` 的偏移），否则会因左右/上下留白
      不对称而看起来偏。
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (size, size), BRAND)
    draw = ImageDraw.Draw(img)

    probe = ImageFont.truetype(str(font_path), size)
    bbox = draw.textbbox((0, 0), GLYPH, font=probe)
    ink_h = bbox[3] - bbox[1]
    if ink_h <= 0:
        raise SystemExit("[icons] ✗ 字体量不到墨迹高度，换一个字体")

    target_ink_h = size * glyph_ratio
    font = ImageFont.truetype(str(font_path), max(1, round(size * target_ink_h / ink_h)))
    bbox = draw.textbbox((0, 0), GLYPH, font=font)
    ink_w, ink_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    draw.text(
        ((size - ink_w) / 2 - bbox[0], (size - ink_h) / 2 - bbox[1]),
        GLYPH,
        font=font,
        fill=WHITE,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return ink_w, ink_h


def main() -> int:
    font_path = pick_font()
    say(f"[icons] 字体 = {font_path}")
    say(f"[icons] 底色 = #{BRAND[0]:02X}{BRAND[1]:02X}{BRAND[2]:02X}｜字 = {GLYPH}")
    say(f"[icons] 输出 = {OUT_DIR}")
    say("")

    bad = 0
    for name, size, ratio in TARGETS:
        out = OUT_DIR / name
        ink_w, ink_h = render(font_path, size, ratio, out)
        got = png_size(out)

        # 外接圆半径（CJK 字近似正方形墨迹，取对角线一半）
        half_diag = ((ink_w / 2) ** 2 + (ink_h / 2) ** 2) ** 0.5
        ratio_r = half_diag / size
        maskable = "maskable" in name
        ok_size = got == (size, size)
        ok_safe = (not maskable) or ratio_r <= SAFE_RADIUS_RATIO
        flag = "✅" if (ok_size and ok_safe) else "❌"
        if not (ok_size and ok_safe):
            bad += 1
        note = f"｜安全圆占比 {ratio_r:.1%}（限 {SAFE_RADIUS_RATIO:.0%}）" if maskable else ""
        say(
            f"  {flag} {name:22} IHDR={got[0]}×{got[1]}（期望 {size}×{size}）"
            f"｜墨迹 {ink_w}×{ink_h}（字高占比 {ink_h / size:.1%}）{note}"
        )

    say("")
    if bad:
        say(f"[icons] ✗ {bad} 张不合格")
        return 1
    say(f"[icons] ✓ {len(TARGETS)} 张全部合格（尺寸按 IHDR 实读，maskable 已验安全区）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
