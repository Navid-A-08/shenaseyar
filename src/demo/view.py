"""Pure display helpers for app.py (testable without Streamlit)."""
import csv
import math
import re

from src.detect.engine import InvoiceLine, LineError
from src.detect.explain import TAX_STATUS_FA, fmt_num
from src.detect.score import FLOOR_TERM, HIGH_RISK

FIELDS = ["issue_date", "sstid", "sstt", "am", "fee", "vra", "vam"]
LABELS_FA = {"issue_date": "تاریخ صدور (YYYY-MM-DD شمسی)", "sstid": "شناسه کالا/خدمت (sstid)",
             "sstt": "شرح کالا/خدمت (sstt)", "am": "مقدار (am)", "fee": "مبلغ واحد، ریال (fee)",
             "vra": "نرخ مالیات، درصد (vra)", "vam": "مبلغ مالیات، ریال (vam)"}
SEVERITY_FA = {"high": "زیاد", "medium": "متوسط", "low": "کم", "none": "اطلاعاتی"}
TERM_FA = {"text_mismatch": "ناهمخوانی شرح با شناسه (BM25)",
           "rank_declared": "رتبه شناسه اعلام‌شده (BM25)",
           "confident_alternative": "وجود جایگزین مطمئن (BM25)",
           "review_floor": "کف امتیاز: قاعده‌ای فعال شده است"}
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


def default_pick(rows, checker):
    """Index of the first sample line with an injected error (T1-T6) that its rule catches, so
    the page opens on a true positive. Falls back to the first labeled line, then to 0."""
    labeled = [i for i, r in enumerate(rows) if r["labels"].startswith("T")]
    for i in labeled:
        r = rows[i]
        try:
            line = InvoiceLine.parse(*(r[f] for f in FIELDS))
        except LineError:
            continue
        if set(r["labels"].split(";")) & set(checker.check(line).codes):
            return i
    return labeled[0] if labeled else 0


def contribution_rows(result):
    """Every term of the score, zeros included (so a rule with weight 0, such as T1, is visible),
    then the raw sum, the review floor if it applied, and the final score. Empty for a line
    with a hard flag: it has no score."""
    if result.score is None:
        return []
    fmt = lambda v: f"{v:.3f}"  # noqa: E731
    rows = []
    for k, v in result.contributions.items():
        if k == FLOOR_TERM:
            continue
        name = TERM_FA.get(k) or f"قاعده {k} (وزن دستی)"
        rows.append({"جزء": name, "سهم": fmt(v)})
    rows.append({"جزء": "امتیاز خام (جمع اجزا)", "سهم": fmt(result.raw_score)})
    if FLOOR_TERM in result.contributions:
        rows.append({"جزء": TERM_FA[FLOOR_TERM], "سهم": fmt(result.contributions[FLOOR_TERM])})
    rows.append({"جزء": "امتیاز نهایی", "سهم": fmt(result.score)})
    return rows


def score_texts(result):
    """(metric value, caption or None) for the score. A floored score is labeled as the review
    floor and shown with the raw score next to it."""
    if result.score is None:
        return "محاسبه نشد", ("این ردیف پرچم قطعی دارد: امتیاز ریسک برای آن محاسبه نمی‌شود و ردیف در "
                             "هر حال برای بررسی فرستاده می‌شود.")
    if FLOOR_TERM in result.contributions:
        return (f"{result.score:.2f} (کف بررسی)",
                f"قاعده‌ای فعال شده است، پس امتیاز تا آستانه بررسی ({HIGH_RISK}) بالا برده شد. "
                f"امتیاز خام (جمع وزن‌ها): {result.raw_score:.2f}. امتیاز فقط ترتیب ردیف را در صف "
                f"بررسی تعیین می‌کند.")
    return f"{result.score:.2f}", None


def tie_groups(scores):
    """For each position, the 1-based positions sharing its BM25 score (empty if none)."""
    out = []
    for i, s in enumerate(scores):
        same = [j + 1 for j, t in enumerate(scores) if math.isclose(s, t, rel_tol=1e-9)]
        out.append(same if len(same) > 1 else [])
    return out


def alternative_rows(result, show_rate=True):
    """show_rate=False on the real profile: its Vat column is not a known rate (TODO(legal))."""
    rows = []
    ties = tie_groups([c.score for c in result.alternatives])
    for i, c in enumerate(result.alternatives, 1):
        row = {"رتبه": i, "شناسه": c.sstid, "عنوان": c.version.title.replace("\n", " ")}
        if show_rate:
            row["نرخ"] = fmt_num(c.version.rate)
        row.update({"وضعیت": TAX_STATUS_FA[c.version.tax_status], "نوع": c.version.id_type,
                    "امتیاز BM25": round(c.score, 2),
                    "هم‌امتیاز": ("با رتبه‌های " + "، ".join(map(str, ties[i - 1])))
                                 if ties[i - 1] else "",
                    "اعلام‌شده": "✓" if c.sstid == result.line.sstid else ""})
        rows.append(row)
    return rows


def rate_status_text(result, show_rate=True):
    r = result.rate
    if r.version is None:
        return f"وضعیت شناسه در تاریخ صدور: {r.status.value}"
    v = r.version
    end = v.valid_to_incl or "اکنون"
    if not show_rate:
        return (f"شناسه در تاریخ صدور معتبر است ({TAX_STATUS_FA[v.tax_status]}، معتبر از "
                f"{v.valid_from} تا {end}). نرخ نمایش داده نمی‌شود: معنای ستون Vat در فهرست "
                f"واقعی هنوز روشن نیست (TODO(legal)).")
    return (f"نرخ ثبت‌شده برای شناسه در تاریخ صدور: {fmt_num(v.rate)}٪ "
            f"({TAX_STATUS_FA[v.tax_status]}، معتبر از {v.valid_from} تا {end})")


def hard_flag_texts(result):
    """One line per hard flag (NOT_IN_CATALOG / NOT_IN_FORCE / QUARANTINED_ONLY): a fact about
    the declared ID, shown above the score, which is not computed for such a line."""
    by_code = {e.code: e.text for e in result.explanations}
    return [f"پرچم قطعی {h.code}: {by_code.get(h.code, h.description)}" for h in result.hard_flags]
