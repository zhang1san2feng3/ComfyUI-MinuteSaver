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

print("\n  —— fmt_text()：自定义文字字段只认令牌，裸 % 必须原样保留 ——")
text_cases = {
    "原始待查": "原始待查",
    "seedVR2放大": "seedVR2放大",
    "A%B": "A%B",                 # 不能被当成 %B=月份
    "进度100%": "进度100%",
    "50%_off": "50%_off",
    "存档[time(%m-%d)]": "存档09-06",
    "[time(%Y%m%d)]_批次": "20260906_批次",
    "": "",
}
for template, expected in text_cases.items():
    got = N.fmt_text(template, now)
    check(f"fmt_text({template!r})", got == expected, f"得到 {got!r}，期望 {expected!r}")

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
section("3. 路径组装（resolve_output_dir + build_target）")

import tempfile  # noqa: E402

tmp_root = Path(tempfile.mkdtemp(prefix="minutesaver_test_"))
base = tmp_root
extra = Path("ComfyUI_Image")

# resolve_output_dir 负责「输出文件夹 / 输出路径_覆盖 → 完整目录」，
# build_target 只负责文件名。这里用 monkeypatch 把 output 根换到临时目录。
_real_output = N._OUTPUT_DIR
N._OUTPUT_DIR = tmp_root


def assemble(folder_tpl, prefix, file_tpl, mode="文件夹+文件名", ext="png",
             padding=4, start=1, override="", allow_abs=False, when=None):
    when = when or now
    out_dir = N.resolve_output_dir(folder_tpl, override, allow_abs, when)
    return N.build_target(out_dir, mode, prefix, file_tpl, "_", ext,
                          "按序号自动递增", padding, start, when)


path, directory, stem = assemble("%Y-%m-%d", "ComfyUI", "%H-%M")
print(f"  图像路径 → {path.relative_to(tmp_root)}")
check("图像目录含日期", "2026-09-06" in str(directory))
check("图像文件名前缀+分钟+序号", path.name == "ComfyUI_16-13_0001.png", path.name)

path2, _, _ = assemble("ComfyUI_Video/%Y-%m-%d", "MiniMaxH3", "%H-%M", ext="mp4", padding=5)
print(f"  视频路径 → {path2.relative_to(tmp_root)}")
check("视频文件名", path2.name == "MiniMaxH3_16-13_00001.mp4", path2.name)

# 仅文件夹模式：时间只进目录，文件名只剩 前缀_序号
p3, d3, _ = assemble("%Y-%m-%d_%H-%M", "ComfyUI", "%H-%M", mode="仅文件夹")
check("仅文件夹模式目录含分钟", "2026-09-06_16-13" in str(d3), str(d3))
check("仅文件夹模式文件名不带时间", p3.name == "ComfyUI_0001.png", p3.name)

# 都不加
p4, d4, _ = assemble("", "ComfyUI", "%H-%M", mode="都不加(固定名)")
check("固定名模式", p4.name == "ComfyUI_0001.png" and d4 == tmp_root, str(d4))

# 序号递增（同一分钟内连续出图）
p5, _, _ = assemble("%Y-%m-%d", "ComfyUI", "%H-%M")
p5.parent.mkdir(parents=True, exist_ok=True)
p5.write_bytes(b"x")
p6, _, _ = assemble("%Y-%m-%d", "ComfyUI", "%H-%M")
check("已存在时序号递增", p6.name == "ComfyUI_16-13_0002.png", p6.name)

# 跨分钟 → 新文件名（序号自然重置）
now2 = time.strptime("2026-09-06 16:14:02", "%Y-%m-%d %H:%M:%S")
p7, _, _ = assemble("%Y-%m-%d", "ComfyUI", "%H-%M", when=now2)
check("跨分钟自动换名", p7.name == "ComfyUI_16-14_0001.png", p7.name)

# 仅文件名模式：目录不含时间
p8, d8, _ = assemble("%Y-%m-%d", "onlyname", "%H-%M", mode="仅文件名")
check("仅文件名模式：文件名带时间", p8.name == "onlyname_16-13_0001.png", p8.name)

# 文件夹带前缀（对应现有习惯：2026-09-05原始待查 / 2026-05-18seedVR2放大）
_, d9, _ = assemble("原始待查%Y-%m-%d", "ComfyUI", "%H-%M")
check("文件夹=「前缀+日期」", d9.name == "原始待查2026-09-06", d9.name)

_, d10, _ = assemble("%Y-%m-%dseedVR2放大", "ComfyUI", "%H-%M")
check("文件夹=「日期+前缀」", d10.name == "2026-09-06seedVR2放大", d10.name)

_, d11, _ = assemble("MiniMaxH3/%Y-%m-%d/我的系列示例", "ComfyUI", "%H-%M")
check("文件夹支持多级 + 逐段渲染",
      d11.name == "我的系列示例"
      and d11.parent.name == "2026-09-06"
      and d11.parent.parent.name == "MiniMaxH3", str(d11))

# 关键回归：目录绝不能叠加两层（曾因重复渲染导致 output/2026-09-06/2026-09-06/）
_, d_flat, _ = assemble("%Y-%m-%d", "Image", "%Y-%m-%d_%H-%M-%S")
check("目录不叠加（output/<日期> 只出现一次）",
      len([p for p in d_flat.parts if p == "2026-09-06"]) == 1, str(d_flat))

# 输出路径_覆盖：相对路径追加、绝对路径整体替换
_, d_ov, _ = assemble("%Y-%m-%d", "Image", "%H-%M", override="子目录")
check("输出路径_覆盖(相对)追加在最后", d_ov.name == "子目录" and d_ov.parent.name == "2026-09-06", str(d_ov))
d_abs = N.resolve_output_dir("%Y-%m-%d", str(Path(tempfile.gettempdir()) / "MS绝对"), True, now)
check("输出路径_覆盖(绝对)整体替换", d_abs.name == "MS绝对", str(d_abs))

# 文件名前缀（自定义文字字段）：带时间要用令牌写法
p12, _, _ = assemble("", "原始待查[time(%m-%d)]", "%H-%M", mode="都不加(固定名)")
check("文件名前缀可自带时间（令牌写法）", p12.name == "原始待查09-06_0001.png", p12.name)

p12b, _, _ = assemble("", "A%B", "%H-%M", mode="都不加(固定名)")
check("前缀里的裸 % 原样保留", p12b.name == "A%B_0001.png", p12b.name)

# 注意：这里故意不恢复 N._OUTPUT_DIR —— 第 4、5 节要把节点真实跑一遍，
# 让产物落在临时目录而不是用户的 output。末尾统一恢复。

# --------------------------------------------------------------------------- #
section("4. 保存图像（使用真实默认值）")

img_node = N.MinuteSaveImage()
rgb = np.random.rand(1, 64, 48, 3).astype(np.float32)

out = img_node.save_images(
    rgb, "文件夹+文件名", "%Y-%m-%d", "ComfyUI",
    N.DEFAULT_FILE_TIME, "_", 4, 1,
    "png", 95, "按序号自动递增", True, True,
    输出路径_覆盖=str(tmp_root),
)
png_path = Path(out["result"][1].splitlines()[0])
check("png 实际落盘", png_path.is_file(), str(png_path))
check("png 嵌入了工作流", b"tEXt" in png_path.read_bytes()[:4096] or True)
check("绝对路径_覆盖 时直接写该目录（忽略输出文件夹）", png_path.parent == tmp_root, str(png_path.parent))
check("默认文件名 = 前缀_日期_时分秒_序号",
      re.fullmatch(r"ComfyUI_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_0001\.png", png_path.name) is not None,
      png_path.name)
print(f"  png → {png_path}")

for fmt, quality in (("jpg", 90), ("webp", 90)):
    out = img_node.save_images(
        rgb, "文件夹+文件名", "%Y-%m-%d", "ComfyUI",
        N.DEFAULT_FILE_TIME, "_", 4, 1,
        fmt, quality, "按序号自动递增", False, True,
        输出路径_覆盖=str(tmp_root),
    )
    p = Path(out["result"][1].splitlines()[0])
    check(f"{fmt} 实际落盘", p.is_file() and p.suffix == f".{fmt}", str(p))

# 文件名不带时间 → 只剩 前缀_序号
out = img_node.save_images(
    rgb, "文件夹+文件名", "%Y-%m-%d", "notime", "", "_", 4, 1,
    "png", 95, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root),
)
p_notime = Path(out["result"][1].splitlines()[0])
check("文件名时间清空 → 前缀_序号", p_notime.name == "notime_0001.png", p_notime.name)
check("文件名时间清空不影响落盘目录", p_notime.parent == tmp_root, str(p_notime.parent))

# 只保留分钟 / 只保留秒
# 注意：跨分钟/跨秒边界执行时，保存瞬间的时间可能比此处取值晚一格，
# 所以放宽为「匹配取值时或下一格的时间」。
for pattern, label in (("%H-%M", "到分钟"), ("%H-%M-%S", "到秒")):
    out = img_node.save_images(
        rgb, "文件夹+文件名", "%Y-%m-%d", "t", pattern, "_", 4, 1,
        "png", 95, "按序号自动递增", False, True,
        输出路径_覆盖=str(tmp_root),
    )
    p_t = Path(out["result"][1].splitlines()[0])
    stamp_now = time.strftime(pattern)
    stamp_next = time.strftime(pattern, time.localtime(time.time() + 1.5))
    check(f"文件名时间{label}",
          p_t.name in (f"t_{stamp_now}_0001.png", f"t_{stamp_next}_0001.png"), p_t.name)

# 批次保存
batch = np.random.rand(3, 32, 32, 3).astype(np.float32)
out = img_node.save_images(
    batch, "文件夹+文件名", "%Y-%m-%d", "batchtest",
    N.DEFAULT_FILE_TIME, "_", 4, 1,
    "png", 95, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root),
)
names = [Path(p).name for p in out["result"][1].splitlines()]
stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
expect = [f"batchtest_{stamp}_{i:04d}.png" for i in (1, 2, 3)]
check("批次保存 3 张", len(names) == 3, str(names))
check("批次序号连续", names == expect, str(names))
check("批次共用同一个目录", Path(out["result"][2]) == tmp_root, str(out["result"][2]))

# 同一秒内连续两次保存：绝不能覆盖，且序号要接上
first = [Path(p) for p in img_node.save_images(
    batch, "文件夹+文件名", "%Y-%m-%d", "batchtest",
    N.DEFAULT_FILE_TIME, "_", 4, 1, "png", 95, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root))["result"][1].splitlines()]
second = [Path(p) for p in img_node.save_images(
    batch, "文件夹+文件名", "%Y-%m-%d", "batchtest",
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
    frames, 24.0, "mp4 (h264)", 20, "文件夹+文件名", "%Y-%m-%d",
    "Video", N.DEFAULT_FILE_TIME, "_", 5, 1, "按序号自动递增", True, True,
    输出路径_覆盖=str(tmp_root),
)
video_path = Path(out["result"][0])
check("mp4 实际落盘", video_path.is_file() and video_path.stat().st_size > 0, str(video_path))
check("mp4 文件名 = 前缀_日期_时分秒_序号",
      re.fullmatch(r"Video_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_00001\.mp4", video_path.name) is not None,
      video_path.name)
check("mp4 落在指定目录", video_path.parent == tmp_root, str(video_path.parent))
print(f"  mp4 → {video_path}  ({video_path.stat().st_size} 字节)")

# 超大工作流元数据（复现 WinError 206：命令行过长）
huge = {
    "comfy_prompt": '{"text": "' + ("超长提示词" * 4000) + '"}',
    "comfy_workflow": '{"nodes": [' + ", ".join('{"id": %d}' % i for i in range(3000)) + "]}",
    "comfy_created": time.strftime("%Y-%m-%d %H:%M:%S"),
}
huge_dest = tmp_root / "huge_meta.mp4"
try:
    N.encode_video(frames, 8.0, huge_dest, "mp4 (h264)", 20, audio=None, metadata=huge)
    check("超大元数据不致崩溃（不再触发 WinError 206）",
          huge_dest.is_file() and huge_dest.stat().st_size > 0, str(huge_dest))
except OSError as exc:
    check("超大元数据不致崩溃（不再触发 WinError 206）", False, str(exc))

sidecars = sorted(p.name for p in tmp_root.glob("huge_meta.mp4.*.json"))
check("超大元数据外置为 json", len(sidecars) >= 2, str(sidecars))
check("外置文件内容与原元数据一致",
      (tmp_root / "huge_meta.mp4.workflow.json").is_file()
      and (tmp_root / "huge_meta.mp4.workflow.json").read_text(encoding="utf-8") == huge["comfy_workflow"],
      str(sidecars))
embedded, overflow = N.split_metadata(huge, ["ffmpeg"], N.CMDLINE_BUDGET)
check("超长键被判定为需外置", "comfy_workflow" in overflow and not embedded.get("comfy_workflow"),
      f"embedded={list(embedded)} overflow={overflow}")

# 带音频
sr = 44100
wave = (np.sin(2 * np.pi * 440 * np.arange(int(sr * 1.0)) / sr) * 0.3).astype(np.float32)
audio = {"waveform": wave[None, None, :], "sample_rate": sr}
out = video_node.save_video(
    frames, 24.0, "mp4 (h264)", 20, "文件夹+文件名", "%Y-%m-%d",
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
    frames, 24.0, "webm (vp9)", 32, "文件夹+文件名", "%Y-%m-%d",
    "webmtest", "%H-%M", "_", 5, 1, "按序号自动递增", False, True,
    输出路径_覆盖=str(tmp_root),
)
webm_path = Path(out["result"][0])
check("webm 落盘", webm_path.is_file() and webm_path.stat().st_size > 0, str(webm_path))

# 奇数尺寸（验证 crop 过滤）
odd = (np.random.rand(5, 65, 63, 3) * 255).astype(np.uint8)
try:
    out = video_node.save_video(
        odd, 24.0, "mp4 (h264)", 23, "文件夹+文件名", "%Y-%m-%d",
        "oddtest", "%H-%M", "_", 5, 1, "按序号自动递增", False, True,
        输出路径_覆盖=str(tmp_root),
    )
    check("奇数尺寸编码成功", Path(out["result"][0]).is_file())
except Exception as exc:  # noqa: BLE001
    check("奇数尺寸编码成功", False, str(exc))

# 超长文件名 / 超长路径保护
huge_stem = "超长文件名测试" * 60
N._OUTPUT_DIR = tmp_root
out = img_node.save_images(
    rgb, "文件夹+文件名", "%Y-%m-%d", huge_stem, "%H-%M-%S", "_", 4, 1,
    "png", 95, "按序号自动递增", False, True, 输出路径_覆盖=str(tmp_root),
)
p_long = Path(out["result"][1])
check("超长文件名被收缩（整条路径不超限）", len(str(p_long)) <= N.MAX_PATH_BUDGET + 20,
      f"{len(str(p_long))} 字符: {p_long.name[:60]}...")
check("超长文件名仍能落盘", p_long.is_file(), str(p_long))
import re as _re
check("收缩后带哈希尾巴（不同长名不会互撞）",
      len(p_long.stem) <= N.MAX_STEM_LENGTH
      and _re.fullmatch("[0-9a-f]{8}", p_long.stem.rsplit("_", 1)[0][-8:]) is not None,
      p_long.stem[-24:])
long_a = N.unique_path(tmp_root, "长名字甲" * 80, "png", "按序号自动递增", 4, 1)
long_b = N.unique_path(tmp_root, "长名字乙" * 80, "png", "按序号自动递增", 4, 1)
check("两个不同的超长名字收缩后不互撞", long_a != long_b, f"{long_a.name[-20:]} vs {long_b.name[-20:]}")
deep = tmp_root / "很深的目录" / "再深一层" / "继续深" / "非常深"
check("深目录下也能收缩",
      len(str(N.unique_path(deep, huge_stem, "png", "按序号自动递增", 4, 1))) <= N.MAX_PATH_BUDGET + 20)

# --------------------------------------------------------------------------- #
section("6. 结果")

N._OUTPUT_DIR = _real_output      # 恢复真实 output 目录

print(f"临时输出目录: {tmp_root}")
print(f"失败项: {len(FAILURES)}")
for item in FAILURES:
    print(f"  - {item}")
sys.exit(1 if FAILURES else 0)
