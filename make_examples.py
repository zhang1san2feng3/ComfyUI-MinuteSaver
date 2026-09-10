"""生成 examples/ 下的示例工作流 JSON（NodeDef 格式，可直接拖进 ComfyUI）。

用法::

    python make_examples.py

脚本会导入 nodes.py，用 INPUT_TYPES 校验每个节点的 widgets_values 数量，
避免以后改了输入项顺序 / 数量导致示例工作流加载错乱。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import nodes as N  # noqa: E402

EXAMPLES = HERE / "examples"
EXAMPLES.mkdir(exist_ok=True)

# 各节点 widgets_values 的顺序 = INPUT_TYPES()["required"] 的键顺序
REQUIRED_COUNTS = {
    t: len(spec["required"]) for t, spec in (
        ("MinuteSaveImage", N.MinuteSaveImage.INPUT_TYPES()),
        ("MinuteSaveVideo", N.MinuteSaveVideo.INPUT_TYPES()),
    )
}
print("required 输入项数量:", REQUIRED_COUNTS)

COMMON_NODE_PROPS = {"Node name for S&R": "Placeholder"}


def node(node_id, ntype, pos, size, widgets, inputs=None, outputs=None,
         title=None, order=0):
    entry = {
        "id": node_id,
        "type": ntype,
        "pos": list(pos),
        "size": list(size),
        "flags": {},
        "order": order,
        "mode": 0,
        "inputs": inputs or [],
        "outputs": outputs or [],
        "properties": {"Node name for S&R": ntype},
        "widgets_values": widgets,
    }
    if title:
        entry["title"] = title
    return entry


def link(link_id, origin_id, origin_slot, target_id, target_slot, ltype):
    return [link_id, origin_id, origin_slot, target_id, target_slot, ltype]


def workflow(nodes, links, last_node_id, last_link_id, groups=None, extra=None):
    return {
        "id": "00000000-0000-4000-8000-000000000000",
        "revision": 0,
        "last_node_id": last_node_id,
        "last_link_id": last_link_id,
        "nodes": nodes,
        "links": links,
        "groups": groups or [],
        "config": {},
        "extra": extra or {"ds": {"scale": 1.0, "offset": [0, 0]}},
        "version": 0.4,
    }


def port(name, ptype, links=None, slot=None, label=None):
    entry = {"name": name, "type": ptype, "links": links}
    if slot is not None:
        entry["slot_index"] = slot
    if label:
        entry["label"] = label
    return entry


# --------------------------------------------------------------------------- #
# 图像示例：EmptyLatentImage → KSampler → VAEDecode → MinuteSaveImage
# --------------------------------------------------------------------------- #

def build_image_example():
    nodes = [
        node(1, "CheckpointLoaderSimple", [40, 60], [320, 100],
             ["model.safetensors"],
             outputs=[port("MODEL", "MODEL", [1], 0),
                      port("CLIP", "CLIP", [2, 3], 1),
                      port("VAE", "VAE", [4], 2)],
             order=0),
        node(2, "CLIPTextEncode", [400, 60], [400, 120],
             ["best quality, 1girl"],
             inputs=[{"name": "clip", "type": "CLIP", "link": 2}],
             outputs=[port("CONDITIONING", "CONDITIONING", [5], 0)],
             title="正向提示词", order=2),
        node(3, "CLIPTextEncode", [400, 240], [400, 120],
             ["worst quality, low quality"],
             inputs=[{"name": "clip", "type": "CLIP", "link": 3}],
             outputs=[port("CONDITIONING", "CONDITIONING", [6], 0)],
             title="负向提示词", order=3),
        node(4, "EmptyLatentImage", [400, 420], [320, 110],
             [1024, 1024, 1],
             outputs=[port("LATENT", "LATENT", [7], 0)],
             order=1),
        node(5, "KSampler", [850, 60], [300, 480],
             [12345, "randomize", 20, 7.0, "euler", "normal", 1.0],
             inputs=[{"name": "model", "type": "MODEL", "link": 1},
                     {"name": "positive", "type": "CONDITIONING", "link": 5},
                     {"name": "negative", "type": "CONDITIONING", "link": 6},
                     {"name": "latent_image", "type": "LATENT", "link": 7}],
             outputs=[port("LATENT", "LATENT", [8], 0)],
             order=4),
        node(6, "VAEDecode", [1200, 60], [220, 60],
             [],
             inputs=[{"name": "samples", "type": "LATENT", "link": 8},
                     {"name": "vae", "type": "VAE", "link": 4}],
             outputs=[port("IMAGE", "IMAGE", [9], 0)],
             order=5),
        node(7, "MinuteSaveImage", [1470, 60], [330, 620],
             # 顺序必须与 INPUT_TYPES 的 required 顺序一致
             [
                 "文件夹+文件名",          # 保存模式
                 "%Y-%m-%d",               # 输出文件夹（可写「前缀+时间」，如 原始待查%Y-%m-%d）
                 "Image",                  # 文件名前缀（Image / Video / 系列名）
                 "%Y-%m-%d_%H-%M-%S",      # 文件名时间格式（到秒；清空则不带时间）
                 "_",                      # 文件名分隔符
                 4,                        # 文件名序号位数
                 1,                        # 文件名序号起始
                 "png",                    # 图片格式
                 95,                       # 质量
                 "按序号自动递增",          # 重名处理
                 True,                     # 嵌入工作流
                 False,                    # 允许绝对路径
                 "",                       # 输出路径_覆盖
             ],
             inputs=[{"name": "images", "type": "IMAGE", "link": 9}],
             title="⏱ 按时间保存图像", order=6),
    ]

    links = [
        link(1, 1, 0, 5, 0, "MODEL"),
        link(2, 1, 1, 2, 0, "CLIP"),
        link(3, 1, 1, 3, 0, "CLIP"),
        link(4, 1, 2, 6, 1, "VAE"),
        link(5, 2, 0, 5, 1, "CONDITIONING"),
        link(6, 3, 0, 5, 2, "CONDITIONING"),
        link(7, 4, 0, 5, 3, "LATENT"),
        link(8, 5, 0, 6, 0, "LATENT"),
        link(9, 6, 0, 7, 0, "IMAGE"),
    ]

    groups = [{
        "id": 1,
        "title": "最简出图 + 按时间保存",
        "bounding": [0, -20, 1850, 700],
        "color": "#3f789e",
        "flags": {},
    }]

    return workflow(nodes, links, 7, 9, groups)


# --------------------------------------------------------------------------- #
# 视频示例：LoadImage → 缩放 → MinuteSaveVideo（把一批图串成 mp4）
# --------------------------------------------------------------------------- #

def build_video_example():
    nodes = [
        node(1, "LoadImage", [40, 60], [320, 330],
             ["example.png", "image"],
             outputs=[port("IMAGE", "IMAGE", [1], 0),
                      port("MASK", "MASK", None, 1)],
             order=0),
        node(2, "ImageScale", [400, 60], [300, 130],
             ["lanczos", 832, 480, "disabled"],
             inputs=[{"name": "image", "type": "IMAGE", "link": 1}],
             outputs=[port("IMAGE", "IMAGE", [2], 0)],
             title="统一尺寸（可选）", order=1),
        node(3, "MinuteSaveVideo", [740, 60], [340, 620],
             [
                 24.0,                     # 帧率
                 "mp4 (h264)",             # 视频格式
                 19,                       # CRF质量
                 "文件夹+文件名",           # 保存模式
                 "MiniMaxH3/%Y-%m-%d",     # 输出文件夹（多级）
                 "我的系列示例",             # 文件名前缀（系列名）
                 "%Y-%m-%d_%H-%M-%S",      # 文件名时间格式（到秒）
                 "_",                      # 文件名分隔符
                 5,                        # 文件名序号位数
                 1,                        # 文件名序号起始
                 "按序号自动递增",          # 重名处理
                 True,                     # 保存元数据
                 False,                    # 允许绝对路径
                 "",                       # 输出路径_覆盖
             ],
             inputs=[{"name": "images", "type": "IMAGE", "link": 2}],
             title="⏱ 按时间保存视频", order=2),
    ]

    links = [
        link(1, 1, 0, 2, 0, "IMAGE"),
        link(2, 2, 0, 3, 0, "IMAGE"),
    ]

    groups = [{
        "id": 1,
        "title": "图像 → 视频，按时间（到分钟）保存",
        "bounding": [0, -20, 1120, 700],
        "color": "#9e6d3f",
        "flags": {},
    }]

    return workflow(nodes, links, 3, 2, groups)


def main():
    written = []
    for name, data in (
        ("save_image_with_time.json", build_image_example()),
        ("save_video_with_time.json", build_video_example()),
    ):
        # 校验 widgets_values 数量与 INPUT_TYPES 是否一致
        for entry in data["nodes"]:
            ntype = entry["type"]
            if ntype in REQUIRED_COUNTS:
                actual = len(entry["widgets_values"])
                expected = REQUIRED_COUNTS[ntype]
                if actual != expected:
                    raise SystemExit(
                        f"{name}: 节点 {ntype} 的 widgets_values 有 {actual} 项，"
                        f"但 INPUT_TYPES required 有 {expected} 项，请同步修改。"
                    )
        path = EXAMPLES / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
        print(f"写入 {path}")

    return written


if __name__ == "__main__":
    main()
