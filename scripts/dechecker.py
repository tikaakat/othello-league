"""
市松模様(透明表示用のチェッカーパターン)がJPEG化で焼き込まれた画像から、
本来の透明度(アルファチャンネル)を復元する。
チェッカーは無彩色(R≈G≈B)なので、彩度(色味)の無いピクセルを透明化する。
"""
import sys
from PIL import Image
import numpy as np


def dechecker(in_path, out_path, low=6, high=22):
    im = Image.open(in_path).convert("RGB")
    arr = np.array(im).astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    chroma = (mx - mn).astype(np.float32)

    alpha = np.clip((chroma - low) / (high - low), 0.0, 1.0) * 255.0
    alpha = alpha.astype(np.uint8)

    rgba = np.dstack([arr.astype(np.uint8), alpha])
    out = Image.fromarray(rgba, mode="RGBA")
    out.save(out_path)
    transparent_ratio = (alpha < 10).mean()
    print(f"{in_path} -> {out_path}  (transparent pixels: {transparent_ratio:.1%})")


if __name__ == "__main__":
    dechecker(sys.argv[1], sys.argv[2])
