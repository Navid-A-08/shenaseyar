"""Pure display helpers for app.py (testable without Streamlit)."""
import csv
import re

from src.detect.explain import TAX_STATUS_FA, fmt_num

FIELDS = ["issue_date", "sstid", "sstt", "am", "fee", "vra", "vam"]
LABELS_FA = {"issue_date": "تاریخ صدور (YYYY-MM-DD شمسی)", "sstid": "شناسه کالا/خدمت (sstid)",
             "sstt": "شرح کالا/خدمت (sstt)", "am": "مقدار (am)", "fee": "مبلغ واحد، ریال (fee)",
             "vra": "نرخ مالیات، درصد (vra)", "vam": "مبلغ مالیات، ریال (vam)"}
SEVERITY_FA = {"high": "زیاد", "medium": "متوسط", "low": "کم", "none": "اطلاعاتی"}
TERM_FA = {"text_mismatch": "ناهمخوانی شرح با شناسه (BM25)",
           "rank_declared": "رتبه شناسه اعلام‌شده (BM25)",
           "confident_alternative": "وجود جایگزین مطمئن (BM25)"}
_MD = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~])")


def md_escape(text):
    """Escape Markdown/HTML so data (catalog titles, invoice text) renders as plain text."""
    return _MD.sub(r"\\\1", str(text))


def read_demo_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def hit_rows(result):
    return [{"کد": h.code, "شدت": SEVERITY_FA[h.severity], "قاعده": h.description}
            for h in result.hits]


def contribution_rows(result):
    return [{"جزء": TERM_FA.get(k, k), "سهم": round(v, 3)}
            for k, v in result.contributions.items() if v]


def alternative_rows(result):
    return [{"رتبه": i, "شناسه": c.sstid, "عنوان": c.version.title.replace("\n", " "),
             "نرخ": fmt_num(c.version.rate), "وضعیت": TAX_STATUS_FA[c.version.tax_status],
             "نوع": c.version.id_type, "امتیاز BM25": round(c.score, 2),
             "اعلام‌شده": "✓" if c.sstid == result.line.sstid else ""}
            for i, c in enumerate(result.alternatives, 1)]


def rate_status_text(result):
    r = result.rate
    if r.version is None:
        return f"وضعیت شناسه در تاریخ صدور: {r.status.value}"
    v = r.version
    end = v.valid_to_incl or "اکنون"
    return (f"نرخ ثبت‌شده برای شناسه در تاریخ صدور: {fmt_num(v.rate)}٪ "
            f"({TAX_STATUS_FA[v.tax_status]}، معتبر از {v.valid_from} تا {end})")
