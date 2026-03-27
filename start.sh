#!/bin/zsh
# 《资治通鉴·周纪》智能比对 Agent — 一键启动脚本

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/venv}"
VENV_PYTHON="$VENV_DIR/bin/python"
LLM_MODEL="${ZZTJ_LLM_MODEL:-qwen2.5:3b}"

echo "============================================"
echo "  资治通鉴周纪智能比对 Agent"
echo "============================================"

# 1. 准备 Python 虚拟环境
cd "$PROJECT_DIR"
if [ ! -x "$VENV_PYTHON" ]; then
    echo "📦 创建 Python 虚拟环境..."
    python3 -m venv "$VENV_DIR"
fi

if ! "$VENV_PYTHON" -c "import chromadb, gradio, httpx, sentence_transformers, opencc" > /dev/null 2>&1; then
    echo "📦 安装项目依赖..."
    "$VENV_PYTHON" -m pip install --upgrade pip
    "$VENV_PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
fi

# 2. 确保 Ollama 在运行
if ! curl -s http://127.0.0.1:11434 > /dev/null 2>&1; then
    echo "⚠️  Ollama 未运行，正在启动..."
    if command -v brew > /dev/null 2>&1; then
        brew services start ollama
    else
        echo "❌ 未检测到 brew，请先手动启动 Ollama。"
        exit 1
    fi
    sleep 2
fi

# 3. 检查模型
echo "📦 检查 Ollama 模型..."
if ! ollama list | grep -q "$LLM_MODEL"; then
    echo "📥 本地缺少模型 $LLM_MODEL，正在拉取..."
    ollama pull "$LLM_MODEL"
fi

# 4. 使用虚拟环境运行 Gradio
echo ""
echo "🚀 启动 Gradio Web UI..."
echo "   访问地址: http://localhost:7860"
echo ""
"$VENV_PYTHON" app.py
