"""Shenaseyar demo (v0.1): check one invoice line. Run: streamlit run app.py

Catalog: the invented data/sample/fake_catalog.csv by default. SHENASEYAR_CATALOG=<path> points it
elsewhere, with a warning: the meaning of the real catalog's Vat column is still TODO(legal).
The invoice text is untrusted: it is shown only through st.code / escaped Markdown.
"""
import os
from pathlib import Path

import streamlit as st

from src.demo.pipeline import DEMO_INVOICES, FAKE_CATALOG, Checker
from src.demo.view import (FIELDS, LABELS_FA, alternative_rows, contribution_rows, hit_rows,
                           md_escape, rate_status_text, read_demo_rows)
from src.detect.engine import InvoiceLine, LineError
from src.detect.score import HIGH_RISK

RTL_CSS = """<style>
.stApp, .stMarkdown, .stTextInput, .stSelectbox, .stRadio, .stTable, .stAlert {direction: rtl; text-align: right;}
code, pre {direction: ltr; text-align: left;}
</style>"""
CATALOG = Path(os.environ.get("SHENASEYAR_CATALOG", FAKE_CATALOG))


@st.cache_resource
def get_checker(path):
    return Checker.from_paths(path)


st.set_page_config(page_title="شناسه‌یار: نسخه نمایشی", layout="wide")
st.markdown(RTL_CSS, unsafe_allow_html=True)       # constant CSS only, never data
st.title("شناسه‌یار: بررسی یک ردیف صورتحساب")
st.caption("نسخه نمایشی 0.1. خروجی پیشنهادی برای بررسی انسانی است، نه حکم.")
if CATALOG.resolve() != FAKE_CATALOG.resolve():
    st.warning("فهرست شناسه‌ها غیر از فهرست ساختگی است. معنای ستون Vat هنوز روشن نیست "
               "(TODO(legal))، پس نتیجه قاعده T2 قابل اتکا نیست.")
else:
    st.info("داده‌ها ساختگی‌اند: فهرست شناسه‌ها و ردیف‌های نمونه همگی ساخته شده‌اند.")

checker = get_checker(str(CATALOG))
mode = st.radio("ورودی", ["انتخاب از ردیف‌های نمونه", "ورود دستی"], horizontal=True)
if mode == "انتخاب از ردیف‌های نمونه":
    rows = read_demo_rows(DEMO_INVOICES)
    names = [f"{r['line_id']}  [{r['labels'] or 'بدون خطای تزریقی'}]" for r in rows]
    pick = st.selectbox("ردیف نمونه (برچسب واقعی داخل کروشه)", range(len(rows)),
                        format_func=lambda i: names[i])
    values = {f: rows[pick][f] for f in FIELDS}
else:
    values = {f: st.text_input(LABELS_FA[f], value="") for f in FIELDS}

st.subheader("ردیف ورودی")
st.code("\n".join(f"{f}: {values[f]}" for f in FIELDS), language=None)

try:
    line = InvoiceLine.parse(**values)
except LineError as e:
    st.error(f"ردیف معتبر نیست: {md_escape(e)}")
    st.stop()

result = checker.check(line)

c1, c2 = st.columns(2)
c1.metric("امتیاز ریسک (0 تا 1، وزن‌دهی دستی)", f"{result.score:.2f}")
c2.metric("نیاز به بررسی", "بله" if result.high_risk else "خیر", help=f"آستانه {HIGH_RISK}")
st.markdown(md_escape(rate_status_text(result)))

st.subheader("قاعده‌های فعال‌شده")
if result.hits:
    st.table(hit_rows(result))
else:
    st.write("هیچ قاعده‌ای فعال نشد.")
with st.expander("اجزای امتیاز"):
    st.table(contribution_rows(result) or [{"جزء": "-", "سهم": 0}])
    st.caption("وزن‌ها دستی تعیین شده‌اند، نه با یادگیری (src/detect/score.py).")

st.subheader("پنج شناسه جایگزین (BM25، معتبر در تاریخ صدور)")
if result.alternatives:
    st.table(alternative_rows(result))
else:
    st.write("هیچ شناسه معتبری با این شرح منطبق نشد.")

st.subheader("توضیح")
if not result.explanations:
    st.write("یافته‌ای برای توضیح وجود ندارد.")
for e in result.explanations:
    st.markdown(f"**{e.code}**: {md_escape(e.text)}")
    st.markdown(md_escape(e.citation_text))
    if e.citation is not None:
        frm, to = e.citation_valid_range
        st.caption(f"بازه اعتبار مستند: {frm} تا {to or 'اکنون'}")
        if e.citation.is_placeholder:
            st.warning("این مستند نمونه است و متن حقوقی آن هنوز وارد نشده است (TODO(legal)).")
    st.caption(e.footer)
