// ComfyUI-MinuteSaver — 前端扩展
//
// 作用：
//  1. 节点执行完成后，把「本次实际保存到的完整路径」显示到节点上（只读输入框）。
//  2. 提供一个「📂 查看本次路径」按钮，点击可复制最近一次路径。
//
// 注意：这里刻意不实时推算路径——路径里的时间令牌是按「执行那一刻」的系统时间渲染的，
// 画布上实时显示反而会误导，所以只在执行结束后显示真实结果。

import { app } from "../../scripts/app.js";

const TARGET_NODES = ["MinuteSaveImage", "MinuteSaveVideo"];

function findWidget(node, ...candidates) {
    if (!node.widgets) return null;
    for (const name of candidates) {
        const found = node.widgets.find((w) => w.name === name);
        if (found) return found;
    }
    return null;
}

function ensurePathWidget(node) {
    if (node.__minuteSaverPathWidget) return node.__minuteSaverPathWidget;

    const widget = node.addWidget("text", "上次保存路径 (只读)", "", () => {});
    if (widget.inputEl) {
        widget.inputEl.readOnly = true;
        widget.inputEl.style.cssText += [
            "background: rgba(56, 189, 248, 0.06) !important",
            "border: 1px solid rgba(56, 189, 248, 0.25) !important",
            "color: #7dd3fc !important",
            "font-size: 11px !important",
        ].join(";");
    }
    node.__minuteSaverPathWidget = widget;
    return widget;
}

function setPathText(node, text) {
    const widget = ensurePathWidget(node);
    widget.value = text || "";
    if (widget.inputEl) widget.inputEl.value = widget.value;
    node.__minuteSaverLastPath = text || "";
    node.setSize([Math.max(node.size[0], 320), node.size[1]]);
    app.graph.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "MinuteSaver.PathDisplay",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!TARGET_NODES.includes(nodeData?.name)) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);
            ensurePathWidget(this);

            this.addWidget("button", "📂 复制上次路径", null, async () => {
                const text = this.__minuteSaverLastPath;
                if (!text) {
                    alert("还没有保存记录，先执行一次工作流。");
                    return;
                }
                try {
                    await navigator.clipboard.writeText(text);
                } catch (err) {
                    console.warn("[MinuteSaver] 剪贴板写入失败", err);
                }
            });
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            const out = message?.text?.[0];
            if (out) setPathText(this, out);
        };
    },
});
