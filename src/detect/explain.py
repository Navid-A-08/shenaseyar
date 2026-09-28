"""Persian explanations from fixed templates, one per finding code. No LLM (descoped, CLAUDE.md).

Each explanation is filled only from the rule hit's evidence (numbers, IDs, catalog titles) and
the legal unit it cites. The invoice text `sstt` is never inserted: it is untrusted, and the UI
shows it separately as plain text.

Conventions (src/text/normalize.py): Persian ی and ک only (never Arabic ي / ك), Latin digits inside
the pipeline (the UI may convert them for display). Templates state what the data says; they make
no claim about tax law. The legal side is the cited unit from data/legal/units.json, with the date
range it was valid for. Tax status keeps its three values apart (مشمول / معاف / غیر مشمول).
"""
from dataclasses import dataclass
from decimal import Decimal

from src.detect.features import RANK_ABSENT

TAX_STATUS_FA = {"taxable": "مشمول", "exempt": "معاف", "out_of_scope": "غیر مشمول"}
FOOTER = "این یک پیشنهاد برای بررسی انسانی است، نه حکم."

TEMPLATES = {
    "T1": ("شرح این ردیف با عنوان شناسه اعلام‌شده {sstid} همخوانی ندارد: این شناسه در میان "
           "{top_k} شناسه منطبق‌تر با شرح نیست (رتبه: {rank_text}) و امتیاز انطباق آن {sim_pct}٪ "
           "امتیاز بهترین شناسه معتبر در تاریخ {issue_date} است. نزدیک‌ترین شناسه: {best_sstid} "
           "با عنوان «{best_title}»."),
    "T2": ("نرخ اعلام‌شده در این ردیف {declared_rate}٪ است، اما برای شناسه {sstid} در تاریخ "
           "{issue_date} نرخ {reference_rate}٪ ثبت شده است (وضعیت در فهرست شناسه‌ها: {tax_status_fa}؛ "
           "این نسخه از {valid_from} تا {valid_to} معتبر است)."),
    "T6": ("مبلغ مالیات اعلام‌شده {declared_vam} ریال است، اما مبلغ پایه {base} ریال با نرخ اعلام‌شده "
           "{declared_rate}٪ مالیاتی برابر {expected_vam} ریال می‌دهد. اختلاف {difference} ریال است و "
           "از حد مجاز گرد کردن ({tolerance} ریال) بیشتر است."),
    "T3": ("شناسه {sstid} یک شناسه عمومی است ({declared_type})، اما شناسه اختصاصی {best_specific} "
           "با عنوان «{best_specific_title}» با شرح این ردیف منطبق‌تر است."),
    "T4": ("شرح این ردیف فقط از واژه‌های کلی تشکیل شده است و مبلغ پایه آن {base} ریال است که از "
           "آستانه بررسی ({min_amount} ریال) بیشتر است."),
    "NOT_IN_CATALOG": ("شناسه {sstid} در فهرست شناسه‌های کالا و خدمت یافت نشد. "
                       "(ممکن است فهرست بارگذاری‌شده کامل نباشد.)"),
    "NOT_IN_FORCE": ("شناسه {sstid} در فهرست وجود دارد، اما در تاریخ {issue_date} هیچ نسخه‌ای از آن "
                     "معتبر نیست."),
    "AMBIGUOUS": ("برای شناسه {sstid} در تاریخ {issue_date} بیش از یک نسخه معتبر با نرخ‌های "
                  "{tied_rates} ثبت شده است. این مشکل داده‌های فهرست است و ریسک این ردیف را افزایش "
                  "نمی‌دهد."),
    "QUARANTINED_ONLY": ("همه ردیف‌های شناسه {sstid} در فهرست به دلیل خطای داده کنار گذاشته شده‌اند. "
                         "وضعیت این شناسه نیازمند بررسی است."),
}
NO_CITATION = {"AMBIGUOUS", "QUARANTINED_ONLY"}     # data findings: nothing legal to cite


def fmt_num(value):
    """Decimal -> Latin digits, thousands separated, no trailing zeros (12.50 -> 12.5)."""
    d = Decimal(value)
    if d == d.to_integral_value():
        return f"{int(d):,}"
    s = f"{d.normalize():f}"
    whole, frac = s.split(".")
    return f"{int(whole):,}.{frac}"


@dataclass(frozen=True)
class Explanation:
    code: str
    text: str
    citation: object | None          # legal.LegalUnit
    citation_text: str
    footer: str = FOOTER

    @property
    def citation_valid_range(self):
        c = self.citation
        return None if c is None else (c.valid_from, c.valid_to)


def _fields(hit, line):
    f = {"sstid": line.sstid, "issue_date": line.issue_date}
    for k, v in hit.evidence.items():
        f[k] = fmt_num(v) if isinstance(v, Decimal) else v
    if "tax_status" in hit.evidence:
        f["tax_status_fa"] = TAX_STATUS_FA[hit.evidence["tax_status"]]
    if "valid_from" in hit.evidence:
        f["valid_to"] = hit.evidence.get("valid_to_incl") or "اکنون (بدون تاریخ پایان)"
    if "rank_declared" in hit.evidence:
        rank = hit.evidence["rank_declared"]
        f["rank_text"] = f"پایین‌تر از {RANK_ABSENT - 1}" if rank >= RANK_ABSENT else str(rank)
        f["sim_pct"] = str(round(100 * hit.evidence["sim_declared"]))
    if "tied_rates" in hit.evidence:
        f["tied_rates"] = "، ".join(f"{fmt_num(r)}٪" for r in hit.evidence["tied_rates"])
    return f


def cite(code, date, units):
    if code in NO_CITATION:
        return None, "این یافته مربوط به کیفیت داده است و مستند حقوقی ندارد."
    found = units.for_finding(code, date)
    if not found:
        return None, f"مستندی که در تاریخ {date} معتبر باشد برای این یافته ثبت نشده است."
    u = found[0]
    end = u.valid_to or "اکنون"
    text = f"مستند: {u.title} (معتبر از {u.valid_from} تا {end})"
    if u.is_placeholder:
        text += " [نمونه: متن حقوقی هنوز وارد نشده است، TODO(legal)]"
    return u, text


def explain(hit, line, units):
    if hit.code not in TEMPLATES:
        raise KeyError(f"no template for finding code {hit.code!r}")
    text = TEMPLATES[hit.code].format(**_fields(hit, line))
    unit, ctext = cite(hit.code, line.issue_date, units)
    return Explanation(hit.code, text, unit, ctext)
