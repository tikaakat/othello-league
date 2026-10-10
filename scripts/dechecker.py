"""
市松模様(透明表示用のチェッカーパターン)がJPEG化で焼き込まれた画像から、
本来の透明度(アルファチャンネル)を復元する。
チェッカーは無彩色(R≈G≈B)なので、彩度(色味)の無いピクセルを透明化する。
チェッカーの明るさ・色味は画像ごとに微妙に違う(ほぼ無彩色だが、わずかに
暖色寄りの場合がある)ため、各画像の縁(背景だけが写っているはず)を
サンプリングして、しきい値を自動調整する。
"""
import sys
from PIL import Image
import numpy as np


def _chroma(arr):
    r, g, b = arr[..., 0].astype(np.int16), arr[..., 1].astype(np.int16), arr[..., 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    return (mx - mn).astype(np.float32)


def dechecker(in_path, out_path, margin_ratio=0.04, low_pad=2.0, feather=12.0):
    im = Image.open(in_path).convert("RGB")
    arr = np.array(im)
    h, w = arr.shape[:2]
    chroma = _chroma(arr)

    # 縁(背景のみのはずの領域)をサンプリングして、このチェッカーの最大彩度を調べる
    m = max(2, int(min(h, w) * margin_ratio))
    border = np.concatenate([
        chroma[:m, :].ravel(), chroma[-m:, :].ravel(),
        chroma[:, :m].ravel(), chroma[:, -m:].ravel(),
    ])
    checker_max = float(np.percentile(border, 99.5))

    low = checker_max + low_pad
    high = low + feather

    alpha = np.clip((chroma - low) / (high - low), 0.0, 1.0) * 255.0
    alpha = alpha.astype(np.uint8)

    rgba = np.dstack([arr, alpha])
    out = Image.fromarray(rgba, mode="RGBA")
    out.save(out_path)
    transparent_ratio = (alpha < 10).mean()
    print(f"{in_path} -> {out_path}  (checker_max={checker_max:.1f} low={low:.1f} high={high:.1f} transparent={transparent_ratio:.1%})")


if __name__ == "__main__":
    dechecker(sys.argv[1], sys.argv[2])
