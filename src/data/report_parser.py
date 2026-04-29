"""
report_parser — 研报 PDF 获取与解析。

功能：
1. 从东财接口获取研报列表（含元数据、PDF链接）
2. 下载并用 pdfplumber 解析 PDF 文本（前 N 页）
3. 支持用户手动上传 PDF 研报（持久化存储）
4. 返回结构化研报列表，供 ResearchAgent 使用

设计原则：
- PDF 下载失败时降级为仅使用元数据
- 每份报告限制提取前 6 页（正文通常在前几页）
- 缓存 PDF 内容到本地文件，避免重复下载
- 用户上传研报存储于 uploads/research_pdfs/{stock_code}/
"""

import hashlib
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

_CACHE_DIR = Path(__file__).parent.parent.parent / "cache" / "research_pdfs"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── 用户上传目录 ──────────────────────────────────────────────
UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads" / "research_pdfs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}
_DOWNLOAD_TIMEOUT = 15
_MAX_PAGES = 6          # 每份报告最多解析前 N 页
_MAX_CHARS_PER_REPORT = 3000  # 每份报告提取正文上限字符数


def _pdf_cache_path(url: str) -> Path:
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    return _CACHE_DIR / f"{h}.txt"


def _download_pdf_text(url: str) -> Optional[str]:
    """下载 PDF 并提取文本；失败返回 None。"""
    cache_path = _pdf_cache_path(url)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    try:
        r = requests.get(url, headers=_HTTP_HEADERS, timeout=_DOWNLOAD_TIMEOUT)
        r.raise_for_status()
        if not r.content[:4] == b"%PDF":
            return None

        import pdfplumber
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            pages = pdf.pages[:_MAX_PAGES]
            texts = []
            for page in pages:
                t = page.extract_text() or ""
                if t.strip():
                    texts.append(t)
            full_text = "\n".join(texts)[:_MAX_CHARS_PER_REPORT]

        cache_path.write_text(full_text, encoding="utf-8")
        return full_text
    except Exception as e:
        print(f"[report_parser] PDF下载/解析失败: {url[:60]}... — {e}")
        return None


def fetch_research_reports(
    stock_code: str,
    limit: int = 8,
    fetch_pdf: bool = True,
) -> list[dict]:
    """
    获取研报列表，每条包含：
    {
        "title": str,
        "institution": str,
        "date": str,
        "rating": str,        # 东财评级（买入/增持/中性 等）
        "eps_forecast": dict, # {year: {"eps": float, "pe": float}}
        "pdf_url": str,
        "text": str,          # PDF 正文（可能为空字符串）
    }
    """
    import akshare as ak

    records = []
    try:
        df = ak.stock_research_report_em(symbol=stock_code)
        df = df.sort_values("日期", ascending=False).head(limit * 2)
    except Exception as e:
        print(f"[report_parser] 研报列表获取失败 {stock_code}: {e}")
        return []

    fetched = 0
    for _, row in df.iterrows():
        if fetched >= limit:
            break

        title = str(row.get("报告名称", ""))
        institution = str(row.get("机构", ""))
        date_val = str(row.get("日期", ""))
        rating = str(row.get("东财评级", ""))
        pdf_url = str(row.get("报告PDF链接", ""))

        # 提取盈利预测
        eps_forecast: dict[str, dict] = {}
        for yr in ["2024", "2025", "2026", "2027"]:
            eps_col = f"{yr}-盈利预测-收益"
            pe_col = f"{yr}-盈利预测-市盈率"
            if eps_col in row and row[eps_col] and str(row[eps_col]) not in ("nan", "None", ""):
                eps_forecast[yr] = {
                    "eps": float(row[eps_col]) if row[eps_col] else None,
                    "pe": float(row[pe_col]) if row.get(pe_col) and str(row[pe_col]) not in ("nan", "None", "") else None,
                }

        text = ""
        if fetch_pdf and pdf_url.startswith("http"):
            text = _download_pdf_text(pdf_url) or ""

        records.append({
            "title": title,
            "institution": institution,
            "date": date_val,
            "rating": rating,
            "eps_forecast": eps_forecast,
            "pdf_url": pdf_url,
            "text": text,
        })
        fetched += 1

    print(f"[report_parser] {stock_code}: 获取 {len(records)} 份研报，{sum(1 for r in records if r['text'])} 份含 PDF 正文")
    for r in records:
        r["source"] = "auto"   # 标记来源
    return records


# ── 用户上传研报 ──────────────────────────────────────────────

def _extract_pdf_bytes(pdf_bytes: bytes, max_pages: int = 8) -> str:
    """从 PDF 字节流中提取文字（pdfplumber）。"""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            texts = []
            for page in pdf.pages[:max_pages]:
                t = page.extract_text() or ""
                if t.strip():
                    texts.append(t)
            return "\n".join(texts)[:_MAX_CHARS_PER_REPORT]
    except Exception as e:
        print(f"[report_parser] PDF字节解析失败: {e}")
        return ""


def save_uploaded_report(
    stock_code: str,
    filename: str,
    pdf_bytes: bytes,
    institution: str = "",
    note: str = "",
) -> dict:
    """
    保存用户上传的 PDF 研报，持久化到 uploads/research_pdfs/{stock_code}/。
    返回与 fetch_research_reports() 相同格式的 dict（source="uploaded"）。
    """
    code_dir = UPLOAD_DIR / stock_code
    code_dir.mkdir(parents=True, exist_ok=True)

    # 用文件名哈希避免重名冲突
    h = hashlib.md5(pdf_bytes).hexdigest()[:8]
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    stem = Path(safe_name).stem
    pdf_path  = code_dir / f"{stem}_{h}.pdf"
    meta_path = code_dir / f"{stem}_{h}.json"

    # 保存 PDF
    pdf_path.write_bytes(pdf_bytes)

    # 提取正文
    text = _extract_pdf_bytes(pdf_bytes)

    meta = {
        "title":       filename.replace(".pdf", ""),
        "institution": institution or "用户上传",
        "date":        datetime.now().strftime("%Y-%m-%d"),
        "rating":      "",
        "eps_forecast": {},
        "pdf_url":     "",
        "text":        text,
        "source":      "uploaded",
        "filename":    filename,
        "note":        note,
        "upload_time": datetime.now().isoformat(),
        "_pdf_path":   str(pdf_path),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[report_parser] 用户上传研报已保存: {pdf_path.name}，正文{len(text)}字")
    return {k: v for k, v in meta.items() if not k.startswith("_")}


def load_uploaded_reports(stock_code: str) -> list[dict]:
    """读取 uploads/research_pdfs/{stock_code}/ 下所有已上传研报的元数据。"""
    code_dir = UPLOAD_DIR / stock_code
    if not code_dir.exists():
        return []
    records = []
    for meta_file in sorted(code_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            # 过滤内部字段
            records.append({k: v for k, v in data.items() if not k.startswith("_")})
        except Exception as e:
            print(f"[report_parser] 读取上传研报元数据失败 {meta_file}: {e}")
    return records


def delete_uploaded_report(stock_code: str, filename: str) -> bool:
    """删除指定上传研报（匹配 title 或 filename 字段）。"""
    code_dir = UPLOAD_DIR / stock_code
    if not code_dir.exists():
        return False
    for meta_file in code_dir.glob("*.json"):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            if data.get("filename") == filename or data.get("title") == filename:
                # 删除 JSON 和 PDF
                meta_file.unlink(missing_ok=True)
                pdf_path = Path(data.get("_pdf_path", ""))
                if pdf_path.exists():
                    pdf_path.unlink(missing_ok=True)
                return True
        except Exception:
            pass
    return False


def fetch_all_reports(stock_code: str, limit: int = 8) -> dict:
    """
    获取该股票的所有研报：自动获取 + 用户上传。

    返回:
    {
        "auto":     list[dict],   # AKShare 自动获取（含 source="auto"）
        "uploaded": list[dict],   # 用户上传（含 source="uploaded"）
    }
    """
    auto     = fetch_research_reports(stock_code, limit=limit, fetch_pdf=True)
    uploaded = load_uploaded_reports(stock_code)
    return {"auto": auto, "uploaded": uploaded}
