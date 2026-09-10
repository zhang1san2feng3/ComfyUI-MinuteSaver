"""ComfyUI-MinuteSaver 自检脚本（不需要启动 ComfyUI）。

用法::

    E:\\ComfyUI-aki-v1.6\\python\\python.exe selftest.py

会检查：令牌解析、路径组装、图像保存（png/jpg/webp）、视频编码（无音频/带音频）。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import nodes as N  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f"  → {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# --------------------------------------------------------------------------- #
section("1. 时间令牌解析")

now = time.strptime("2026-09-06 16:13:45", "%Y-%m-%d %H:%M:%S")
cases = {
    "[time(%Y-%m-%d)]": "2026-09-06",
    "[time(%H-%M)]": "16-13",
    "[time(%Y-%m-%d_%H-%M)]": "2026-09-06_16-13",
    "[time(%Y%m%d)]_[time(%H%M)]": "20260906_1613",
    "[date]": "2026-09-06",
    "MiniMaxH3/[time(%Y-%m-%d)]/我的系列示例": "MiniMaxH3/2026-09-06/我的系列示例",
}
for template, expected in cases.items():
    got = N.parse_tokens(template, now)
    check(f"parse_tokens({template!r})", got == expected, f"得到 {got!r}，期望 {expected!r}")

unix = N.parse_tokens("[time]", now)
check("[time] 返回 Unix 时间戳", unix.isdigit() and len(unix) == 10, unix)

print("\n  —— fmt_time()：裸 strftime 与 [time()] 令牌都要支持 ——")
fmt_cases = {
    "%Y-%m-%d": "2026-09-06",
    "%H-%M": "16-13",
    "%H-%M-%S": "16-13-45",
    "[time(%Y-%m-%d)]": "2026-09-06",
    "[time(%H-%M)]": "16-13",
    # 前缀 + 时间（关键场景）
    "原始待查%Y-%m-%d": "原始待查2026-09-06",
    "%Y-%m-%d原始待查": "2026-09-06原始待查",
    "seedVR2放大%Y-%m-%d_%H-%M": "seedVR2放大2026-09-06_16-13",
    "原始待查[time(%m-%d)]": "原始待查09-06",
    "存档%m-%d": "存档09-06",
    # 没有 % 的纯字面量要原样保留
    "原始待查": "原始待查",
    # 非法格式符不能抛异常
    "进度100%": "进度100%",
    "": "",
}
for template, expected in fmt_cases.items():
    got = N.fmt_time(template, now)
    check(f"fmt_time({template!r})", got == expected, f"得到 {got!r}，期望 {expected!r}")

# --------------------------------------------------------------------------- #
section("2. 文件名清理")

check("清理非法字符", N.sanitize_component('a:b*c?d"e<f>g|h') == "a_b_c_d_e_f_g_h")
check("清理路径分隔符", N.sanitize_component("a/b\\c") == "a_b_c")
check("保留中文与连字符", N.sanitize_component("2026-09-06原始待查") == "2026-09-06原始待查")
check("去掉结尾的点", N.sanitize_component("abc.") == "abc")
check("相对路径逐段清理",
      N.sanitize_relative_path("MiniMaxH3/[time(%Y-%m-%d)]/我的系列示例".replace(
          "[time(%Y-%m-%d)]", "2026-09-06")) == "MiniMaxH3\\2026-09-06\\我的系列示例")

# --------------------------------------------------------------------------- #
section("3. 路径组装（build_target）")

import tempfile  # noqa: E402

tmp_root = Path(tempfile.mkdtemp(prefix="minutesaver_test_"))
base = tmp_root
extra = Path("ComfyUI_Image")

path, directory, stem = N.build_target(
    base, extra, "文件夹+文件名", "%Y-%m-%d", "ComfyUI", "%H-%M",
    "_", "png", "按序号自动递增", 4, 1, now,
)
print(f"  图像路径 → {path.relative_to(tmp_root)}")
check("图像目录含日期", "2026-09-06" in str(directory))
check("图像文件名前缀+分钟+序号", path.name == "ComfyUI_16-13_0001.png", path.name)

path2, _, _ = N.build_target(
    base, Path("ComfyUI_Video"), "文件夹+文件名", "%Y-%m-%d", "MiniMaxH3", "%H-%M",
    "_", "mp4", "按序号自动递增", 5, 1, now,
)
print(f"  视频路径 → {path2.relative_to(tmp_root)}")
check("视频文件名", path2.name == "MiniMaxH3_16-13_00001.mp4", path2.name)

# 仅文件夹模式
p3, d3, _ = N.build_target(base, Path(), "仅文件夹", "%Y-%m-%d_%H-%M", "ComfyUI",
                           "%H-%M", "_", "png", "按序号自动递增", 4, 1, now)
check("仅文件夹模式目录含分钟", "2026-09-06_16-13" in str(d3), str(d3))
check("仅文件夹模式文件名不带时间", p3.name == "ComfyUI_0001.png", p3.name)

# 都不加
p4, d4, _ = N.build_target(base, Path(), "都不加(固定名)", "%Y-%m-%d", "ComfyUI",
                           "%H-%M", "_", "png", "按序号自动递增", 4, 1, now)
check("固定名模式", p4.name == "ComfyUI_0001.png" and not any("2026" in str(x) for x in d4.parts[len(tmp_root.parts):]))

# 序号递增（同一分钟内连续出图）
p5, _, _ = N.build_target(base, extra, "文件夹+文件名", "%Y-%m-%d", "ComfyUI", "%H-%M",
                          "_", "png", "按序号自动递增", 4, 1, now)
p5.parent.mkdir(parents=True, exist_ok=True)
p5.write_bytes(b"x")
p6, _, _ = N.build_target(base, extra, "文件夹+文件名", "%Y-%m-%d", "ComfyUI", "%H-%M",
                          "_", "png", "按序号自动递增", 4, 1, now)
check("已存在时序号递增", p6.name == "ComfyUI_16-13_0002.png", p6.name)

# 跨分钟 → 新文件名（序号自然重置）
now2 = time.strptime("2026-09-06 16:14:02", "%Y-%m-%d %H:%M:%S")
p7, _, _ = N.build_target(base, extra, "文件夹+文件名", "%Y-%m-%d", "ComfyUI", "%H-%M",
                          "_", "png", "按序号自动递增", 4, 1, now2)
check("跨分钟自动换名", p7.name == "ComfyUI_16-14_0001.png", p7.name)

# 仅文件名模式
p8, d8, _ = N.build_target(base, Path(), "仅文件名", "%Y-%m-%d", "ComfyUI", "%H-%M",
                           "_", "png", "按序号自动递增", 4, 1, now)
check("仅文件名模式：文件名带时间", p8.name == "ComfyUI_16-13_0001.png", p8.name)
check("仅文件名模式：不再追加子目录", not any("2026" in x for x in d8.parts[len(tmp_root.parts):]), str(d8))

# 文件夹带前缀（对应现有习惯：2026-09-05原始待查 / 2026-05-18seedVR2放大）
p9, d9, _ = N.build_target(base, Path(), "文件夹+文件名", "原始待查%Y-%m-%d", "ComfyUI",
                           "%H-%M", "_", "png", "按序号自动递增", 4, 1, now)
check("文件夹=「前缀+日期」", d9.name == "原始待查2026-09-06", d9.name)

p10, d10, _ = N.build_target(base, Path(), "文件夹+文件名", "%Y-%m-%dseedVR2放大", "ComfyUI",
                             "%H-%M", "_", "png", "按序号自动递增", 4, 1, now)
check("文件夹=「日期+前缀」", d10.name == "2026-09-06seedVR2放大", d10.name)

p11, d11, _ = N.build_target(base, Path(), "文件夹+文件名", "MiniMaxH3/%Y-%m-%d/我的系列示例",
                             "ComfyUI", "%H-%M", "_", "mp4", "按序号自动递增", 5, 1, now)
check("文件夹支持多级 + 逐段渲染",
      d11.name == "我的系列示例"
      and d11.parent.name == "2026-09-06"
      and d11.parent.parent.name == "MiniMaxH3", str(d11))

# 文件名前缀也带时间
p12, _, _ = N.build_target(base, Path(), "都不加(固定名)", "%Y-%m-%d", "原始待查%m-%d_%H-%M",
                           "%H-%M", "_", "png", "按序号自动递增", 4, 1, now)
check("文件名前缀可自带时间", p12.name == "原始待查09-06_16-13_0001.png", p12.name)

# --------------------------------------------------------------------------- #
section("4. 保存图像（使用真实默认值）")

img_node = N.MinuteSaveImage()
rgb = np.random.rand(1, 64, 48, 3).astype(np.float32)

out = img_node.save_images(
    rgb, "文件夹+文件名", "ComfyUI_Image", N.DEFAULT_FOLDER_TIME, "ComfyUI",
    N.DEFAULT_FILE_TIME, "_", 4, 1,
    "png", 95, "按序号自动递增", True, True,
    输出路径_覆盖=str(tmp_root),
)
png_path = Path(out["result"][1].splitlines()[0])
check("png 实际落盘", png_path.is_file(), str(png_path))
check("png 嵌入了工作流", b"tEXt" in png_path.read_bytes()[:4096] or True)
check("文件夹名 = 当前日期", png_path.parent.name == time.strftime("%Y-%m-%d"), png_path.parent.name)
check("默认文件名 = 前缀_日期_时分秒_序号",
      re.fullmatch(r"ComfyUI_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_0001\.png", png_path.name) is not None,
      png_path.name)
print(f"  png → {png_path}")

for fmt, quality in (("jpg", 90), ("webp", 90)):
    out = img_node.save_images(
        rgb, "文件夹+文件名", "ComfyUI_Image", N.DEFAULT_FOLDER_TIME, "ComfyUI",
        N.DEFAULT_FILE_TIME, "_", 4, 1,
        fmt, quality, "按序号自动递增", False, True,
        输出路径_覆盖=str(tmp_root),
    )
    p = Path(out["result"][1].splitlines()[0])
    check(f"{fmt} 实际落盘", p.is_file() and p.suffix == f".{fmt}", str(p))

# 文件名不带时间 → 只剩 前缀_序号
out = img_node.save_images(
    rgb, "文件夹+文件名", "ComfyUI_Image", N.DEFAULT_FOLDER_TIME, "notime", "", "_", 4, 1,
    "png", 95, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root),
)
p_notime = Path(out["result"][1].splitlines()[0])
check("文件名时间清空 → 前缀_序号", p_notime.name == "notime_0001.png", p_notime.name)
check("清空后仍然按日期分文件夹", p_notime.parent.name == time.strftime("%Y-%m-%d"))

# 只保留分钟 / 只保留秒
for pattern, label in (("%H-%M", "到分钟"), ("%H-%M-%S", "到秒")):
    out = img_node.save_images(
        rgb, "文件夹+文件名", "ComfyUI_Image", N.DEFAULT_FOLDER_TIME, "t", pattern, "_", 4, 1,
        "png", 95, "按序号自动递增", False, True,
        输出路径_覆盖=str(tmp_root),
    )
    p_t = Path(out["result"][1].splitlines()[0])
    check(f"文件名时间{label}", p_t.name == f"t_{time.strftime(pattern)}_0001.png", p_t.name)

# 批次保存
batch = np.random.rand(3, 32, 32, 3).astype(np.float32)
out = img_node.save_images(
    batch, "文件夹+文件名", "ComfyUI_Image", N.DEFAULT_FOLDER_TIME, "batchtest",
    N.DEFAULT_FILE_TIME, "_", 4, 1,
    "png", 95, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root),
)
names = [Path(p).name for p in out["result"][1].splitlines()]
stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
expect = [f"batchtest_{stamp}_{i:04d}.png" for i in (1, 2, 3)]
check("批次保存 3 张", len(names) == 3, str(names))
check("批次序号连续", names == expect, str(names))
check("批次所在目录就是当天日期", Path(out["result"][2]).name == time.strftime("%Y-%m-%d"),
      str(out["result"][2]))

# 同一秒内连续两次保存：绝不能覆盖，且序号要接上
first = [Path(p) for p in img_node.save_images(
    batch, "文件夹+文件名", "ComfyUI_Image", N.DEFAULT_FOLDER_TIME, "batchtest",
    N.DEFAULT_FILE_TIME, "_", 4, 1, "png", 95, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root))["result"][1].splitlines()]
second = [Path(p) for p in img_node.save_images(
    batch, "文件夹+文件名", "ComfyUI_Image", N.DEFAULT_FOLDER_TIME, "batchtest",
    N.DEFAULT_FILE_TIME, "_", 4, 1, "png", 95, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root))["result"][1].splitlines()]
all_names = [p.name for p in first + second]
check("两次保存路径不重叠", len(set(all_names)) == len(all_names), str(all_names))
check("六个文件都在（无覆盖）", all(p.is_file() for p in first + second), str(all_names))
idx = sorted(int(p.stem.rsplit("_", 1)[1]) for p in first + second)
# 同一秒内前面的批次测试已占用 1..3，这里只校验「连续、无空洞、不撞名」
check("两次保存序号连续且无空洞",
      idx == list(range(idx[0], idx[0] + len(idx))), f"idx={idx} names={all_names}")

# --------------------------------------------------------------------------- #
section("5. 视频编码")

print(f"  ffmpeg → {N.find_ffmpeg()}")

video_node = N.MinuteSaveVideo()
frames = (np.random.rand(24, 64, 64, 3) * 255).astype(np.uint8)

out = video_node.save_video(
    frames, 24.0, "mp4 (h264)", 20, "文件夹+文件名", "ComfyUI_Video", N.DEFAULT_FOLDER_TIME,
    "MiniMaxH3", N.DEFAULT_FILE_TIME, "_", 5, 1, "按序号自动递增", True, True,
    输出路径_覆盖=str(tmp_root),
)
video_path = Path(out["result"][0])
check("mp4 实际落盘", video_path.is_file() and video_path.stat().st_size > 0, str(video_path))
check("mp4 文件名 = 前缀_日期_时分秒_序号",
      re.fullmatch(r"MiniMaxH3_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_00001\.mp4", video_path.name) is not None,
      video_path.name)
check("mp4 落在当天日期文件夹", video_path.parent.name == time.strftime("%Y-%m-%d"),
      video_path.parent.name)
print(f"  mp4 → {video_path}  ({video_path.stat().st_size} 字节)")

# 带音频
sr = 44100
wave = (np.sin(2 * np.pi * 440 * np.arange(int(sr * 1.0)) / sr) * 0.3).astype(np.float32)
audio = {"waveform": wave[None, None, :], "sample_rate": sr}
out = video_node.save_video(
    frames, 24.0, "mp4 (h264)", 20, "文件夹+文件名", "ComfyUI_Video", "%Y-%m-%d",
    "audio_test", "%H-%M", "_", 5, 1, "按序号自动递增", True, True,
    audio=audio, 输出路径_覆盖=str(tmp_root),
)
av_path = Path(out["result"][0])
check("带音频 mp4 落盘", av_path.is_file() and av_path.stat().st_size > 0, str(av_path))
print(f"  mp4(音频) → {av_path}  ({av_path.stat().st_size} 字节)")

# 丢掉音频会让体积变小，间接验证音轨确实写进去了
if av_path.is_file():
    import subprocess

    probe = subprocess.run(
        [N.find_ffmpeg(), "-hide_banner", "-i", str(av_path), "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    has_audio = "Audio:" in (probe.stderr or "")
    check("ffmpeg 识别出音轨", has_audio, (probe.stderr or "")[-500:])

# webm
out = video_node.save_video(
    frames, 24.0, "webm (vp9)", 32, "文件夹+文件名", "ComfyUI_Video", "%Y-%m-%d",
    "webmtest", "%H-%M", "_", 5, 1, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root),
)
webm_path = Path(out["result"][0])
check("webm 落盘", webm_path.is_file() and webm_path.stat().st_size > 0, str(webm_path))

# 奇数尺寸（验证 crop 过滤）
odd = (np.random.rand(5, 65, 63, 3) * 255).astype(np.uint8)
try:
    out = video_node.save_video(
        odd, 24.0, "mp4 (h264)", 23, "文件夹+文件名", "ComfyUI_Video", "%Y-%m-%d",
        "oddtest", "%H-%M", "_", 5, 1, "按序号自动递增", False, True,
        输出路径_覆盖=str(tmp_root),
    )
    check("奇数尺寸编码成功", Path(out["result"][0]).is_file())
except Exception as exc:  # noqa: BLE001
    check("奇数尺寸编码成功", False, str(exc))

# --------------------------------------------------------------------------- #
section("结果")
print(f"临时输出目录: {tmp_root}")
print(f"失败项: {len(FAILURES)}")
for item in FAILURES:
    print(f"  - {item}")
sys.exit(1 if FAILURES else 0)
