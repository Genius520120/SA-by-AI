"""
渲染流水线：视图设定 + SaveBMP + 去背/裁切/缩放落盘。

把"重启→打开→设视角→渲染→PIL 后处理"这段每次都要手写的流程收敛成一条命令。

用法：
    from sw_render import render
    shots = render(model, shots=[("iso", 7, 0), ("side", 5, -78)], out_dir=out_dir)
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Sequence

try:
    from .sw_connect import get_com_member
    from .sw_review import zoom_to_fit, clear_selection_for_preview, activate_model_for_preview
except ImportError:
    from sw_connect import get_com_member
    from sw_review import zoom_to_fit, clear_selection_for_preview, activate_model_for_preview

DEFAULT_BG = (248, 249, 250)
DEFAULT_SIZES = {"draft": (700, 440), "final": (1600, 1000)}


# ---------------------------------------------------------------- 纯 PIL 后处理
def postprocess_image(
    src,
    dst,
    angle: float = 0.0,
    width: int = 1600,
    height: int = 1000,
    margin: float = 0.95,
    thresh: int = 198,
    bg: Sequence[int] = DEFAULT_BG,
) -> Path:
    """
    对 SaveBMP 产物做去背景、旋转摆正、按内容裁切、等比缩放并居中落盘为 PNG。

    参数:
        src: 输入 BMP/PNG
        dst: 输出 PNG
        angle: 旋转角度（度，正值逆时针）
        width/height: 输出画幅
        margin: 内容占画幅比例 0–1
        thresh: 判定前景的"最小通道"阈值；SolidWorks 背景渐变最暗约 215，
                取 200 以下可避免把背景当作主体
    """
    from PIL import Image, ImageChops

    bg_rgb = tuple(int(v) for v in bg)
    im = Image.open(src).convert("RGB")

    def min_channel(img):
        r, g, b = img.split()
        return ImageChops.darker(r, ImageChops.darker(g, b))

    mask = min_channel(im).point(lambda v: 255 if v < thresh else 0)
    bbox = mask.getbbox()
    if bbox is None:
        raise RuntimeError(f"未识别到前景主体: {src}")
    x0, y0, x1, y1 = bbox
    pad = 30
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(im.width, x1 + pad), min(im.height, y1 + pad)
    crop = im.crop((x0, y0, x1, y1))
    submask = mask.crop((x0, y0, x1, y1))

    rotated = crop.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=bg_rgb)
    rmask = submask.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=0)
    rmask = rmask.point(lambda v: 255 if v > 128 else 0)
    canvas = Image.new("RGB", rotated.size, bg_rgb)
    canvas.paste(rotated, (0, 0), rmask)

    final_mask = min_channel(canvas).point(lambda v: 255 if v < thresh else 0).getbbox()
    if final_mask:
        canvas = canvas.crop(final_mask)

    scale = min(width * margin / canvas.width, height * margin / canvas.height)
    nw, nh = max(1, int(canvas.width * scale)), max(1, int(canvas.height * scale))
    canvas = canvas.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGB", (width, height), bg_rgb)
    out.paste(canvas, ((width - nw) // 2, (height - nh) // 2))

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() != ".png":
        dst = dst.with_suffix(".png")
    out.save(dst)
    return dst


# ---------------------------------------------------------------- 需要模型的出图
def render(
    model,
    shots: Iterable[tuple[str, int, float]] | None = None,
    out_dir=None,
    *,
    size: str = "final",
    width: int | None = None,
    height: int | None = None,
    margin: float = 0.95,
    angle: float = 0.0,
    clean: bool = True,
) -> list[Path]:
    """
    按视角清单渲染并落盘 PNG。

    参数:
        model: 已打开的 IModelDoc2
        shots: [(文件名, 视图ID, 倾角deg)]，默认 [("iso", 7, 0), ("side", 5, -78)]
        out_dir: 输出目录
        size: "draft" 起草用小图 / "final" 正式大图
        width/height: 直接指定渲染尺寸（优先于 size）
        margin/angle: 后处理参数
    返回:
        落盘的 PNG 路径列表
    """
    shots = list(shots) if shots else [("iso", 7, 0), ("side", 5, -78)]
    w, h = (width, height) if (width and height) else DEFAULT_SIZES[size]
    out_dir = Path(out_dir) if out_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    activate_model_for_preview(model)
    produced: list[Path] = []
    for name, view_id, tilt in shots:
        bmp = out_dir / f"{name}.bmp"
        try:
            model.ShowNamedView2("", int(view_id))
            zoom_to_fit(model)
            clear_selection_for_preview(model)
            if tilt:
                model.ActiveView.RotateAboutCenter(math.radians(-tilt), 0.0)
                zoom_to_fit(model)
                clear_selection_for_preview(model)
            model.SaveBMP(str(bmp), int(w), int(h))
            png = postprocess_image(bmp, out_dir / f"{name}.png",
                                    angle=angle, width=int(w), height=int(h), margin=margin)
            produced.append(png)
        except Exception as exc:  # 单视角失败不阻断其余
            print(f"[render] {name} 失败: {exc!r}")
        finally:
            if clean:
                try:
                    bmp.unlink(missing_ok=True)
                except Exception:
                    pass
    return produced
