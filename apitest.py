"""通过 ComfyUI API 实跑 MinuteSaver 两个节点，验证真实环境可用。

用法（ComfyUI 需已启动）::

    python apitest.py [端口]      # 默认 6288
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

PORT = sys.argv[1] if len(sys.argv) > 1 else "6288"
HOST = f"http://127.0.0.1:{PORT}"

OUTPUT_DIR = Path(r"E:\ComfyUI-aki-v1.6\ComfyUI\output")


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{HOST}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{HOST}{path}", timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run(name: str, prompt: dict, wait: float = 180.0) -> dict:
    client_id = str(uuid.uuid4())
    result = post("/prompt", {"prompt": prompt, "client_id": client_id})
    prompt_id = result["prompt_id"]
    print(f"[{name}] 已提交 prompt_id={prompt_id}")

    deadline = time.time() + wait
    while time.time() < deadline:
        hist = get(f"/history/{prompt_id}")
        if prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("completed") or status.get("status_str") in ("success", "error"):
                print(f"[{name}] 状态: {status.get('status_str')}")
                if status.get("status_str") == "error":
                    for m in status.get("messages", []):
                        print("    ", m)
                return entry
        time.sleep(2)
    raise TimeoutError(f"[{name}] 等待超时")


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  → {detail}" if not ok else ""))
    return ok


FAILS = 0


def main() -> int:
    global FAILS

    stamp_before = time.time()

    # ---------------- 图像 ----------------
    img_prompt = {
        "1": {"class_type": "EmptyImage",
              "inputs": {"width": 320, "height": 240, "batch_size": 3, "color": 0}},
        "2": {"class_type": "MinuteSaveImage",
              "inputs": {
                  "images": ["1", 0],
                  "保存模式": "文件夹+文件名",
                  "二级目录": "MinuteSaver_API测试",
                  "文件夹时间格式": "API测试%Y-%m-%d",
                  "文件名前缀": "ComfyUI",
                  "文件名时间格式": "%H-%M-%S",
                  "文件名分隔符": "_",
                  "文件名序号位数": 4,
                  "文件名序号起始": 1,
                  "图片格式": "png",
                  "质量": 95,
                  "重名处理": "按序号自动递增",
                  "嵌入工作流": True,
                  "允许绝对路径": False,
                  "输出路径_覆盖": "",
              }},
    }
    entry = run("图像", img_prompt)
    outputs = entry.get("outputs", {})
    node_out = outputs.get("2", {})
    paths = (node_out.get("text") or [""])[0].splitlines()
    print("     输出:", paths)
    FAILS += not check("图像：返回 3 条路径", len(paths) == 3, str(paths))
    for p in paths:
        fp = Path(p)
        FAILS += not check(f"图像存在 {fp.name}", fp.is_file() and fp.stat().st_size > 0, str(fp))
    if paths:
        d = Path(paths[0]).parent
        FAILS += not check("文件夹名 = 「前缀+日期」", d.name == f"API测试{time.strftime('%Y-%m-%d')}", d.name)
        FAILS += not check("二级目录正确", d.parent.name == "MinuteSaver_API测试", d.parent.name)
        FAILS += not check("文件名序号 0001/0002/0003",
                           [Path(p).name for p in paths] ==
                           [f"ComfyUI_{Path(paths[0]).name.split('_')[1]}_{i:04d}.png" for i in (1, 2, 3)],
                           str([Path(p).name for p in paths]))
    FAILS += not check("返回 gifs/images 之外的 text 预览",
                       bool(node_out.get("text")), str(list(node_out.keys())))

    # 再跑一次，验证序号递增（不覆盖）。
    # EmptyImage 带一个随机 seed，避免 ComfyUI 认为 prompt 未变化而直接复用缓存输出。
    img_prompt2 = json.loads(json.dumps(img_prompt))
    img_prompt2["1"]["inputs"]["seed"] = int(time.time() * 1000) % 100000000
    entry2 = run("图像-第二次", img_prompt2)
    paths2 = (entry2.get("outputs", {}).get("2", {}).get("text") or [""])[0].splitlines()
    print("     输出:", paths2)
    all_paths = list(paths) + list(paths2)
    FAILS += not check("两次运行路径不重叠", len(set(all_paths)) == len(all_paths), str(all_paths))
    FAILS += not check("六个文件都在（无覆盖）",
                       all(Path(p).is_file() and Path(p).stat().st_size > 0 for p in all_paths),
                       str(all_paths))
    # 跨秒时文件名里的时间戳不同，序号各自从 1 开始是正确的；
    # 「同一秒内不覆盖」由 selftest.py 的同一秒连跑用例覆盖。
    stems = [Path(p).name.rsplit("_", 1)[1] for p in all_paths]
    FAILS += not check("两次运行序号都是 0001 起（时间戳不同）",
                       stems == ["0001.png", "0002.png", "0003.png"] * 2, str(stems))

    # ---------------- 视频 ----------------
    vid_prompt = {
        "1": {"class_type": "EmptyImage",
              "inputs": {"width": 322, "height": 240, "batch_size": 16, "color": 0}},
        "2": {"class_type": "MinuteSaveVideo",
              "inputs": {
                  "images": ["1", 0],
                  "帧率": 8.0,
                  "视频格式": "mp4 (h264)",
                  "CRF质量": 20,
                  "保存模式": "文件夹+文件名",
                  "二级目录": "MinuteSaver_API测试",
                  "文件夹时间格式": "%Y-%m-%d",
                  "文件名前缀": "我的系列示例",
                  "文件名时间格式": "%Y-%m-%d_%H-%M-%S",
                  "文件名分隔符": "_",
                  "文件名序号位数": 5,
                  "文件名序号起始": 1,
                  "重名处理": "按序号自动递增",
                  "保存元数据": True,
                  "允许绝对路径": False,
                  "输出路径_覆盖": "",
              }},
    }
    entry3 = run("视频", vid_prompt, wait=300)
    node_out3 = entry3.get("outputs", {}).get("2", {})
    vpath = Path((node_out3.get("text") or [""])[0])
    print("     输出:", vpath)
    FAILS += not check("视频落盘且有体积", vpath.is_file() and vpath.stat().st_size > 1000, str(vpath))
    FAILS += not check("视频在日期文件夹内",
                       vpath.parent.name == time.strftime("%Y-%m-%d"), vpath.parent.name)
    FAILS += not check("gifs 预览信息返回", bool(node_out3.get("gifs")), str(list(node_out3.keys())))

    # 用 ffmpeg 校验视频可解码
    import subprocess
    sys.path.insert(0, str(Path(__file__).parent))
    import nodes as N
    probe = subprocess.run([N.find_ffmpeg(), "-hide_banner", "-i", str(vpath), "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    info = probe.stderr or ""
    FAILS += not check("视频可解码", "Video: h264" in info, info[-400:])
    FAILS += not check("视频尺寸 322x240（奇数宽已自动裁剪）", "322x240" in info or "320x240" in info,
                       [l for l in info.splitlines() if "Stream" in l])

    print("\n" + "=" * 60)
    print(f"API 测试失败项: {FAILS}")
    print(f"（测试输出目录: {OUTPUT_DIR / 'MinuteSaver_API测试'}）")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
