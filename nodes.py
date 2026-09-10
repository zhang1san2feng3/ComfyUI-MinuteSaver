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
图像::

    output/ComfyUI_Image/2026-09-06/ComfyUI_16-13_00001.png

视频::

    output/ComfyUI_Video/2026-09-06/MiniMaxH3_16-13_00001.mp4

令牌一览（文件夹格式 / 文件名时间 / 文件名前缀 / 额外文件名 都能用）
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

DEFAULT_FOLDER_TIME = "%Y-%m-%d"
DEFAULT_FILE_TIME = "%H-%M"

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


def fmt_time(template: str, now: time.struct_time) -> str:
    """把「时间格式」字段渲染成字符串。

    两种写法都支持，方便两种习惯：

    * ``%Y-%m-%d``                 → 裸 strftime，直接格式化
    * ``[time(%Y-%m-%d)]``         → WAS Node Suite 风格令牌
    * ``存档[time(%m-%d)]``        → 令牌嵌在文字里（此时整串做令牌解析）

    先整体做一次令牌解析；若渲染结果显示「没有时间信息」而模板里又确实带了
    ``%`` 格式符，则再按裸 strftime 解释一次。
    """
    if not template or not template.strip():
        return ""

    expanded = parse_tokens(template, now)
    if expanded != template:
        return expanded

    if "%" in template:
        try:
            return time.strftime(template, now)
        except Exception:
            return template
    return template


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

def resolve_base_dir(output_path: str, allow_absolute: bool) -> Path:
    """输出根目录：绝对路径（需允许）或 ComfyUI 的 output 目录。"""
    raw = output_path.strip()
    if not raw:
        return _OUTPUT_DIR
    expanded = parse_tokens(raw)
    if allow_absolute and Path(expanded).is_absolute():
        return Path(expanded)
    # 非绝对路径统一落到 output/<相对路径>，避免意外写到别处
    return _OUTPUT_DIR


def resolve_extra_dir(output_path: str, allow_absolute: bool) -> Path:
    """当 output_path 是相对路径时，它同时作为追加的子目录。"""
    raw = output_path.strip()
    if not raw:
        return Path()
    expanded = parse_tokens(raw)
    if allow_absolute and Path(expanded).is_absolute():
        return Path()
    return Path(sanitize_relative_path(strip_leading_abs(expanded)))


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


def build_target(base_dir: Path, extra_dir: Path, save_mode: str,
                 folder_time: str, prefix: str, file_time: str,
                 delimiter: str, ext: str, overwrite_mode: str,
                 padding: int, start: int, now: time.struct_time):
    """统一的落盘路径计算，图像 / 视频节点共用。返回 (完整路径, 目录, 文件名主干)。

    四种模式的语义：

    * ``文件夹+文件名``：时间进文件夹，也进文件名
    * ``仅文件名``    ：时间只进文件名
    * ``仅文件夹``    ：时间只进文件夹，文件名只剩 前缀_序号
    * ``都不加``      ：两边都不加时间，文件名只剩 前缀_序号
    """
    time_in_folder = save_mode in ("文件夹+文件名", "仅文件夹")
    time_in_name = save_mode in ("文件夹+文件名", "仅文件名")

    folder_text = fmt_time(folder_time, now) if time_in_folder else ""

    prefix = (prefix or "").strip() or "ComfyUI"
    file_time = (file_time or "").strip()

    parts = [prefix]
    if time_in_name and file_time:
        rendered = fmt_time(file_time, now)
        if rendered:
            parts.append(rendered)
    stem = delimiter.join(parts)

    subfolder = Path(sanitize_relative_path(folder_text))
    directory = base_dir / extra_dir / subfolder
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
                "二级目录": ("STRING", {"default": "ComfyUI_Image", "multiline": False,
                                        "tooltip": "output 下的二级目录，可留空直接写 output 根目录"}),
                "文件夹时间格式": ("STRING", {"default": DEFAULT_FOLDER_TIME, "multiline": False,
                                              "tooltip": "strftime 格式，默认 %Y-%m-%d（每天一个文件夹）"}),
                "文件名前缀": ("STRING", {"default": "ComfyUI", "multiline": False,
                                          "tooltip": "[time(...)] 令牌同样可用，留空则用二级目录名"}),
                "文件名时间格式": ("STRING", {"default": DEFAULT_FILE_TIME, "multiline": False,
                                              "tooltip": "默认 %H-%M（精确到分钟）；清空则文件名不带时间"}),
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
                                              "tooltip": "留空则用「二级目录」。绝对路径需勾选允许绝对路径；"
                                                         "相对路径会作为追加子目录"}),
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
    DESCRIPTION = ("按系统时间自动命名保存图像。时间令牌 [time(格式)] 可放在文件夹或文件名上，"
                   "默认文件名带 _%H-%M 精确到分钟，同分钟内序号递增、跨分钟自动换新。")

    def save_images(self, images, 保存模式, 二级目录, 文件夹时间格式, 文件名前缀,
                    文件名时间格式, 文件名分隔符, 文件名序号位数, 文件名序号起始,
                    图片格式, 质量, 重名处理, 嵌入工作流, 允许绝对路径,
                    输出路径_覆盖="", 额外文件名=None, prompt=None, extra_pnginfo=None):

        now = time.localtime()
        base_dir = resolve_base_dir(输出路径_覆盖, 允许绝对路径)
        extra_dir = resolve_extra_dir(输出路径_覆盖, 允许绝对路径)
        if not 输出路径_覆盖.strip():
            extra_dir = Path(sanitize_relative_path(parse_tokens(二级目录, now)))

        prefix = 文件名前缀.strip() or (二级目录.strip() if 输出路径_覆盖.strip() else "ComfyUI")
        if 额外文件名:
            prefix = f"{prefix}{文件名分隔符}{parse_tokens(str(额外文件名), now)}"

        ext = 图片格式.lower()
        if ext == "jpeg":
            ext = "jpg"

        path, directory, stem = build_target(
            base_dir, extra_dir, 保存模式, 文件夹时间格式, prefix, 文件名时间格式,
            文件名分隔符, ext, 重名处理, 文件名序号位数, 文件名序号起始, now,
        )
        stem = sanitize_component(stem)

        batch = images
        if hasattr(batch, "shape") and len(batch.shape) == 3:
            batch = batch[None, ...]

        meta = build_metadata(prompt, extra_pnginfo) if 嵌入工作流 else {}
        saved_paths = []
        ui_images = []

        for i in range(batch.shape[0]):
            if i > 0:      # 批次里的后续帧往后顺延序号
                path = unique_path(directory, stem, ext, 重名处理,
                                   文件名序号位数, 文件名序号起始 + i)
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
                "二级目录": ("STRING", {"default": "MiniMaxH3", "multiline": False,
                                        "tooltip": "output 下的二级目录，可留空"}),
                "文件夹时间格式": ("STRING", {"default": DEFAULT_FOLDER_TIME, "multiline": False,
                                              "tooltip": "同图像节点： %Y-%m-%d 或 [time(%Y-%m-%d)]"}),
                "文件名前缀": ("STRING", {"default": "ComfyUI", "multiline": False,
                                          "tooltip": "例如填系列名「我的系列示例」；[time(...)] 令牌同样可用"}),
                "文件名时间格式": ("STRING", {"default": DEFAULT_FILE_TIME, "multiline": False,
                                              "tooltip": "默认 %H-%M（精确到分钟）；清空则文件名只留 前缀_序号"}),
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
                                              "tooltip": "留空则用「二级目录」"}),
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
                   "时间令牌 [time(格式)] 可放在文件夹或文件名上，默认文件名带 _%H-%M 精确到分钟。")

    def save_video(self, images, 帧率, 视频格式, CRF质量, 保存模式, 二级目录,
                   文件夹时间格式, 文件名前缀, 文件名时间格式, 文件名分隔符,
                   文件名序号位数, 文件名序号起始, 重名处理, 保存元数据, 允许绝对路径,
                   audio=None, 输出路径_覆盖="", 额外文件名=None,
                   prompt=None, extra_pnginfo=None):

        now = time.localtime()
        base_dir = resolve_base_dir(输出路径_覆盖, 允许绝对路径)
        extra_dir = resolve_extra_dir(输出路径_覆盖, 允许绝对路径)
        if not 输出路径_覆盖.strip():
            extra_dir = Path(sanitize_relative_path(parse_tokens(二级目录, now)))

        prefix = 文件名前缀.strip() or (二级目录.strip() if 输出路径_覆盖.strip() else "ComfyUI")
        if 额外文件名:
            prefix = f"{prefix}{文件名分隔符}{parse_tokens(str(额外文件名), now)}"

        ext = _FORMAT_ALIASES.get(视频格式, "mp4")
        path, directory, _ = build_target(
            base_dir, extra_dir, 保存模式, 文件夹时间格式, prefix, 文件名时间格式,
            文件名分隔符, ext, 重名处理, 文件名序号位数, 文件名序号起始, now,
        )

        frames = tensor_frames_to_uint8(images)
        metadata = None
        if 保存元数据:
            raw = build_metadata(prompt, extra_pnginfo)
            metadata = {f"comfy_{key}": value for key, value in raw.items()}
            metadata["comfy_created"] = time.strftime("%Y-%m-%d %H:%M:%S", now)

        encode_video(frames, 帧率, path, 视频格式, CRF质量, audio=audio, metadata=metadata)

        _, inside = to_ui_location(path)
        folder_display = to_ui_location(path)[0]["subfolder"] if inside else str(path.parent)

        ui = {"text": [str(path)], "gifs": []}
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
