"""Shenaseyar demo (v0.1): check one invoice line. Run: streamlit run app.py

Two catalog profiles (src/demo/pipeline.py). `fake`: the invented data/sample/fake_catalog.csv,
every rule. `real`: the manually downloaded catalog (SHENASEYAR_REAL_CATALOG, or the one zip in
data/catalog/), offered only when present; T2 is NOT run there because the meaning of the Vat
column is still TODO(legal), and no rate is shown. Loading it takes about a minute (cached).
Hard flags (NOT_IN_CATALOG / NOT_IN_FORCE / QUARANTINED_ONLY) are shown first and bypass the score.
The invoice text is untrusted: it is shown only through st.code / escaped Markdown.
"""
import streamlit as st

from src.demo.pipeline import FAKE, Checker, real_catalog_path, real_profile
from src.demo.view import (FIELDS, LABELS_FA, alternative_rows, contribution_rows,
                           hard_flag_texts, hit_rows, md_escape, rate_status_text, read_demo_rows)
from src.detect.engine import InvoiceLine, LineError
from src.detect.score import HIGH_RISK

RTL_CSS = """<style>
.stApp, .stMarkdown, .stTextInput, .stSelectbox, .stRadio, .stTable, .stAlert {direction: rtl; text-align: right;}
code, pre {direction: ltr; text-align: left;}
</style>"""
PROFILE_FA = {"fake": "فهرست ساختگی (همه قاعده‌ها، از جمله T2)",
              "real": "فهرست واقعی (T1، T3، T4، T6؛ قاعده T2 اجرا نمی‌شود)"}
PICK, MANUAL = "انتخاب از ردیف‌های نمونه", "ورود دستی"


@st.cache_resource
def get_checker(name):
    return Checker.from_profile(real_profile() if name == "real" else FAKE)


st.set_page_config(page_title="شناسه‌یار: نسخه نمایشی", layout="wide")
st.markdown(RTL_CSS, unsafe_allow_html=True)       # constant CSS only, never data
st.title("شناسه‌یار: بررسی یک ردیف صورتحساب")
st.caption("نسخه نمایشی 0.1. خروجی پیشنهادی برای بررسی انسانی است، نه حکم.")

names = ["fake"] + (["real"] if real_catalog_path() else [])
name = st.radio("فهرست شناسه‌ها", names, format_func=PROFILE_FA.get, horizontal=True)
checker = get_checker(name)
prof = checker.profile
if prof.rates_trusted:
    st.info("داده‌ها ساختگی‌اند: فهرست شناسه‌ها و ردیف‌های نمونه همگی ساخته شده‌اند.")
else:
    st.warning("فهرست واقعی: معنای ستون Vat هنوز روشن نیست (TODO(legal))، پس قاعده T2 اجرا "
               "نمی‌شود و نرخی نمایش داده نمی‌شود. ردیف‌های نمونه ساختگی‌اند.")

modes = [PICK, MANUAL] if prof.invoices.exists() else [MANUAL]
mode = st.radio("ورودی", modes, horizontal=True)
if mode == PICK:
    rows = read_demo_rows(prof.invoices)
    labels = [f"{r['line_id']}  [{r['labels'] or 'بدون خطای تزریقی'}]" for r in rows]
    pick = st.selectbox("ردیف نمونه (برچسب واقعی داخل کروشه)", range(len(rows)),
                        format_func=lambda i: labels[i])
    values = {f: rows[pick][f] for f in FIELDS}
else:
    values = {f: st.text_input(LABELS_FA[f], value="") for f in FIELDS}

try:
    line = InvoiceLine.parse(**values)
except LineError as e:
    st.subheader("ردیف ورودی")
    st.code("\n".join(f"{f}: {values[f]}" for f in FIELDS), language=None)
    st.error(f"ردیف معتبر نیست: {md_escape(e)}")
    st.stop()

result = checker.check(line)

# Hard flags first: facts about the declared ID, not risk. They bypass the score.
for text in hard_flag_texts(result):
    st.error(md_escape(text))

st.subheader("ردیف ورودی")
st.code("\n".join(f"{f}: {values[f]}" for f in FIELDS), language=None)

c1, c2 = st.columns(2)
if result.score is None:
    c1.metric("امتیاز ریسک (0 تا 1، وزن‌دهی دستی)", "محاسبه نشد")
    st.caption("این ردیف پرچم قطعی دارد: امتیاز ریسک برای آن محاسبه نمی‌شود و ردیف در هر حال "
               "برای بررسی فرستاده می‌شود.")
else:
    c1.metric("امتیاز ریسک (0 تا 1، وزن‌دهی دستی)", f"{result.score:.2f}")
c2.metric("نیاز به بررسی", "بله" if result.needs_review else "خیر",
          help=f"پرچم قطعی یا فعال شدن یک قاعده (کف امتیاز {HIGH_RISK})")
st.markdown(md_escape(rate_status_text(result, prof.rates_trusted)))

st.subheader("قاعده‌های فعال‌شده")
if result.hits:
    st.table(hit_rows(result))
else:
    st.write("هیچ قاعده‌ای فعال نشد.")
for code in result.skipped:
    st.info(f"قاعده {code} روی این فهرست اجرا نشد (نه اینکه یافته‌ای نداشت): معنای ستون Vat "
            f"هنوز روشن نیست (TODO(legal)).")
with st.expander("اجزای امتیاز"):
    st.table(contribution_rows(result) or [{"جزء": "-", "سهم": 0}])
    st.caption("وزن‌ها دستی تعیین شده‌اند، نه با یادگیری (src/detect/score.py). امتیاز فقط "
               "ترتیب ردیف‌ها را در صف بررسی تعیین می‌کند؛ هر ردیفی که قاعده‌ای در آن فعال شود "
               "وارد صف می‌شود.")

st.subheader("پنج شناسه جایگزین (BM25، معتبر در تاریخ صدور)")
if result.alternatives:
    st.table(alternative_rows(result, prof.rates_trusted))
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
