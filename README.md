# 资治通鉴·周纪 智能比对 Agent

本地 RAG + 本地 LLM 工具。输入《资治通鉴·周纪》文本后，程序会自动检索原始史料，定位最相似片段，对比文本差异，并生成基于证据的改写分析。

## 功能

- 史料检索：基于向量数据库检索《史记》《战国策》等原始典籍
- 文本比对：展示最相似史料窗口，辅助观察删改压缩
- LLM 分析：用本地 Ollama 模型解释文本差异
- Web UI：通过 Gradio 在浏览器中使用
- 本地运行：数据和推理都在本机完成

## 环境要求

- macOS
- Python 3.11+
- Git
- Ollama
- Homebrew

## 快速启动

推荐直接运行：

```bash
cd ~/Desktop/zztj-agent
./start.sh
```

`start.sh` 会自动完成这些步骤：

- 创建 `venv/`
- 安装 `requirements.txt` 中的依赖
- 检查并启动 Ollama
- 检查并拉取默认模型 `qwen2.5:3b`
- 启动 Gradio Web UI

启动后访问 [http://localhost:7860](http://localhost:7860)。

## 手动启动

```bash
git clone <你的仓库地址>
cd zztj-agent

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

brew services start ollama
ollama pull qwen2.5:3b

python app.py
```

## 数据准备

项目会从 `sources/` 目录读取史料文本。

当前推荐做法是：把 `sources/` 一并提交到 GitHub。这样别人克隆后就已经带有史料文本，可以直接构建索引并运行。

如果你不想把史料文本放进仓库，就需要让对方自行准备 `sources/` 下的 `.txt` 文件，例如：

```text
zztj-agent/
└── sources/
    ├── 史記_卷004_周本紀.txt
    ├── 史記_卷040_晉世家.txt
    ├── 史記_卷044_趙世家.txt
    ├── 戰國策_東周.txt
    └── 戰國策_西周.txt
```

首次运行时会自动构建 `index_storage/` 向量索引。这个目录是本地生成物，不建议上传到 GitHub。

注意：Ollama 模型本身不会放进 GitHub 仓库。别人第一次运行时，`start.sh` 会在他们自己的电脑上自动拉取默认模型 `qwen2.5:3b`。

## 项目结构

```text
zztj-agent/
├── app.py
├── zztj_agent.py
├── start.sh
├── requirements.txt
├── download_classics.py
├── tests/
├── docs/
├── sources/
├── index_storage/      # 本地生成，不建议提交
└── README.md
```

## 上传到 GitHub

这个项目完全可以上传到 GitHub，而且很适合这样分享。

推荐上传：

- 源码：`app.py`、`zztj_agent.py`、`download_classics.py`
- 说明文件：`README.md`
- 启动脚本：`start.sh`
- 依赖文件：`requirements.txt`
- 测试：`tests/`
- 文档：`docs/`
- 史料文本：`sources/`（建议一起上传，这样别人拉下来就能直接运行）

不要上传：

- `venv/`
- `.venv/`
- `bin/`
- `include/`
- `lib/`
- `share/`
- `index_storage/`
- `__pycache__/`
- `.pytest_cache/`
- `app.log`
- `download.log`

仓库里已经配置了 `.gitignore`，会自动忽略这些本地文件。

首次推送示例：

```bash
cd ~/Desktop/zztj-agent
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <你的 GitHub 仓库地址>
git push -u origin main
```

如果你还没有 GitHub 仓库：

1. 在 GitHub 新建一个空仓库
2. 不要勾选自动生成 README
3. 复制仓库地址
4. 回到本地执行上面的 `git remote add origin ...`

## 怎么发给别人本地部署

最推荐的做法是把 GitHub 地址发给对方，让对方执行：

```bash
git clone <你的仓库地址>
cd zztj-agent
./start.sh
```

因为你会把 `sources/` 也提交进仓库，所以对方不需要再手动找史料文件。首次启动时只会多做两件事：

- 在本机创建 Python 虚拟环境并安装依赖
- 基于仓库里的 `sources/` 自动构建本地向量索引

如果对方还没安装 Ollama：

```bash
brew install ollama
brew services start ollama
```

## 不用 GitHub 也可以

如果你想直接发压缩包，也可以。但发之前建议先删掉本地生成物，避免包太大：

```bash
rm -rf venv index_storage __pycache__ .pytest_cache
```

然后把项目目录压缩发给对方。对方解压后执行：

```bash
cd zztj-agent
./start.sh
```

## 模型切换

默认模型是 `qwen2.5:3b`，也可以通过环境变量切换：

```bash
ZZTJ_LLM_MODEL=qwen2.5:7b python app.py
```

也支持调参：

```bash
ZZTJ_LLM_MODEL=qwen2.5:7b \
ZZTJ_LLM_TEMPERATURE=0.15 \
ZZTJ_LLM_NUM_CTX=8192 \
ZZTJ_LLM_NUM_PREDICT=1200 \
python app.py
```
