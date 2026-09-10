"""ComfyUI-MinuteSaver — 按系统时间（精确到分钟）保存图像 / 视频。

核心特性
--------
* 路径与文件名支持时间令牌 ``[time(格式)]``（与 WAS Node Suite 语法完全兼容），
  例如 ``[time(%Y-%m-%d)]`` / ``[time(%H-%M)]``。
* 时间可以只放文件夹、只放文件名、两者都放、或都不放（4 种保存模式）。
* 默认即「精确到分钟」：文件名带 ``_%H-%M``，所以同一分钟内连续出图共享一个
  时间戳、序号从 00001 递增；跨分钟自动换到新的命名与新的序号。

默认输出长相（贴近你现有习惯）
------------------------------
ComfyUI 的 ``output`` 是一级目录（固定，节点只能写在它下面），
所以节点只给一个「输出文件夹」字段，默认就是**当前日期**。
文件名 = **前缀_时间_序号**，时间可以精确到「分」甚至「秒」，也可以整个清空（只剩 `前缀_序号`）。

图像::

    output/2026-09-06/Image_2026-09-06_16-13-45_0001.png
    └ 一级 ┘└ 输出文件夹 ┘└前缀┘└── 文件时间 ──┘└序号┘

视频（默认前缀 Video；填系列名也行）::

    output/2026-09-06/Video_2026-09-06_16-13-45_00001.mp4
    output/2026-09-06/我的系列示例_2026-09-06_16-13-45_00001.mp4

只想要「日期文件夹 + 纯序号」，把「文件名时间格式」清空即可::

    output/2026-09-06/Image_0001.png

想让文件夹带系列名 / 精确到分钟，改「输出文件夹」::

    output/我的系列示例2026-09-06/Image_0001.png
    output/2026-09-06_16-13/Image_0001.png

令牌一览（输出文件夹 / 文件名时间 / 文件名前缀 / 额外文件名 都能用）
-------------------------------------------------------------------
======================  =========================================================
``[time]``              Unix 时间戳（秒）
``[time(%Y-%m-%d)]``    strftime 自定义格式：``%Y`` ``%m`` ``%d`` ``%H`` ``%M`` ``%S``
``[date]``              等价于 ``[time(%Y-%m-%d)]``
``[hostname]``          机器名
``[user]``              当前用户名
======================  =========================================================

Windows 文件名不允许 ``: * ? " < > |``，会被自动替换成 ``_``。
所以「小时:分钟」请写 ``%H-%M``，不要写 ``%H:%M``。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

try:  # pragma: no cover - 仅在 ComfyUI 运行时可用
    import folder_paths

    _OUTPUT_DIR = Path(folder_paths.get_output_directory())
except Exception:  # pragma: no cover - 便于脱离 ComfyUI 单测
    folder_paths = None
    _OUTPUT_DIR = Path.cwd() / "output"

try:  # pragma: no cover
    import comfy.model_management as model_management
except Exception:  # pragma: no cover
    model_management = None


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

CATEGORY = "MinuteSaver/时间保存"

IMAGE_EXTENSIONS = ["png", "jpg", "webp"]
VIDEO_FORMATS = ["mp4 (h264)", "mov (h264)", "mkv (h264)", "webm (vp9)"]
OVERWRITE_MODES = ["按序号自动递增", "直接覆盖", "允许重名(追加毫秒)"]
SAVE_MODES = ["文件夹+文件名", "仅文件名", "仅文件夹", "都不加(固定名)"]

DEFAULT_OUTPUT_FOLDER = "%Y-%m-%d"
DEFAULT_FILE_TIME = "%Y-%m-%d_%H-%M-%S"

_ILLEGAL_RE = re.compile(r'[<>:"|?*\x00-\x1f]')
TOKEN_RE = re.compile(r"\[time\((.*?)\)\]")

_FFMPEG_TAIL = {
    "mp4 (h264)": ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart"],
    "mov (h264)": ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart"],
    "mkv (h264)": ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"],
    "webm (vp9)": ["-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-row-mt", "1"],
}

_FORMAT_ALIASES = {
    "mp4 (h264)": "mp4",
    "mov (h264)": "mov",
    "mkv (h264)": "mkv",
    "webm (vp9)": "webm",
}


# --------------------------------------------------------------------------- #
# 令牌解析
# --------------------------------------------------------------------------- #

def parse_tokens(text: str, now: time.struct_time | None = None) -> str:
    """替换路径 / 文件名中的时间等令牌（与 WAS Node Suite 语法一致）。"""
    if not text:
        return ""
    if now is None:
        now = time.localtime()

    out = TOKEN_RE.sub(lambda m: time.strftime(m.group(1), now), text)
    out = out.replace("[date]", time.strftime("%Y-%m-%d", now))
    out = out.replace("[time]", str(int(time.time())))
    out = out.replace("[hostname]", socket.gethostname())
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "null"
    out = out.replace("[user]", user)
    return out


def _apply_strftime(text: str, now: time.struct_time) -> str:
    """把文本里的 strftime 格式符渲染掉。

    只在「没有残留的 [ ] 令牌」且「文本里有 %」时才尝试；
    遇到 ``%`` 但不是合法格式符（``ValueError``）就原样返回，不会把用户的字面量搞坏。
    """
    if not text or "%" not in text or "[" in text or "]" in text:
        return text
    try:
        return time.strftime(text, now)
    except Exception:
        return text


def fmt_time(template: str, now: time.struct_time) -> str:
    """把「时间格式 / 文件名前缀」字段渲染成字符串。

    支持「前缀 + 时间」自由组合，下面四种写法等价：

    * ``%Y-%m-%d``                       → ``2026-09-06``
    * ``[time(%Y-%m-%d)]``               → ``2026-09-06``
    * ``原始待查%Y-%m-%d``               → ``原始待查2026-09-06``
    * ``[time(%Y-%m-%d)]原始待查``       → ``2026-09-06原始待查``
    * ``2026-09-06_%H-%M``               → ``2026-09-06_16-13``

    先做令牌解析；若结果里还留着 ``%`` 格式符，再按裸 strftime 解释一次。
    这样「前缀是字面量、时间是格式符」的场景也能正常工作。
    """
    if not template or not template.strip():
        return ""

    return _apply_strftime(parse_tokens(template, now), now)


def sanitize_component(part: str) -> str:
    """清理单个路径片段（文件夹名 / 文件名），去掉 Windows 非法字符。"""
    part = _ILLEGAL_RE.sub("_", part)
    part = part.replace("\\", "_").replace("/", "_")
    part = part.strip().rstrip(".")
    return part or "_"


def sanitize_relative_path(text: str) -> str:
    """清理相对路径：允许 ``/`` 与 ``\\`` 作分隔符，逐段清理。"""
    if not text:
        return ""
    normalized = text.replace("\\", "/")
    parts = [sanitize_component(p) for p in normalized.split("/") if p.strip()]
    return os.path.join(*parts) if parts else ""


def strip_leading_abs(text: str) -> str:
    """把 ``/abs/...`` 或 ``E:/abs/...`` 形式转成纯相对路径。"""
    t = text.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", t):
        t = t[2:]
    return t.lstrip("/")


# --------------------------------------------------------------------------- #
# 路径组装
# --------------------------------------------------------------------------- #

def resolve_output_dir(output_folder: str, output_path_override: str,
                       allow_absolute: bool, now: time.struct_time) -> Path:
    """算出最终的输出目录（绝对路径）。

    规则（顺序很重要）：

    1. ``output``（ComfyUI 固定的一级目录）/ ``输出文件夹``
    2. ``输出路径_覆盖`` 填绝对路径且勾选了「允许绝对路径」→ 它直接成为输出目录，
       「输出文件夹」被忽略（等于彻底改写到别的盘）
    3. ``输出路径_覆盖`` 填相对路径 → 附加在「输出文件夹」之后，
       即 ``output/<输出文件夹>/<输出路径_覆盖>/``
    4. 两个都留空 → 直接写 ``output`` 根目录
    """
    parts: list[Path] = []

    folder_rel = sanitize_relative_path(render_folder_path(output_folder, now))
    if folder_rel:
        parts.append(Path(folder_rel))

    override = (output_path_override or "").strip()
    if override:
        expanded = parse_tokens(override, now)
        if allow_absolute and Path(expanded).is_absolute():
            return Path(expanded)                  # 规则 2：整体改到绝对目录
        extra_rel = sanitize_relative_path(strip_leading_abs(expanded))
        if extra_rel:
            parts.append(Path(extra_rel))          # 规则 3：追加在输出文件夹之后

    if not parts:
        return _OUTPUT_DIR                         # 规则 4：直接写 output 根目录

    return _OUTPUT_DIR.joinpath(*parts)


def default_prefix(output_folder: str, now: time.struct_time) -> str:
    """「文件名前缀」留空时的兜底：用「输出文件夹」的最后一段（已渲染）。"""
    folder = sanitize_relative_path(render_folder_path(output_folder, now))
    if folder:
        last = Path(folder).name
        if last:
            return last
    return "ComfyUI"


def unique_path(directory: Path, stem: str, ext: str, mode: str,
                padding: int, start: int, max_scan: int = 100000) -> Path:
    """在 directory 下为 stem 找一个可用文件路径。"""
    if mode == "直接覆盖":
        return directory / f"{stem}.{ext}"

    if mode == "允许重名(追加毫秒)":
        path = directory / f"{stem}.{ext}"
        if not path.exists():
            return path
        return directory / f"{stem}_{int(time.time() * 1000)}.{ext}"

    width = max(1, int(padding))
    counter = max(0, int(start))
    for _ in range(max_scan):
        candidate = directory / f"{stem}_{counter:0{width}d}.{ext}"
        if not candidate.exists():
            return candidate
        counter += 1
    return directory / f"{stem}_{int(time.time() * 1000)}.{ext}"


def render_folder_path(template: str, now: time.struct_time) -> str:
    """渲染「输出文件夹」字段：按 ``/`` 或 ``\\`` 拆段，逐段渲染时间，再拼回去。

    逐段处理很关键——整串一起处理的话，像 ``MiniMaxH3/%Y-%m-%d/我的系列示例``
    这种「有的段带时间、有的段不带」的路径就会出错。
    """
    if not template or not template.strip():
        return ""

    text = template.replace("\\", "/")
    parts = [fmt_time(part, now) for part in text.split("/") if part.strip()]
    return "/".join(parts)


def unique_path_indexed(directory: Path, stem: str, ext: str, mode: str,
                        padding: int, start: int, max_scan: int = 100000):
    """同 :func:`unique_path`，但额外返回「本次实际用到的序号」。

    批次保存时用它串起序号，避免同一秒内第二次运行把第一张覆盖掉。
    """
    path = unique_path(directory, stem, ext, mode, padding, start, max_scan)

    used = start
    if mode == "按序号自动递增":
        try:
            tail = path.stem.rsplit("_", 1)[1]
            used = int(tail)
        except (IndexError, ValueError):
            used = start
    return path, used


def build_target(output_dir: Path, save_mode: str,
                 prefix: str, file_time: str,
                 delimiter: str, ext: str, overwrite_mode: str,
                 padding: int, start: int, now: time.struct_time):
    """统一的落盘路径计算，图像 / 视频节点共用。返回 (完整路径, 目录, 文件名主干)。

    ``output_dir`` 是已经算好的目录（由 :func:`resolve_output_dir` 得到，
    已经包含「输出文件夹」和「输出路径_覆盖」），这里只负责文件名部分。

    文件名分支（相对于 output 目录）的语义：

    * ``文件夹+文件名`` / ``仅文件名``：``前缀_时间_序号``
    * ``仅文件夹`` / ``都不加``      ：``前缀_序号``（时间只体现在目录上）

    前缀与时间都支持「前缀 + 时间」自由组合，
    例如 ``原始待查%Y-%m-%d`` / ``%Y-%m-%d原始待查`` / ``我的系列示例[time(%m-%d)]``。
    """
    time_in_name = save_mode in ("文件夹+文件名", "仅文件名")

    prefix_rendered = fmt_time((prefix or "").strip(), now) or "ComfyUI"
    file_time = (file_time or "").strip()

    parts = [prefix_rendered]
    if time_in_name and file_time:
        rendered = fmt_time(file_time, now)
        if rendered:
            parts.append(rendered)
    stem = delimiter.join(parts)

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    path = unique_path(directory, sanitize_component(stem), ext,
                       overwrite_mode, padding, start)
    return path, directory, stem


def to_ui_location(path: Path) -> tuple[dict, bool]:
    """生成 ComfyUI UI 预览所需的信息；返回 (result dict, 是否在 output 内)。"""
    try:
        relative = path.parent.resolve().relative_to(_OUTPUT_DIR.resolve())
        inside = True
        subfolder = "" if str(relative) == "." else str(relative)
    except Exception:
        inside = False
        subfolder = str(path.parent)

    result = {
        "filename": path.name,
        "subfolder": subfolder,
        "type": "output" if inside else "absolute",
    }
    return result, inside


# --------------------------------------------------------------------------- #
# 元数据 / 张量转换
# --------------------------------------------------------------------------- #

def build_metadata(prompt, extra_pnginfo) -> dict[str, str]:
    """ComfyUI 的 prompt / workflow → 可直接写盘的字符串字典。"""
    meta: dict[str, str] = {}

    def _dump(obj) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            return str(obj)

    if prompt is not None:
        meta["prompt"] = _dump(prompt)
    if isinstance(extra_pnginfo, dict):
        for key, value in extra_pnginfo.items():
            meta[str(key)] = _dump(value)
    elif extra_pnginfo is not None:
        meta["extra_pnginfo"] = _dump(extra_pnginfo)
    return meta


def tensor_to_pil(img) -> Image.Image:
    """ComfyUI IMAGE 单帧（H,W,C float 0~1）→ PIL Image。"""
    arr = img
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    if arr.shape[-1] > 3:
        arr = arr[:, :, :3]
    arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def tensor_frames_to_uint8(images) -> np.ndarray:
    """ComfyUI IMAGE (B,H,W,C) → (B,H,W,3) uint8。"""
    arr = images
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    arr = np.asarray(arr)
    if arr.ndim == 3:
        arr = arr[None, ...]
    if arr.ndim != 4:
        raise ValueError(f"IMAGE 张量维度应为 4（B,H,W,C），当前为 {arr.ndim}")
    if arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    if arr.shape[-1] > 3:
        arr = arr[:, :, :, :3]
    if arr.dtype != np.uint8:
        arr = np.clip(arr.astype(np.float32) * 255.0, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(arr)


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #

def find_ffmpeg() -> str:
    """按优先级查找 ffmpeg：环境变量 → PATH → imageio-ffmpeg 内置 → 常见路径。"""
    env = os.environ.get("FFMPEG_BINARY")
    if env and Path(env).is_file():
        return env

    which = shutil.which("ffmpeg")
    if which:
        return which

    try:  # ComfyUI 环境里 videohelpersuite 依赖了 imageio-ffmpeg
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass

    for guess in (
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
    ):
        if guess.is_file():
            return str(guess)

    raise RuntimeError(
        "找不到 ffmpeg。请安装 ffmpeg 并加入 PATH，或设置环境变量 FFMPEG_BINARY 指向 "
        "ffmpeg.exe，也可以 pip install imageio-ffmpeg 使用内置版本。"
    )


def audio_to_wav_bytes(audio, limit_seconds: float | None = None) -> bytes | None:
    """ComfyUI AUDIO dict → WAV 字节流（16bit PCM）。"""
    if not isinstance(audio, dict):
        return None
    samples = audio.get("waveform")
    rate = int(audio.get("sample_rate") or 44100)
    if samples is None:
        return None

    if hasattr(samples, "detach"):
        samples = samples.detach().cpu().numpy()
    data = np.asarray(samples, dtype=np.float32)

    if data.ndim == 3:      # (batch, channels, time)
        data = data[0]
    if data.ndim == 1:      # (time,)
        data = data[None, :]
    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
        return None

    if data.shape[0] > 2:   # 多于 2 声道则下混为前 2 声道
        data = data[:2]
    data = np.clip(data, -1.0, 1.0)

    if limit_seconds is not None:
        data = data[:, : max(1, int(limit_seconds * rate))]

    channels = int(data.shape[0])
    pcm = (data.T * 32767.0).astype("<i2").tobytes()   # (time, channels)

    byte_rate = rate * channels * 2
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, channels * 2, 16)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def encode_video(frames: np.ndarray, fps: float, dest: Path, fmt: str,
                 crf: int, audio=None, metadata: dict[str, str] | None = None) -> Path:
    """把 (F,H,W,3) uint8 帧编码成视频文件，可选拼接音频。"""
    ffmpeg = find_ffmpeg()
    fps = max(1.0, float(fps))
    height, width = int(frames.shape[1]), int(frames.shape[2])

    tail = list(_FFMPEG_TAIL.get(fmt, _FFMPEG_TAIL["mp4 (h264)"]))
    crf = max(0, min(51, int(crf)))
    if fmt == "webm (vp9)":
        tail += ["-crf", str(crf), "-b:v", "0"]
    else:
        tail += ["-crf", str(crf)]

    vf: list[str] = []
    if width % 2 or height % 2:      # yuv420p 要求偶数宽高
        vf = ["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2"]

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-vcodec", "rawvideo",
           "-s", f"{width}x{height}", "-pix_fmt", "rgb24",
           "-r", f"{fps}", "-i", "pipe:0"]

    audio_path: Path | None = None
    if audio is not None:
        wav = audio_to_wav_bytes(audio, limit_seconds=frames.shape[0] / fps)
        if wav:
            fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix="minutesaver_")
            os.close(fd)
            audio_path = Path(tmp_name)
            audio_path.write_bytes(wav)
            cmd += ["-i", str(audio_path)]

    for key, value in (metadata or {}).items():
        text = str(value)
        if len(text) > 20000:        # 避免命令行过长
            text = text[:20000] + "...(truncated)"
        cmd += ["-metadata", f"{key}={text}"]

    cmd += vf + tail
    if audio_path is not None:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(str(dest))

    creationflags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    try:
        proc.stdin.write(frames.tobytes())
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    stderr = proc.stderr.read()
    proc.wait()
    if audio_path is not None:
        audio_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg 编码失败（code %s）：\n%s"
            % (proc.returncode, stderr.decode("utf-8", "replace")[-4000:])
        )
    return dest


# --------------------------------------------------------------------------- #
# 节点 1：按时间保存图像
# --------------------------------------------------------------------------- #

class MinuteSaveImage:
    """把图像保存到「按系统时间」生成的文件夹 / 文件名中。"""

    def __init__(self):
        self.output_dir = _OUTPUT_DIR
        self.type = "output"
        self.prefix_append = ""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "保存模式": (SAVE_MODES, {"default": "文件夹+文件名",
                                          "tooltip": "文件夹+文件名 / 仅文件名 / 仅文件夹 / 都不加"}),
                "输出文件夹": ("STRING", {"default": DEFAULT_OUTPUT_FOLDER, "multiline": False,
                                          "tooltip": "相对 output 的文件夹，默认 %Y-%m-%d（当前日期）。\n"
                                                     "「前缀+时间」自由组合，也支持多级：\n"
                                                     "  原始待查%Y-%m-%d   → output/原始待查2026-09-06/\n"
                                                     "  我的系列示例%Y-%m-%d → output/我的系列示例2026-09-06/\n"
                                                     "  MiniMaxH3/%Y-%m-%d  → output/MiniMaxH3/2026-09-06/\n"
                                                     "清空则直接写到 output 根目录"}),
                "文件名前缀": ("STRING", {"default": "Image", "multiline": False,
                                          "tooltip": "用来区分类型 / 系列，默认 Image。"
                                                     "前缀可以自带时间，例如 原始待查%m-%d_%H-%M 或 [time(%m-%d)]；"
                                                     "留空则用「输出文件夹」的最后一段"}),
                "文件名时间格式": ("STRING", {"default": DEFAULT_FILE_TIME, "multiline": False,
                                              "tooltip": "默认 %Y-%m-%d_%H-%M-%S（到秒）。"
                                                         "只要分秒填 %H-%M-%S，只要分钟填 %H-%M，"
                                                         "清空则文件名不带时间，只剩 前缀_序号"}),
                "文件名分隔符": ("STRING", {"default": "_", "multiline": False}),
                "文件名序号位数": ("INT", {"default": 4, "min": 1, "max": 12, "step": 1}),
                "文件名序号起始": ("INT", {"default": 1, "min": 0, "max": 999999, "step": 1}),
                "图片格式": (IMAGE_EXTENSIONS, {"default": "png"}),
                "质量": ("INT", {"default": 95, "min": 1, "max": 100, "step": 1,
                                 "tooltip": "png 用于控制压缩级别，jpg/webp 为质量"}),
                "重名处理": (OVERWRITE_MODES, {"default": OVERWRITE_MODES[0]}),
                "嵌入工作流": ("BOOLEAN", {"default": True,
                                           "tooltip": "把 prompt / 工作流写进 PNG，拖回 ComfyUI 可还原"}),
                "允许绝对路径": ("BOOLEAN", {"default": False,
                                             "tooltip": "开启后，输出路径填绝对路径时会直接写到该磁盘目录"}),
            },
            "optional": {
                "输出路径_覆盖": ("STRING", {"default": "", "multiline": False,
                                              "tooltip": "留空则用「输出文件夹」。会追加在「输出文件夹」之后：\n"
                                                         "  output/<输出文件夹>/<输出路径_覆盖>/\n"
                                                         "填绝对路径需勾选「允许绝对路径」"}),
                "额外文件名": ("STRING", {"forceInput": True,
                                          "tooltip": "接一个字符串进来，会追加到文件名前缀后面"}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("images", "完整路径", "文件夹")
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = ("按系统时间自动命名保存图像。输出文件夹默认是当前日期（%Y-%m-%d），"
                   "文件名默认带 %Y-%m-%d_%H-%M-%S（到秒）；文件名时间可以清空，只剩 前缀_序号。"
                   "两种写法都认：%Y-%m-%d 或 [time(%Y-%m-%d)]。")

    def save_images(self, images, 保存模式, 输出文件夹, 文件名前缀,
                    文件名时间格式, 文件名分隔符, 文件名序号位数, 文件名序号起始,
                    图片格式, 质量, 重名处理, 嵌入工作流, 允许绝对路径,
                    输出路径_覆盖="", 额外文件名=None, prompt=None, extra_pnginfo=None):

        now = time.localtime()
        out_dir = resolve_output_dir(输出文件夹, 输出路径_覆盖, 允许绝对路径, now)

        prefix = 文件名前缀.strip() or default_prefix(输出文件夹, now)
        if 额外文件名:
            prefix = f"{prefix}{文件名分隔符}{额外文件名}"

        ext = 图片格式.lower()
        if ext == "jpeg":
            ext = "jpg"

        path, directory, stem = build_target(
            out_dir, 保存模式, prefix, 文件名时间格式,
            文件名分隔符, ext, 重名处理, 文件名序号位数, 文件名序号起始, now,
        )
        stem = sanitize_component(stem)

        batch = images
        if hasattr(batch, "shape") and len(batch.shape) == 3:
            batch = batch[None, ...]

        meta = build_metadata(prompt, extra_pnginfo) if 嵌入工作流 else {}
        saved_paths = []
        ui_images = []

        # 批次内序号必须跟着上一张真实落盘的序号走：
        # 比如第二次运行同一秒，第一张会落到 0004，那么后面就应该是 0005、0006，
        # 而不是从 文件名序号起始 重新数（那样会跟已有文件撞名并覆盖）。
        next_index = 文件名序号起始
        for i in range(batch.shape[0]):
            path, used_index = unique_path_indexed(
                directory, stem, ext, 重名处理, 文件名序号位数, next_index)
            next_index = used_index + 1
            pil = tensor_to_pil(batch[i])

            save_kwargs = {}
            if ext == "png":
                save_kwargs["compress_level"] = max(0, min(9, round((100 - int(质量)) / 100 * 9)))
                if meta:
                    pnginfo = PngInfo()
                    for key, value in meta.items():
                        pnginfo.add_text(key, value)
                    save_kwargs["pnginfo"] = pnginfo
            elif ext in ("jpg", "webp"):
                save_kwargs["quality"] = int(质量)
                save_kwargs["optimize"] = True
                if ext == "webp":
                    save_kwargs["method"] = 4

            pil.save(path, **save_kwargs)
            saved_paths.append(str(path))

            location, inside = to_ui_location(path)
            if inside:
                ui_images.append(location)

        ui = {"images": ui_images[:1], "text": ["\n".join(saved_paths)]}
        return {"ui": ui, "result": (images, "\n".join(saved_paths), str(directory))}


# --------------------------------------------------------------------------- #
# 节点 2：按时间保存视频
# --------------------------------------------------------------------------- #

class MinuteSaveVideo:
    """把图像序列（+可选音频）编码成视频，写到「按系统时间」生成的目录。"""

    def __init__(self):
        self.output_dir = _OUTPUT_DIR
        self.type = "output"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "帧率": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.01}),
                "视频格式": (VIDEO_FORMATS, {"default": VIDEO_FORMATS[0]}),
                "CRF质量": ("INT", {"default": 19, "min": 0, "max": 51, "step": 1,
                                    "tooltip": "越小越清晰、文件越大；18~20 基本视觉无损"}),
                "保存模式": (SAVE_MODES, {"default": "文件夹+文件名",
                                          "tooltip": "文件夹+文件名 / 仅文件名 / 仅文件夹 / 都不加"}),
                "输出文件夹": ("STRING", {"default": DEFAULT_OUTPUT_FOLDER, "multiline": False,
                                          "tooltip": "相对 output 的文件夹，默认 %Y-%m-%d（当前日期）。\n"
                                                     "「前缀+时间」自由组合，也支持多级：\n"
                                                     "  我的系列示例%Y-%m-%d → output/我的系列示例2026-09-06/\n"
                                                     "  MiniMaxH3/%Y-%m-%d  → output/MiniMaxH3/2026-09-06/\n"
                                                     "清空则直接写到 output 根目录"}),
                "文件名前缀": ("STRING", {"default": "Video", "multiline": False,
                                          "tooltip": "用来区分类型 / 系列，默认 Video（也可填系列名「我的系列示例」）；"
                                                     "[time(...)] 令牌同样可用；留空则用「输出文件夹」的最后一段"}),
                "文件名时间格式": ("STRING", {"default": DEFAULT_FILE_TIME, "multiline": False,
                                              "tooltip": "默认 %Y-%m-%d_%H-%M-%S（到秒）。"
                                                         "只要分秒填 %H-%M-%S，只要分钟填 %H-%M，"
                                                         "清空则文件名不带时间，只剩 前缀_序号"}),
                "文件名分隔符": ("STRING", {"default": "_", "multiline": False}),
                "文件名序号位数": ("INT", {"default": 5, "min": 1, "max": 12, "step": 1}),
                "文件名序号起始": ("INT", {"default": 1, "min": 0, "max": 999999, "step": 1}),
                "重名处理": (OVERWRITE_MODES, {"default": OVERWRITE_MODES[0]}),
                "保存元数据": ("BOOLEAN", {"default": True,
                                           "tooltip": "把 prompt / 工作流写进视频容器元数据"}),
                "允许绝对路径": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "audio": ("AUDIO", {"tooltip": "接 VAE 解码出的音频，会一起写进 mp4"}),
                "输出路径_覆盖": ("STRING", {"default": "", "multiline": False,
                                              "tooltip": "留空则用「输出文件夹」。会追加在「输出文件夹」之后：\n"
                                                         "  output/<输出文件夹>/<输出路径_覆盖>/\n"
                                                         "填绝对路径需勾选「允许绝对路径」"}),
                "额外文件名": ("STRING", {"forceInput": True,
                                          "tooltip": "接一个字符串进来，会追加到文件名前缀后面"}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("完整路径", "文件夹")
    FUNCTION = "save_video"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = ("把图像（批次会自动串成视频）编码保存，可一起写入音频。"
                   "输出文件夹默认是当前日期（%Y-%m-%d），文件名默认带 %Y-%m-%d_%H-%M-%S（到秒）；"
                   "文件名时间可以清空，只剩 前缀_序号。")

    def save_video(self, images, 帧率, 视频格式, CRF质量, 保存模式, 输出文件夹,
                   文件名前缀, 文件名时间格式, 文件名分隔符,
                   文件名序号位数, 文件名序号起始, 重名处理, 保存元数据, 允许绝对路径,
                   audio=None, 输出路径_覆盖="", 额外文件名=None,
                   prompt=None, extra_pnginfo=None):

        now = time.localtime()
        out_dir = resolve_output_dir(输出文件夹, 输出路径_覆盖, 允许绝对路径, now)

        prefix = 文件名前缀.strip() or default_prefix(输出文件夹, now)
        if 额外文件名:
            prefix = f"{prefix}{文件名分隔符}{额外文件名}"

        ext = _FORMAT_ALIASES.get(视频格式, "mp4")
        path, directory, _ = build_target(
            out_dir, 保存模式, prefix, 文件名时间格式,
            文件名分隔符, ext, 重名处理, 文件名序号位数, 文件名序号起始, now,
        )

        frames = tensor_frames_to_uint8(images)
        metadata = None
        if 保存元数据:
            raw = build_metadata(prompt, extra_pnginfo)
            metadata = {f"comfy_{key}": value for key, value in raw.items()}
            metadata["comfy_created"] = time.strftime("%Y-%m-%d %H:%M:%S", now)

        encode_video(frames, 帧率, path, 视频格式, CRF质量, audio=audio, metadata=metadata)

        location, inside = to_ui_location(path)
        folder_display = location["subfolder"] if inside else str(path.parent)

        # 让 ComfyUI 前端能直接预览视频（历史记录 + 节点预览）
        ui = {
            "text": [str(path)],
            "gifs": [{
                "filename": path.name,
                "subfolder": location["subfolder"],
                "type": location["type"],
                "format": "video/x-matroska" if ext == "mkv" else f"video/{ext}",
            }] if inside else [],
        }
        return {"ui": ui, "result": (str(path), folder_display)}


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #

NODE_CLASS_MAPPINGS = {
    "MinuteSaveImage": MinuteSaveImage,
    "MinuteSaveVideo": MinuteSaveVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MinuteSaveImage": "⏱ 按时间保存图像 (Minute Save Image)",
    "MinuteSaveVideo": "⏱ 按时间保存视频 (Minute Save Video)",
}
