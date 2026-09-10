"""ComfyUI-MinuteSaver

按系统时间（精确到分钟）自动分文件夹 / 命名保存图像与视频。
Save images and videos into time-stamped folders / file names (minute precision).

Repository: https://github.com/zhang1san2feng3/ComfyUI-MinuteSaver
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

__version__ = "1.0.0"
