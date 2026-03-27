"""
《资治通鉴·周纪》智能比对 Agent — Gradio Web UI
"""

import gradio as gr
import sys, os
import httpx
import gradio.blocks as gr_blocks
import gradio.networking as gr_networking

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from zztj_agent import analyze_zztj_text, build_index

CSS = """
#title-block {text-align:center;padding:20px 0 10px;}
"""

_ORIGINAL_HTTPX_REQUEST = httpx.request
_ORIGINAL_HTTPX_GET = httpx.get
_ORIGINAL_HTTPX_HEAD = httpx.head


def _should_bypass_proxy(url) -> bool:
    return isinstance(url, str) and url.startswith(
        ("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")
    )


def _localhost_safe_httpx_request(method, url, *args, **kwargs):
    """Gradio 启动时会回调本地接口；对回环地址禁用代理。"""
    if _should_bypass_proxy(url):
        kwargs.setdefault("trust_env", False)
    return _ORIGINAL_HTTPX_REQUEST(method, url, *args, **kwargs)


def _localhost_safe_httpx_get(url, *args, **kwargs):
    if _should_bypass_proxy(url):
        kwargs.setdefault("trust_env", False)
    return _ORIGINAL_HTTPX_GET(url, *args, **kwargs)


def _localhost_safe_httpx_head(url, *args, **kwargs):
    if _should_bypass_proxy(url):
        kwargs.setdefault("trust_env", False)
    return _ORIGINAL_HTTPX_HEAD(url, *args, **kwargs)


httpx.request = _localhost_safe_httpx_request
httpx.get = _localhost_safe_httpx_get
httpx.head = _localhost_safe_httpx_head
gr_blocks.httpx.request = _localhost_safe_httpx_request
gr_blocks.httpx.get = _localhost_safe_httpx_get
gr_blocks.httpx.head = _localhost_safe_httpx_head
gr_networking.httpx.request = _localhost_safe_httpx_request
gr_networking.httpx.get = _localhost_safe_httpx_get
gr_networking.httpx.head = _localhost_safe_httpx_head

def main():
    with gr.Blocks(css=CSS, title="资治通鉴周纪智能比对Agent") as demo:
        gr.Markdown("""
        <div id="title-block">
            <h1>📜 资治通鉴·周纪 智能比对 Agent</h1>
            <p>
                输入任意一段《资治通鉴·周纪》原文 → 自动检索原始史料 → 定位最相似窗口 → 比较窗口与输入文本差异
                <br><b>⚠️ 注意：Ollama 需先启动（brew services start ollama）</b>
            </p>
        </div>
        """, elem_id="title-block")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📥 输入《周纪》原文")
                input_box = gr.Textbox(
                    placeholder="粘贴《资治通鉴·周纪》一段正文，例如：\n威烈王二十三年，初命晋大夫魏斯、趙籍、韓虔為諸侯……",
                    lines=10,
                    label="周纪文本",
                )
                submit_btn = gr.Button("🔍 开始分析", variant="primary", size="lg")
                gr.Markdown("""
                **使用说明**：
                1. 先确保 Ollama 已运行：`brew services start ollama`
                2. 粘贴或输入一段《资治通鉴·周纪》原文
                3. 点击「开始分析」，等待 10-30 秒（理论上来说这么长时间够用，如果还没召回答案你就老老实实删点本地的sources吧呵呵）
                4. 查看史料来源 + 最相似窗口 + LLM 对比分析报告
                5. 若文本包含 `臣光曰` 按语，系统会自动忽略其后的内容再检索

                **数据说明**：所有分析完全本地运行，数据不上传网络。
                """)

            with gr.Column(scale=2):
                gr.Markdown("### 📊 分析报告")
                output_md = gr.Markdown(
                    value="⬆️ 在左侧输入《周纪》文本，点击「开始分析」",
                    label="分析结果",
                )

        gr.Examples(
            examples=[[
                "威烈王二十三年，初命晉大夫魏斯、趙籍、韓虔為諸侯。"
            ]],
            inputs=input_box,
            label="📌 示例文本（点击自动填入）",
        )

        submit_btn.click(fn=analyze_zztj_text, inputs=input_box, outputs=output_md)
        input_box.submit(fn=analyze_zztj_text, inputs=input_box, outputs=output_md)

    print("🚀 启动 Gradio UI: http://localhost:7860")
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=False,
    )

if __name__ == "__main__":
    main()
