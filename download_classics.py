#!/usr/bin/env python3
"""
批量从 Wikisource 下载古籍文本
- 史记：卷001-卷130（已确认可访问）
- 战国策：各章节
- 自动解析正文，保存为UTF-8 TXT
"""

import re
import os
import time
import subprocess
import html as html_lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_DIR = os.path.join(SCRIPT_DIR, "sources")
os.makedirs(SOURCES_DIR, exist_ok=True)

# ── curl下载器（比Python urllib更稳定，处理TLS兼容性问题）────────────
def fetch(url, timeout=20):
    cmd = [
        'curl', '-sL', '--max-time', str(timeout),
        '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        '--tlsv1.2', url
    ]
    r = subprocess.run(cmd, capture_output=True)
    raw = r.stdout  # bytes
    return raw.decode('utf-8', errors='replace') if raw else None

def fetch_html(url, timeout=20):
    """获取HTML页面（返回str）"""
    return fetch(url, timeout)  # fetch 已返回 str


def clean_extracted_text(text):
    """清理 Wikisource 导航、编辑标记和百科噪音。"""
    text = html_lib.unescape(text).replace("\xa0", " ")
    text = re.sub(r'◄[^►\n]+►', ' ', text)
    text = re.sub(r'姊妹计划\s*:\s*数据项', ' ', text)
    text = re.sub(r'回主目錄|回主目录|閲文言|维基大典 文：|維基大典 文：|本维基文库', ' ', text)
    text = re.sub(r'维基百科\s*中的：|维基百科\s*條目：|維基百科\s*中的：|維基百科\s*條目：', ' ', text)
    text = re.sub(r'\[[^\]]*编辑[^\]]*\]', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'〈[^〉]{0,120}〉', ' ', text)
    text = re.sub(r'○', ' ', text)

    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(marker in line for marker in ("作者：", "姊妹计划", "数据项", "回主目錄", "回主目录")) and len(line) < 120:
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    sentences = [seg.strip() for seg in re.split(r'(?<=[。！？；])\s*', text) if len(seg.strip()) > 4]
    return "\n".join(sentences)

# ── 正文提取 ─────────────────────────────────────────────────────────
def extract_text(html):
    """从 Wikisource HTML 中提取正文"""
    if not html:
        return None
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<table[^>]*>.*?</table>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<sup[^>]*>.*?</sup>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</(p|div|li|tr|h\d)>', '\n', html, flags=re.IGNORECASE)

    # 提取正文区域
    text = None
    for pat in [
        r'<div[^>]*id="mw-content-text"[^>]*>(.*?)<div[^>]*class="printfooter"',
        r'<div[^>]*class="[^"]*mw-parser-output[^"]*"[^>]*>(.*?)</div>\s*</div>',
        r'<div[^>]*id="bodyContent"[^>]*>(.*?)<div[^>]*id="footer"',
    ]:
        m = re.search(pat, html, re.DOTALL)
        if m and len(m.group(1)) > 500:
            text = m.group(1)
            break

    if not text:
        text = html  # 备选

    # 剥除所有HTML标签
    text = re.sub(r'<[^>]+>', ' ', text)
    text = clean_extracted_text(text)
    return text if len(text) > 200 else None


def extract_volume_id(name):
    match = re.search(r'卷(\d{3})', name)
    return match.group(1) if match else None


def slice_between_headings(text, start_heading, end_heading):
    """截取两个标题之间的正文，标题本身保留、结束标题不保留。"""
    start_token = f"\n{start_heading}\n"
    end_token = f"\n{end_heading}\n"

    if text.startswith(start_heading + "\n"):
        start = 0
    else:
        start = text.find(start_token)
        if start == -1:
            return None
        start += 1

    end = text.find(end_token, start)
    if end == -1:
        return None

    return text[start:end].strip()

# ── 核心下载函数 ─────────────────────────────────────────────────────
def download_one(name, url):
    """下载单个文件，返回(成功bool, 文本长度)"""
    fname = os.path.join(SOURCES_DIR, f"{name}.txt")
    if os.path.exists(fname) and os.path.getsize(fname) > 200:
        txt_len = os.path.getsize(fname)
        print(f"  ⏭️  已存在: {name}.txt ({txt_len//2} chars)")
        return True, txt_len

    print(f"  📥 {url}")
    html = fetch_html(url)
    if not html or len(html) < 500:
        print(f"    ❌ 获取失败")
        return False, 0

    text = extract_text(html)
    if not text or len(text) < 200:
        print(f"    ❌ 解析失败（{len(text or '')} chars）")
        return False, 0

    with open(fname, 'w', encoding='utf-8') as f:
        f.write(f"# {name}\n# 来源: {url}\n# 时间: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(text)

    print(f"    ✅ {fname} ({len(text)} chars)")
    return True, len(text)

# ── 史记下载 ─────────────────────────────────────────────────────────
def download_shiji():
    print("📜 史记下载（130卷 + 重点世家）...")

    # 优先下载的周代相关章节（提前做，高优先级）
    priority = {
        '史記_卷004_周本紀':      'https://zh.wikisource.org/wiki/史記/卷004',
        '史記_卷014_十二諸侯年表': 'https://zh.wikisource.org/wiki/史記/卷014',
        '史記_卷015_六國年表':     'https://zh.wikisource.org/wiki/史記/卷015',
        '史記_卷040_晉世家':      'https://zh.wikisource.org/wiki/史記/卷040',
        '史記_卷041_楚世家':       'https://zh.wikisource.org/wiki/史記/卷041',
        '史記_卷044_趙世家':       'https://zh.wikisource.org/wiki/史記/卷044',
        '史記_卷045_魏世家':       'https://zh.wikisource.org/wiki/史記/卷045',
        '史記_卷033_魯周公世家':   'https://zh.wikisource.org/wiki/史記/卷033',
        '史記_卷038_衛康叔世家':   'https://zh.wikisource.org/wiki/史記/卷038',
        '史記_卷039_宋微子世家':   'https://zh.wikisource.org/wiki/史記/卷039',
        '史記_卷043_鄭世家':       'https://zh.wikisource.org/wiki/史記/卷043',
        '史記_卷046_田敬仲完世家': 'https://zh.wikisource.org/wiki/史記/卷046',
        '史記_卷005_秦本紀':       'https://zh.wikisource.org/wiki/史記/卷005',
    }
    covered_volumes = {extract_volume_id(name) for name in priority}
    # 其余卷
    for v in range(1, 131):
        vs = f"{v:03d}"
        if vs not in covered_volumes and f'史記_卷{vs}' not in priority:
            priority[f'史記_卷{vs}'] = f'https://zh.wikisource.org/wiki/史記/卷{vs}'

    ok = fail = 0
    seen_urls = set()
    for name, url in priority.items():
        if url in seen_urls:
            print(f"  ⏭️  跳过重复 URL: {name} -> {url}")
            continue
        ok_, _ = download_one(name, url)
        if ok_:
            ok += 1
            seen_urls.add(url)
        else:
            fail += 1
        time.sleep(0.4)

    print(f"  → 史记: {ok} 成功, {fail} 失败")
    return ok, fail

# ── 战国策下载 ──────────────────────────────────────────────────────
def download_zhanguoce():
    print("📜 战国策下载...")

    # 战国策分国别章节
    chapters = [
        # 周相关（最重要）
        "東周", "西周",
        # 主要诸侯国
        "秦一", "秦二", "秦三", "秦四",
        "齊一", "齊二", "齊三",
        "楚一", "楚二", "楚三",
        "趙一", "趙二", "趙三",
        "魏一", "魏二", "魏三",
        "韓一", "韓二",
        "燕一", "燕二",
        "中山",
    ]

    # 三个版本，依次尝试
    bases = [
        "https://zh.wikisource.org/wiki/戰國策_(士禮居叢書本)/",
        "https://zh.wikisource.org/wiki/戰國策_(姚宏續注本)/",
        "https://zh.wikisource.org/wiki/戰國策_(鮑彪注本)/",
    ]

    ok = fail = 0
    for ch in chapters:
        fname = os.path.join(SOURCES_DIR, f"戰國策_{ch}.txt")
        if os.path.exists(fname) and os.path.getsize(fname) > 100:
            print(f"  ⏭️  已存在: 戰國策_{ch}.txt")
            ok += 1
            continue

        success = False
        for base in bases:
            url = base + ch
            print(f"  📥 {url}")
            html = fetch_html(url)
            if not html:
                continue
            text = extract_text(html)
            if text and len(text) > 200:
                with open(fname, 'w', encoding='utf-8') as f:
                    f.write(f"# 戰國策 {ch}\n# 来源: {url}\n\n{text}")
                print(f"    ✅ {fname} ({len(text)} chars)")
                success = True
                time.sleep(0.4)
                break
            else:
                print(f"    ❌ 内容不足 ({len(text or '')} chars)")

        if not success:
            print(f"  ❌ 戰國策_{ch} 全部版本失败")
            fail += 1

        time.sleep(0.2)

    print(f"  → 战国策: {ok} 成功, {fail} 失败")
    return ok, fail


def download_zhushu_jinian_warring_states():
    print("📜 古本竹書紀年（戰國段）下载...")

    name = "古本竹書紀年_戰國"
    fname = os.path.join(SOURCES_DIR, f"{name}.txt")
    url = "https://zh.wikisource.org/wiki/古本竹書紀年輯校"
    if os.path.exists(fname) and os.path.getsize(fname) > 200:
        txt_len = os.path.getsize(fname)
        print(f"  ⏭️  已存在: {name}.txt ({txt_len//2} chars)")
        return True, txt_len

    print(f"  📥 {url}")
    html = fetch_html(url)
    if not html or len(html) < 500:
        print("    ❌ 获取失败")
        return False, 0

    text = extract_text(html)
    if not text or len(text) < 200:
        print(f"    ❌ 解析失败（{len(text or '')} chars）")
        return False, 0

    section = slice_between_headings(text, "幽公", "附 無年世可繫者")
    if not section or len(section) < 200:
        print(f"    ❌ 战国段截取失败（{len(section or '')} chars）")
        return False, 0

    with open(fname, 'w', encoding='utf-8') as f:
        f.write(f"# {name}\n")
        f.write(f"# 来源: {url}\n")
        f.write(f"# 时间: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write("# 范围: 晉紀 幽公、烈公；魏紀 武侯、梁惠成王、今王\n\n")
        f.write(section)

    print(f"    ✅ {fname} ({len(section)} chars)")
    return True, len(section)

# ── 主程序 ──────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  古籍批量下载器")
    print("  来源: zh.wikisource.org")
    print("=" * 60)

    ok1, f1 = download_shiji()
    print()
    ok2, f2 = download_zhanguoce()
    print()
    ok3, f3 = download_zhushu_jinian_warring_states()

    # 汇总
    files = [f for f in os.listdir(SOURCES_DIR) if f.endswith('.txt')]
    total = sum(os.path.getsize(os.path.join(SOURCES_DIR, f)) for f in files)
    print()
    print("=" * 60)
    print(f"  ✅ 下载完成！")
    print(f"  总文件: {len(files)} 个")
    print(f"  总大小: {total/1024:.0f} KB")
    print(f"  史记: {ok1} 成功, {f1} 失败")
    print(f"  战国策: {ok2} 成功, {f2} 失败")
    print(f"  古本竹書紀年（戰國段）: {1 if ok3 else 0} 成功, {0 if ok3 else 1} 失败")
    print(f"  保存位置: {SOURCES_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    main()
