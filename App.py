# =====================================================================
#  ChetExam AI  —  FREE RAS Exam Test Platform  (FINAL, single file)
# =====================================================================
#  COLAB CELL 1 (run once, then run Cell 2):
#       !pip install -q "gradio>=6.0" gspread pandas numpy
#
#  COLAB CELL 2:
#       paste this whole file and run it.
#       -> Google login popup aayega (sheet read karne ke liye) -> Allow.
#       -> Diagnostics print hongi -> phir public Gradio link milega.
#
#  Rules followed:
#   * Google Sheet = source of truth (kabhi modify / delete nahi hoti)
#   * No Gemini / no API during the test
#   * Hierarchy: Exam -> Subject -> Topic -> Difficulty -> Count -> Test
#   * Pure pandas / numpy (no TA libs)
# =====================================================================

import os
import re
import html
import time
import random
import unicodedata
from collections import Counter

import numpy as np
import pandas as pd
import gradio as gr

# =====================================================================
#  0. CONFIG  (yahi sirf edit karna ho to karo)
# =====================================================================
SHEET_ID = "1Hw2cRzZs9ZvCPDrpC2ehi83PHgmeTAgKlNGMs8hHZVw"
SHEET_TAB = "MCQ_DATABASE"
SHEET_GID = None          # optional: tab ka gid (public-CSV fallback ke liye, e.g. "0")
LOCAL_CSV_PATH = None     # optional: Sheet ki CSV Colab me upload ki ho to path do

DEFAULT_EXAM = "RAS"      # blank / unknown Exam values isme map hongi
MIN_ROWS_FOR_NEW_EXAM = 30  # unknown exam-naam tabhi exam maana jaye jab itni rows ho
MAX_Q = 100
SECONDS_PER_Q = 60

# Raw Exam-column value (lowercase) -> clean exam name.
# Yahan apna mapping add/change kar sakte ho. Sheet modify nahi hoti.
EXAM_ALIASES = {
    "ras": "RAS",
    "ras exam": "RAS",
    "rpsc": "RAS",
    "rpsc ras": "RAS",
    "rajasthan exam": "RAS",
    "rajasthan": "RAS",
    "ras pre": "RAS Pre",
    "ras prelims": "RAS Pre",
    "ras preliminary": "RAS Pre",
    "ras pre exam": "RAS Pre",
}
# Optional: subject/topic ke alag spellings ko ek naam me merge karna ho to
# {"raw lowercase": "Clean Name"}   e.g. {"राजस्थान का इतिहास": "राजस्थान इतिहास"}
SUBJECT_ALIASES = {}
TOPIC_ALIASES = {}

# Agar Check Result / AI Check columns me kuch words ho jinke rows test me nahi chahiye
# e.g. ["wrong", "incorrect", "bad"]  (default: kuch exclude nahi hota)
EXCLUDE_CHECK_KEYWORDS = []

SHOW_TOPICS_IN_DIAGNOSTICS = True

LETTERS = ["A", "B", "C", "D"]
ALL_TOPICS = "All Topics"
ALL_DIFF = "All"
DIFFS = ["Easy", "Medium", "Hard"]
COUNT_CHOICES = ["10", "20", "50", "100", "All Available (max 100)"]

COLS = ["id", "exam_raw", "subject_raw", "topic_raw", "question",
        "opt_A", "opt_B", "opt_C", "opt_D", "correct_raw",
        "explanation", "difficulty_raw", "check_result", "ai_check"]
HEADER_KEYS = ["id", "exam", "subject", "topic", "question",
               "option a", "option b", "option c", "option d", "correct answer",
               "explanation", "difficulty", "check result", "ai check"]

GR_MAJOR = int(str(getattr(gr, "__version__", "6")).split(".")[0])
if GR_MAJOR < 6:
    print(f"⚠️ Gradio {gr.__version__} detected. Recommended: !pip install -q 'gradio>=6.0' "
          f"(app older version me bhi chalega).")

DB = pd.DataFrame()      # normalized + valid MCQs (global)
REPORT = {}              # diagnostics info


# =====================================================================
#  1. TEXT HELPERS
# =====================================================================
_ZW = {ord(c): None for c in "\u200b\u200c\u200d\u2060\ufeff"}
_JUNK = {"nan", "none", "null", "nat", "n/a", "-", "--", "—"}


def clean(x):
    """Display-safe cleaning: NaN -> '', NFC normalise, collapse spaces."""
    if x is None:
        return ""
    if isinstance(x, float) and np.isnan(x):
        return ""
    s = unicodedata.normalize("NFC", str(x)).replace("\xa0", " ").replace("\ufeff", "")
    s = re.sub(r"[ \t\f\v\r]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    return s.strip()


def clean_meta(x):
    s = clean(x)
    return "" if s.lower() in _JUNK else s


def key(x):
    """Comparison key: NFKC + casefold + no zero-width + collapsed spaces."""
    s = unicodedata.normalize("NFKC", clean(x)).translate(_ZW).casefold()
    s = re.sub(r"\s+", " ", s).strip(" .:-–—_,;")
    return s


def esc(s):
    return html.escape(str(s if s is not None else "")).replace("\n", "<br>")


def fmt_time(sec):
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# =====================================================================
#  2. LOAD DATABASE  (Google Sheet -> DataFrame, original untouched)
# =====================================================================
def _rows_from_gspread():
    from google.colab import auth            # only exists in Colab
    auth.authenticate_user()
    import gspread
    from google.auth import default
    creds, _ = default()
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
    return ws.get_all_values()


def _rows_from_public_csv():
    if SHEET_GID:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    else:
        from urllib.parse import quote
        url = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet="
               f"{quote(SHEET_TAB)}")
        print("ℹ️ Public gviz CSV use ho raha hai (sheet 'Anyone with link' honi chahiye). "
              "Numeric-looking options kabhi blank aa sakte hain — Colab login method better hai.")
    df = pd.read_csv(url, dtype=str, keep_default_na=False, header=None)
    return df.values.tolist()


def _rows_to_df(rows):
    width = len(COLS)
    rows = [(list(r) + [""] * width)[:max(width, len(r))] for r in rows if r is not None]
    if not rows:
        raise RuntimeError("Sheet khaali hai ya read nahi hui.")
    header = [key(h) for h in rows[0]]
    body = rows[1:]
    header_like = ("question" in header) or (header and header[0] == "id")
    if header_like:
        pos = {h: i for i, h in enumerate(header) if h}
        if all(k in pos for k in HEADER_KEYS[:12]):
            idxs = [pos.get(k) for k in HEADER_KEYS]          # header-based mapping
        else:
            idxs = list(range(width))                          # positional A..N
    else:
        body = rows
        idxs = list(range(width))
    out = []
    for r in body:
        out.append([(r[i] if (i is not None and i < len(r)) else "") for i in idxs])
    return pd.DataFrame(out, columns=COLS)


def load_database():
    """Read the raw sheet rows (strings). Never writes anything back."""
    if LOCAL_CSV_PATH:
        rows = pd.read_csv(LOCAL_CSV_PATH, dtype=str, keep_default_na=False, header=None).values.tolist()
        print(f"✅ Local CSV loaded: {LOCAL_CSV_PATH}")
    else:
        try:
            rows = _rows_from_gspread()
            print("✅ Google Sheet loaded (Colab login).")
        except Exception as e:
            print(f"ℹ️ Colab/gspread load nahi hua ({type(e).__name__}: {e}). Public CSV try kar raha hoon…")
            rows = _rows_from_public_csv()
            print("✅ Google Sheet loaded (public CSV).")
    return _rows_to_df(rows)


# =====================================================================
#  3. NORMALISATION  (Exam/Subject/Topic hierarchy repair — in memory only)
# =====================================================================
_SUBJECT_HINTS_DEV = [
    "इतिहास", "भूगोल", "राजव्यवस्था", "राजनीति", "अर्थव्यवस्था", "अर्थशास्त्र", "विज्ञान",
    "प्रौद्योगिकी", "कला", "संस्कृति", "साहित्य", "गणित", "तर्कशक्ति", "समसामयिक", "पर्यावरण",
    "सामान्य ज्ञान", "संविधान", "कृषि", "हिंदी", "हिन्दी", "अंग्रेजी", "जनसंख्या"
]
_SUBJECT_HINTS_ASCII = re.compile(
    r"(?<![a-z])(history|geography|polity|economy|economics|science|technology|culture|art|"
    r"environment|ecology|reasoning|aptitude|maths?|mathematics|gk|constitution|agriculture|"
    r"literature|current affairs|general knowledge|hindi|english|biology|physics|chemistry)(?![a-z])")
_OTHER_EXAM_RE = re.compile(
    r"(?<![a-z])(upsc|ias|ips|ssc|cgl|chsl|reet|rtet|ctet|patwari|constable|si|ldc|udc|rssb|ibps|"
    r"sbi|rbi|rrb|ntpc|net|jrf|gate|neet|jee|cds|nda|clat|cuet|banking|railway|vdo|police|fci)(?![a-z])")


def looks_like_subject(k):
    if not k:
        return False
    if _SUBJECT_HINTS_ASCII.search(k):
        return True
    return any(h in k for h in _SUBJECT_HINTS_DEV)


def classify_exam(raw, cnt, subject_keys):
    """Return (kind, canonical_exam_or_None). kind: exam | subject | blank | unknown-small"""
    k = key(raw)
    if not k:
        return "blank", DEFAULT_EXAM
    if k in EXAM_ALIASES:
        return "exam", EXAM_ALIASES[k]
    if re.search(r"(?<![a-z])ras(?![a-z])", k) or "rajasthan administrative" in k:
        if re.search(r"(?<![a-z])(pre|prelim|prelims|preliminary)(?![a-z])", k) \
                or "प्रारंभिक" in k or "प्री" in k:
            return "exam", "RAS Pre"
        if "mains" in k or "मुख्य" in k:
            return "exam", "RAS Mains"
        return "exam", "RAS"
    if _OTHER_EXAM_RE.search(k):
        return "exam", clean(raw)
    if looks_like_subject(k) or k in subject_keys:
        return "subject", None
    if cnt >= MIN_ROWS_FOR_NEW_EXAM:
        return "exam", clean(raw)
    return "unknown-small", DEFAULT_EXAM


_DEV_LETTER = {"क": "A", "ख": "B", "ग": "C", "घ": "D"}
_NUM_LETTER = {"1": "A", "2": "B", "3": "C", "4": "D"}


def normalize_answer(raw, opts):
    """Return 'A'/'B'/'C'/'D' or '' when it cannot be understood."""
    s = clean(raw)
    if not s:
        return ""
    u = unicodedata.normalize("NFKC", s).strip()
    up = u.upper()
    m = re.fullmatch(r"[\(\[\{]?\s*([ABCD1-4])\s*[\)\]\}\.\:]?", up)
    if m:
        c = m.group(1)
        return _NUM_LETTER.get(c, c)
    m = re.fullmatch(r"[\(\[\{]?\s*([कखगघ])\s*[\)\]\}\.\:]?", u)
    if m:
        return _DEV_LETTER[m.group(1)]
    m = re.search(r"(?:OPTION|ANS(?:WER)?|उत्तर|विकल्प)\s*[:\-]?\s*[\(\[]?\s*([ABCD1-4])(?![A-Z0-9])", up)
    if m:
        c = m.group(1)
        return _NUM_LETTER.get(c, c)
    m = re.match(r"[\(\[]?([ABCD])[\)\]\.\:]\s", up)
    if m:
        return m.group(1)
    ks = key(s)
    for L in LETTERS:
        if opts.get(L) and key(opts[L]) == ks:
            return L
    return ""


def norm_difficulty(raw):
    k = key(raw)
    if not k:
        return "Medium"
    if k in ("1", "2", "3"):
        return {"1": "Easy", "2": "Medium", "3": "Hard"}[k]
    if any(t in k for t in ("hard", "difficult", "tough", "advanced", "कठिन", "मुश्किल", "कठीन")):
        return "Hard"
    if any(t in k for t in ("easy", "simple", "basic", "आसान", "सरल", "सुगम")):
        return "Easy"
    if any(t in k for t in ("medium", "moderate", "average", "मध्यम", "मध्य")):
        return "Medium"
    return "Medium"


def _canon_labels(series, alias):
    """Merge spelling/case variants of the same value (most frequent spelling wins)."""
    alias_k = {key(a): v for a, v in alias.items()}
    keys = series.map(key)
    tmp = pd.DataFrame({"k": keys, "v": series})
    disp = tmp.groupby("k")["v"].agg(lambda x: x.value_counts().idxmax())
    out = keys.map(disp)
    return pd.Series([alias_k.get(k, v) for k, v in zip(keys, out)], index=series.index)


def normalize_database(raw):
    """Raw sheet DF -> clean, validated DF. Sheet is never modified."""
    df = raw.copy()
    total_rows = len(df)
    for c in ["id", "question", "opt_A", "opt_B", "opt_C", "opt_D", "explanation", "correct_raw"]:
        df[c] = df[c].map(clean)
    for c in ["exam_raw", "subject_raw", "topic_raw", "difficulty_raw", "check_result", "ai_check"]:
        df[c] = df[c].map(clean_meta)

    # drop fully blank rows (count them, sheet unchanged)
    blank_mask = (df["question"] == "") & (df[["opt_A", "opt_B", "opt_C", "opt_D"]] == "").all(axis=1)
    blank_rows = int(blank_mask.sum())
    df = df[~blank_mask].reset_index(drop=True)

    # ---- Exam column classification -------------------------------
    subject_keys = set(df["subject_raw"].map(key)) - {""}
    exam_counts = df["exam_raw"].value_counts()
    exam_info = {raw: classify_exam(raw, int(cnt), subject_keys) for raw, cnt in exam_counts.items()}
    if "" not in exam_info:
        exam_info[""] = ("blank", DEFAULT_EXAM)

    exams, subjects, topics, rerouted = [], [], [], 0
    for e_raw, s_raw, t_raw in zip(df["exam_raw"], df["subject_raw"], df["topic_raw"]):
        kind, canon = exam_info[e_raw]
        if kind == "subject":
            # Exam column me actually Subject likha hai
            rerouted += 1
            exam = DEFAULT_EXAM
            subject = e_raw
            topic = t_raw or (s_raw if key(s_raw) != key(e_raw) else "")
        else:
            exam = canon
            subject = s_raw or "General"
            topic = t_raw
        exams.append(exam)
        subjects.append(subject)
        topics.append(topic or "Other")
    df["exam"] = exams
    df["subject"] = subjects
    df["topic"] = topics

    df["exam"] = _canon_labels(df["exam"], {})
    df["subject"] = _canon_labels(df["subject"], SUBJECT_ALIASES)
    df["topic"] = _canon_labels(df["topic"], TOPIC_ALIASES)

    # ---- answer / difficulty / validity ---------------------------
    correct, reasons = [], []
    ck = [k.casefold() for k in EXCLUDE_CHECK_KEYWORDS]
    for i in range(len(df)):
        opts = {L: df.at[i, f"opt_{L}"] for L in LETTERS}
        ans = normalize_answer(df.at[i, "correct_raw"], opts)
        correct.append(ans)
        n_opts = sum(1 for L in LETTERS if opts[L])
        if len(df.at[i, "question"]) < 3:
            reasons.append("empty question")
        elif n_opts < 2:
            reasons.append("fewer than 2 options")
        elif ans == "":
            reasons.append("correct answer not understood")
        elif not opts[ans]:
            reasons.append("correct option is empty")
        elif ck and any(w in key(df.at[i, "check_result"]) or w in key(df.at[i, "ai_check"]) for w in ck):
            reasons.append("excluded by check keyword")
        else:
            reasons.append("")
    df["correct"] = correct
    df["difficulty"] = df["difficulty_raw"].map(norm_difficulty)
    df["qkey"] = [key(q) + "||" + "|".join(key(o) for o in (a, b, c, d))
                  for q, a, b, c, d in zip(df["question"], df["opt_A"], df["opt_B"], df["opt_C"], df["opt_D"])]
    df["_reason"] = reasons

    valid = df[df["_reason"] == ""].copy().reset_index(drop=True)
    exam_map = sorted(
        [(raw if raw else "(blank)", int(exam_counts.get(raw, 0)), kind, canon or "→ used as Subject")
         for raw, (kind, canon) in exam_info.items() if int(exam_counts.get(raw, 0)) > 0],
        key=lambda x: -x[1])

    report = {
        "total_rows": total_rows,
        "blank_rows": blank_rows,
        "valid": len(valid),
        "invalid_reasons": Counter(r for r in reasons if r),
        "exam_map": exam_map,
        "rerouted": rerouted,
        "duplicates": int(valid["qkey"].duplicated().sum()),
        "check_result_dist": Counter(df["check_result"].replace("", "(blank)")).most_common(8),
        "ai_check_dist": Counter(df["ai_check"].replace("", "(blank)")).most_common(8),
    }
    return valid, report


# =====================================================================
#  4. QUERY FUNCTIONS  (ALWAYS Exam -> Subject -> Topic -> Difficulty)
# =====================================================================
def _scope(exam=None, subject=None, topic=None, difficulty=None):
    d = DB
    if exam:
        d = d[d["exam"] == exam]
    if subject:
        d = d[d["subject"] == subject]
    if topic and topic != ALL_TOPICS:
        d = d[d["topic"] == topic]
    if difficulty and difficulty != ALL_DIFF:
        d = d[d["difficulty"] == difficulty]
    return d


def count_q(exam=None, subject=None, topic=None, difficulty=None):
    if DB.empty:
        return 0
    return int(_scope(exam, subject, topic, difficulty)["qkey"].nunique())


def get_exams():
    if DB.empty:
        return []
    cnt = DB.groupby("exam")["qkey"].nunique()
    names = list(cnt.index)
    pref = [e for e in ("RAS", "RAS Pre") if e in names]
    rest = sorted([e for e in names if e not in pref], key=lambda e: (-int(cnt[e]), e))
    return pref + rest


def get_subject_counts(exam):
    if not exam:
        return {}
    d = _scope(exam)
    cnt = d.groupby("subject")["qkey"].nunique()
    return dict(sorted(((s, int(n)) for s, n in cnt.items()), key=lambda x: (-x[1], x[0])))


def get_subjects(exam):
    return list(get_subject_counts(exam).keys())


def get_topic_counts(exam, subject):
    if not exam or not subject:
        return {}
    d = _scope(exam, subject)
    cnt = d.groupby("topic")["qkey"].nunique()
    return dict(sorted(((t, int(n)) for t, n in cnt.items()), key=lambda x: x[0].casefold()))


def get_topics(exam, subject):
    """[ALL_TOPICS] + every topic that exists for THIS exam + subject."""
    return [ALL_TOPICS] + list(get_topic_counts(exam, subject).keys())


def get_difficulties(exam, subject, topic):
    """{'All': n, 'Easy': n, 'Medium': n, 'Hard': n}  (for the selected exam/subject/topic)."""
    d = _scope(exam, subject, topic)
    out = {ALL_DIFF: int(d["qkey"].nunique()) if len(d) else 0}
    for lvl in DIFFS:
        out[lvl] = int(d[d["difficulty"] == lvl]["qkey"].nunique()) if len(d) else 0
    return out


def build_selectors(exam=None, subject=None, topic=None, diff=None):
    """Sanitise the whole cascade in order and return choices + valid values (never invalid)."""
    exams = get_exams()
    exam = exam if exam in exams else (exams[0] if exams else None)
    scnt = get_subject_counts(exam)
    subs = list(scnt.keys())
    subject = subject if subject in subs else (subs[0] if subs else None)
    tcnt = get_topic_counts(exam, subject)
    topics = [ALL_TOPICS] + list(tcnt.keys())
    topic = topic if topic in topics else ALL_TOPICS
    dcnt = get_difficulties(exam, subject, topic)
    diff = diff if diff in dcnt else ALL_DIFF

    exam_ch = [(f"{e} ({count_q(e)})", e) for e in exams]
    subj_ch = [(f"{s} ({n})", s) for s, n in scnt.items()]
    total_in_subject = count_q(exam, subject) if subject else 0
    topic_ch = [(f"{ALL_TOPICS} ({total_in_subject})", ALL_TOPICS)] + [(f"{t} ({n})", t) for t, n in tcnt.items()]
    diff_ch = [(("All Levels" if k == ALL_DIFF else k) + f" ({n})", k) for k, n in dcnt.items()]

    if exam and subject:
        info = (f"<div class='ce-info'>📚 <b>{esc(exam)}</b> › <b>{esc(subject)}</b> › <b>{esc(topic)}</b><br>"
                f"Available: <b>{dcnt[ALL_DIFF]}</b> questions &nbsp;·&nbsp; Easy {dcnt['Easy']} · "
                f"Medium {dcnt['Medium']} · Hard {dcnt['Hard']}<br>"
                f"Selected difficulty <b>{esc(diff)}</b> → <b>{dcnt.get(diff, 0)}</b> questions</div>")
    else:
        info = "<div class='ce-info'>⚠️ Database me koi valid question nahi mila.</div>"
    return {"exam": (exam_ch, exam), "subject": (subj_ch, subject), "topic": (topic_ch, topic),
            "diff": (diff_ch, diff), "info": info, "available": dcnt.get(diff, 0)}


# =====================================================================
#  5. DIAGNOSTICS  (launch se pehle print)
# =====================================================================
def print_diagnostics(report):
    line = "=" * 64
    print("\n" + line)
    print("📊 DATABASE DIAGNOSTICS")
    print(line)
    print(f"Total MCQs (rows in sheet) : {report['total_rows']}")
    print(f"Blank rows skipped         : {report['blank_rows']}")
    print(f"Valid MCQs                 : {report['valid']}")
    inv = report["invalid_reasons"]
    if inv:
        print(f"Invalid (not shown in test): {sum(inv.values())}")
        for r, n in inv.most_common():
            print(f"   - {r}: {n}")
    print(f"Duplicate questions (same text+options, test me ek hi baar aayenge): {report['duplicates']}")

    print("\n🧹 Exam-column cleaning (raw value → what students see):")
    for raw, n, kind, mapped in report["exam_map"]:
        print(f"   {raw!r:38} {n:>5} rows  [{kind}]  → {mapped}")
    print(f"   (rows jinka Exam-column asal me Subject tha: {report['rerouted']})")

    if report["check_result_dist"]:
        print("\n🔎 'Check Result' values:", report["check_result_dist"])
    if report["ai_check_dist"]:
        print("🔎 'AI Check' values   :", report["ai_check_dist"])
    print("   (Kuch rows exclude karni ho to EXCLUDE_CHECK_KEYWORDS config me words daalo.)")

    print("\nExams: " + ", ".join(f"{e} ({count_q(e)})" for e in get_exams()))
    for e in get_exams():
        print(f"\n{e}")
        for s, n in get_subject_counts(e).items():
            tcnt = get_topic_counts(e, s)
            print(f"  {s} → {len(tcnt)} topics ({n} questions)")
            if SHOW_TOPICS_IN_DIAGNOSTICS:
                print("      " + " | ".join(f"{t} ({c})" for t, c in tcnt.items()))
    print(line + "\n")


# =====================================================================
#  6. TEST ENGINE
# =====================================================================
def _parse_count(choice):
    s = str(choice or "10").strip()
    if s.lower().startswith("all"):
        return "all"
    try:
        return int(re.sub(r"\D", "", s) or 10)
    except Exception:
        return 10


def _row_to_q(r):
    return {"id": r.id, "exam": r.exam, "subject": r.subject, "topic": r.topic,
            "difficulty": r.difficulty, "question": r.question,
            "options": {"A": r.opt_A, "B": r.opt_B, "C": r.opt_C, "D": r.opt_D},
            "correct": r.correct, "explanation": r.explanation}


def generate_test(exam, subject, topic, difficulty, n_choice):
    """Return (questions, meta, message). questions=None => message explains why."""
    exams = get_exams()
    if exam not in exams:
        return None, None, "⚠️ Please select a valid Exam."
    if subject not in get_subjects(exam):
        return None, None, "⚠️ Please select a Subject."
    if topic not in get_topics(exam, subject):
        topic = ALL_TOPICS
    if difficulty not in (ALL_DIFF, *DIFFS):
        difficulty = ALL_DIFF

    pool = _scope(exam, subject, topic, difficulty).drop_duplicates("qkey")
    avail = len(pool)
    label = f"{exam} › {subject} › {topic} › {difficulty}"
    if avail == 0:
        return None, None, (f"⚠️ <b>Is selection me koi question nahi hai.</b><br>{esc(label)}<br>"
                            f"Difficulty ya topic badal kar dekho.")
    want = _parse_count(n_choice)
    n = min(avail, MAX_Q) if want == "all" else want
    if n > avail:
        return None, None, (f"⚠️ <b>Sirf {avail} question(s) available hain</b>, aapne {n} maange.<br>"
                            f"{esc(label)}<br>Kam number chuno, ya <b>All Available</b> select karo, "
                            f"ya Topic/Difficulty badlo.")
    picked = pool.sample(n=n)
    questions = [_row_to_q(r) for r in picked.itertuples(index=False)]
    meta = {"exam": exam, "subject": subject, "topic": topic, "difficulty": difficulty}
    return questions, meta, ""


def new_test_state(questions, meta, label="Test"):
    n = len(questions)
    return {"active": True, "submitted": False, "questions": questions, "answers": [""] * n,
            "marked": [False] * n, "cur": 0, "duration": n * SECONDS_PER_Q,
            "end_ts": time.time() + n * SECONDS_PER_Q, "meta": meta, "label": label,
            "result": None, "auto": False}


def submit_test(st):
    """Grade the test. Returns result dict (also stored in state)."""
    qs, ans = st["questions"], st["answers"]
    rows, c, w, na = [], 0, 0, 0
    for i, q in enumerate(qs):
        a = ans[i]
        if not a:
            status = "na"; na += 1
        elif a == q["correct"]:
            status = "correct"; c += 1
        else:
            status = "wrong"; w += 1
        rows.append({"idx": i, "your": a, "correct": q["correct"], "status": status,
                     "marked": bool(st["marked"][i])})
    attempted = c + w
    res = {"total": len(qs), "correct": c, "wrong": w, "na": na,
           "marked": int(sum(st["marked"])),
           "accuracy": round(100.0 * c / attempted, 1) if attempted else 0.0,
           "rows": rows, "time_used": min(st["duration"], max(0, int(st["duration"] - (st["end_ts"] - time.time()))))}
    st["submitted"] = True
    st["active"] = False
    st["result"] = res
    return res


def retry_wrong(st, include_not_attempted=False):
    """New question list from the finished test: wrong (+ optionally not attempted)."""
    if not st or not st.get("questions"):
        return []
    out = []
    for i, q in enumerate(st["questions"]):
        a = st["answers"][i]
        if (a and a != q["correct"]) or (include_not_attempted and not a):
            out.append(dict(q, options=dict(q["options"])))
    random.shuffle(out)
    return out


# =====================================================================
#  7. HTML RENDERERS  (explicit dark-on-light colours — see CSS)
# =====================================================================
def timer_html(st):
    rem = st["end_ts"] - time.time() if st else 0
    cls = "ce-timer ce-timer-low" if rem <= 60 else "ce-timer"
    return f"<div class='{cls}'>⏱ Time Left: <b>{fmt_time(rem)}</b></div>"


def progress_html(st):
    n, i = len(st["questions"]), st["cur"]
    answered = sum(1 for a in st["answers"] if a)
    marked = sum(st["marked"])
    return (f"<div class='ce-progress'><div class='ce-qno'>Question {i + 1} / {n}</div>"
            f"<div class='ce-sub'>✅ Answered {answered} &nbsp;·&nbsp; 🔖 Marked {marked} "
            f"&nbsp;·&nbsp; ⚪ Left {n - answered}</div></div>")


def question_html(st):
    i = st["cur"]
    q = st["questions"][i]
    sel = st["answers"][i]
    opts = ""
    for L in LETTERS:
        txt = q["options"].get(L, "")
        if not txt:
            continue
        cls = "ce-opt ce-opt-sel" if sel == L else "ce-opt"
        opts += f"<div class='{cls}'><span class='ce-letter'>{L}.</span> {esc(txt)}</div>"
    mk = "<span class='ce-badge ce-badge-mark'>🔖 Marked</span>" if st["marked"][i] else ""
    return (f"<div class='ce-card'>"
            f"<div class='ce-tags'><span class='ce-badge'>{esc(q['subject'])}</span>"
            f"<span class='ce-badge'>{esc(q['topic'])}</span>"
            f"<span class='ce-badge'>{esc(q['difficulty'])}</span>{mk}</div>"
            f"<div class='ce-q'>{esc(q['question'])}</div>{opts}</div>")


def palette_html(st):
    chips = ""
    for i in range(len(st["questions"])):
        if i == st["cur"]:
            cls = "ce-chip ce-chip-cur"
        elif st["marked"][i]:
            cls = "ce-chip ce-chip-mark"
        elif st["answers"][i]:
            cls = "ce-chip ce-chip-ans"
        else:
            cls = "ce-chip"
        chips += f"<span class='{cls}'>{i + 1}</span>"
    return (f"<div class='ce-pal'><div class='ce-pal-t'>Question Palette</div>{chips}"
            f"<div class='ce-legend'>🟦 Current &nbsp; 🟩 Answered &nbsp; 🟧 Marked &nbsp; ⬜ Not attempted</div></div>")


def summary_html(st, res):
    m = st["meta"] or {}
    auto = "<div class='ce-warn'>⏰ Time khatam — test automatically submit ho gaya.</div>" if st.get("auto") else ""
    where = " › ".join(esc(m.get(k, "")) for k in ("exam", "subject", "topic", "difficulty") if m.get(k))
    return (f"<div class='ce-card'>{auto}"
            f"<div class='ce-res-title'>🎯 TEST RESULT</div>"
            f"<div class='ce-sub'>{where}</div>"
            f"<div class='ce-score'>Score: {res['correct']} / {res['total']}</div>"
            f"<div class='ce-grid'>"
            f"<div class='ce-stat ce-s-ok'>✅ Correct<br><b>{res['correct']}</b></div>"
            f"<div class='ce-stat ce-s-bad'>❌ Wrong<br><b>{res['wrong']}</b></div>"
            f"<div class='ce-stat ce-s-na'>⚪ Not Attempted<br><b>{res['na']}</b></div>"
            f"<div class='ce-stat ce-s-mk'>🔖 Marked for Review<br><b>{res['marked']}</b></div>"
            f"</div>"
            f"<div class='ce-acc'>Accuracy: <b>{res['accuracy']}%</b> "
            f"<span class='ce-sub'>(correct ÷ attempted)</span></div>"
            f"<div class='ce-sub'>Time used: {fmt_time(res['time_used'])} of {fmt_time(st['duration'])}</div></div>")


def review_html(st, res):
    out = ["<div class='ce-rev-head'>📋 Detailed Review</div>"]
    for row in res["rows"]:
        q = st["questions"][row["idx"]]
        i = row["idx"] + 1
        if row["status"] == "correct":
            box, verdict = "ce-rev ce-rev-ok", "✅ Correct"
        elif row["status"] == "wrong":
            box, verdict = "ce-rev ce-rev-bad", "❌ Wrong"
        else:
            box, verdict = "ce-rev ce-rev-na", "⚪ Not Attempted"
        your = (f"<b>{row['your']}</b> — {esc(q['options'].get(row['your'], ''))}"
                if row["your"] else "<b>—</b> (not attempted)")
        corr = f"<b>{q['correct']}</b> — {esc(q['options'].get(q['correct'], ''))}"
        mk = " <span class='ce-badge ce-badge-mark'>🔖 Marked</span>" if row["marked"] else ""
        expl = q["explanation"].strip() if q["explanation"] else ""
        expl_html = esc(expl) if expl else "Is question ki explanation database me available nahi hai."
        out.append(
            f"<div class='{box}'>"
            f"<div class='ce-rev-q'>Q{i}. {esc(q['question'])}{mk}</div>"
            f"<div class='ce-line'>Your Answer: {your}</div>"
            f"<div class='ce-line'>Correct Answer: {corr}</div>"
            f"<div class='ce-line'>Result: <b>{verdict}</b></div>"
            f"<div class='ce-expl'>📖 <b>Explanation:</b><br>{expl_html}</div></div>")
    return "".join(out)


HEADER_HTML = ("<div class='ce-head'><div class='ce-h1'>📝 ChetExam AI</div>"
               "<div class='ce-h2'>Free RAS Exam Practice Tests</div></div>")

CSS = """
:root, .dark, body, .gradio-container {
  --body-background-fill:#eef2f7; --body-text-color:#111827; --body-text-color-subdued:#334155;
  --background-fill-primary:#ffffff; --background-fill-secondary:#f1f5f9;
  --block-background-fill:#ffffff; --block-border-color:#cbd5e1; --border-color-primary:#cbd5e1;
  --block-label-text-color:#111827; --block-title-text-color:#111827; --block-info-text-color:#334155;
  --input-background-fill:#ffffff; --input-border-color:#94a3b8; --input-text-color:#111827;
  --checkbox-label-text-color:#111827; --checkbox-label-background-fill:#ffffff;
  --checkbox-label-background-fill-hover:#e0e7ff; --checkbox-label-background-fill-selected:#dbeafe;
  --checkbox-label-text-color-selected:#1e3a8a; --checkbox-label-border-color:#94a3b8;
  --checkbox-label-border-color-selected:#1d4ed8;
  color-scheme: light;
}
body, .gradio-container { background:#eef2f7 !important; color:#111827 !important; }
.gradio-container { max-width:760px !important; margin:0 auto !important; font-size:16px !important; }
.gradio-container label span, .gradio-container .block-title, .gradio-container [data-testid="block-info"],
.gradio-container legend { color:#111827 !important; }
.gradio-container input, .gradio-container textarea, .gradio-container select {
  color:#111827 !important; background:#ffffff !important; }
.gradio-container ul.options, .gradio-container ul.options li { background:#ffffff !important; color:#111827 !important; }
.gradio-container ul.options li.selected, .gradio-container ul.options li:hover { background:#dbeafe !important; }

/* buttons */
.gradio-container button { font-size:16px !important; min-height:46px; }
.gradio-container button.primary { background:#1d4ed8 !important; color:#ffffff !important; border:none !important; }
.gradio-container button.secondary { background:#e2e8f0 !important; color:#111827 !important; border:1px solid #94a3b8 !important; }
.gradio-container button.stop { background:#dc2626 !important; color:#ffffff !important; border:none !important; }

/* answer radio */
#ans_radio label { background:#ffffff !important; color:#111827 !important; border:2px solid #94a3b8 !important;
  border-radius:12px !important; padding:12px 18px !important; font-size:20px !important; font-weight:700 !important;
  justify-content:center; }
#ans_radio label.selected, #ans_radio label:has(input:checked) {
  background:#dbeafe !important; border-color:#1d4ed8 !important; color:#1e3a8a !important; }
#ans_radio .wrap { display:grid !important; grid-template-columns:repeat(4, 1fr); gap:8px; }
#nav_row, #act_row, #jump_row { flex-wrap:nowrap !important; gap:8px; }
#nav_row > *, #act_row > * { min-width:0 !important; }
#timer_box { position:sticky; top:0; z-index:100; }

/* my HTML blocks: explicit colours so Gradio theme can never wash them out */
.ce-head { text-align:center; padding:6px 0 2px 0; }
.ce-h1 { font-size:28px; font-weight:800; color:#1e3a8a !important; }
.ce-h2 { font-size:15px; color:#334155 !important; }
.ce-info { background:#eff6ff; border:1px solid #93c5fd; border-radius:12px; padding:12px; color:#0f172a !important; line-height:1.6; }
.ce-info *, .ce-msg * { color:#0f172a !important; }
.ce-msg { background:#fef2f2; border:1px solid #f87171; border-radius:12px; padding:12px; color:#7f1d1d !important; line-height:1.6; }
.ce-msg * { color:#7f1d1d !important; }
.ce-ok-msg { background:#ecfdf5; border:1px solid #34d399; border-radius:12px; padding:12px; color:#064e3b !important; }
.ce-timer { background:#1e3a8a; color:#ffffff !important; text-align:center; font-size:20px; padding:10px; border-radius:12px; }
.ce-timer * { color:#ffffff !important; }
.ce-timer-low { background:#b91c1c; }
.ce-progress { background:#ffffff; border:1px solid #cbd5e1; border-radius:12px; padding:10px 14px; }
.ce-qno { font-size:22px; font-weight:800; color:#111827 !important; }
.ce-sub { font-size:14px; color:#334155 !important; }
.ce-card { background:#ffffff; border:1px solid #cbd5e1; border-radius:14px; padding:14px; color:#111827 !important; }
.ce-card div, .ce-card span, .ce-card b { color:#111827; }
.ce-tags { margin-bottom:8px; }
.ce-badge { display:inline-block; background:#e0e7ff; color:#1e3a8a !important; border-radius:999px; padding:2px 10px;
  font-size:13px; margin:0 6px 6px 0; }
.ce-badge-mark { background:#ffedd5; color:#9a3412 !important; }
.ce-q { font-size:19px; line-height:1.6; font-weight:600; color:#111827 !important; margin:6px 0 12px 0; }
.ce-opt { background:#f8fafc; border:2px solid #cbd5e1; border-radius:12px; padding:12px; margin:8px 0;
  font-size:17px; line-height:1.5; color:#111827 !important; }
.ce-opt-sel { background:#dbeafe; border-color:#1d4ed8; }
.ce-letter { font-weight:800; color:#1d4ed8 !important; }
.ce-pal { background:#ffffff; border:1px solid #cbd5e1; border-radius:12px; padding:10px; color:#111827 !important; }
.ce-pal-t { font-weight:700; margin-bottom:6px; color:#111827 !important; }
.ce-chip { display:inline-block; width:38px; height:38px; line-height:38px; text-align:center; border-radius:8px;
  background:#e2e8f0; color:#111827 !important; font-weight:700; margin:3px; }
.ce-chip-ans { background:#bbf7d0; color:#14532d !important; }
.ce-chip-mark { background:#fed7aa; color:#7c2d12 !important; }
.ce-chip-cur { background:#1d4ed8; color:#ffffff !important; }
.ce-legend { font-size:13px; color:#334155 !important; margin-top:6px; }
.ce-warn { background:#fef3c7; border:1px solid #f59e0b; color:#78350f !important; border-radius:10px; padding:10px; margin-bottom:10px; font-weight:600; }
.ce-res-title { font-size:26px; font-weight:800; color:#1e3a8a !important; }
.ce-score { font-size:34px; font-weight:800; color:#111827 !important; margin:8px 0; }
.ce-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:8px 0; }
.ce-stat { border-radius:10px; padding:10px; text-align:center; font-size:15px; }
.ce-stat b { font-size:24px; }
.ce-s-ok { background:#dcfce7; color:#14532d !important; } .ce-s-ok b { color:#14532d !important; }
.ce-s-bad { background:#fee2e2; color:#7f1d1d !important; } .ce-s-bad b { color:#7f1d1d !important; }
.ce-s-na { background:#e5e7eb; color:#1f2937 !important; } .ce-s-na b { color:#1f2937 !important; }
.ce-s-mk { background:#ffedd5; color:#7c2d12 !important; } .ce-s-mk b { color:#7c2d12 !important; }
.ce-acc { font-size:20px; color:#111827 !important; margin-top:6px; } .ce-acc b { color:#111827 !important; }
.ce-rev-head { font-size:22px; font-weight:800; color:#1e3a8a !important; margin:8px 0; }
.ce-rev { background:#ffffff; border:1px solid #cbd5e1; border-left:6px solid #94a3b8; border-radius:12px;
  padding:12px; margin:10px 0; color:#111827 !important; }
.ce-rev-ok { border-left-color:#16a34a; background:#f0fdf4; }
.ce-rev-bad { border-left-color:#dc2626; background:#fef2f2; }
.ce-rev-na { border-left-color:#6b7280; background:#f9fafb; }
.ce-rev div, .ce-rev b, .ce-rev span { color:#111827; }
.ce-rev-q { font-size:17px; font-weight:700; line-height:1.55; color:#111827 !important; margin-bottom:6px; }
.ce-line { font-size:16px; line-height:1.6; color:#111827 !important; }
.ce-expl { background:#fffbeb; border-left:4px solid #f59e0b; border-radius:8px; padding:10px; margin-top:8px;
  font-size:16px; line-height:1.6; color:#1f2937 !important; }
.ce-expl * { color:#1f2937 !important; }
"""


# =====================================================================
#  8. UI HANDLERS
# =====================================================================
def upd(**kw):
    """Version-safe component update."""
    if hasattr(gr, "update"):
        return gr.update(**kw)
    d = {"__type__": "update"}
    d.update(kw)
    return d


def timer_ctl(active):
    try:
        return gr.Timer(active=active)
    except Exception:
        return upd(active=active)


def _screen(mode):
    """(setup_col, test_col, result_col, confirm_col) visibility updates."""
    vis = {"setup": (True, False, False, False),
           "test": (False, True, False, False),
           "result": (False, False, True, False)}[mode]
    return tuple(upd(visible=v) for v in vis)


def _save(st, choice):
    if st and st.get("active"):
        st["answers"][st["cur"]] = choice if choice in LETTERS else ""


def view_updates(st, set_radio=True):
    """Exactly 7 values -> [timer_html, progress, question, radio, palette, mark_btn, jump_num]."""
    i = st["cur"]
    return (timer_html(st), progress_html(st), question_html(st),
            upd(value=(st["answers"][i] or None)) if set_radio else upd(),
            palette_html(st),
            upd(value=("❌ Unmark" if st["marked"][i] else "🔖 Mark for Review")),
            upd(value=i + 1))


N_VIEW = 7
N_SCREEN = 8   # state, setup, test, result, confirm, setup_msg, retest_msg, timer


def _nochange_view():
    return tuple(upd() for _ in range(N_VIEW))


def on_select(exam, subject, topic, diff):
    s = build_selectors(exam, subject, topic, diff)
    return (upd(choices=s["subject"][0], value=s["subject"][1]),
            upd(choices=s["topic"][0], value=s["topic"][1]),
            upd(choices=s["diff"][0], value=s["diff"][1]),
            s["info"])


def _start(st_old, questions, meta, label):
    st = new_test_state(questions, meta, label)
    sc = _screen("test")
    head = (st, sc[0], sc[1], sc[2], sc[3], "", "", timer_ctl(True))
    return head + view_updates(st)


def on_start(st_old, exam, subject, topic, diff, count):
    questions, meta, msg = generate_test(exam, subject, topic, diff, count)
    if questions is None:
        head = (st_old, upd(), upd(), upd(), upd(), f"<div class='ce-msg'>{msg}</div>", upd(), upd())
        return head + _nochange_view()
    return _start(st_old, questions, meta, "Test")


def on_retest(st_old, include_na):
    qs = retry_wrong(st_old, include_na)
    if not qs:
        what = "galat ya un-attempted" if include_na else "galat"
        head = (st_old, upd(), upd(), upd(), upd(), upd(),
                f"<div class='ce-ok-msg'>🎉 Koi {what} question nahi hai — retest ki zaroorat nahi!</div>", upd())
        return head + _nochange_view()
    meta = dict((st_old or {}).get("meta") or {})
    return _start(st_old, qs, meta, "Retest")


def on_retest_wrong(st):
    return on_retest(st, False)


def on_retest_all(st):
    return on_retest(st, True)


def on_new_test():
    sc = _screen("setup")
    return (None, sc[0], sc[1], sc[2], sc[3], "", "", timer_ctl(False))


def _guard(st):
    return bool(st) and st.get("active")


def on_answer(st, choice):
    if not _guard(st):
        return st, upd(), upd(), upd()
    _save(st, choice)
    return st, progress_html(st), question_html(st), palette_html(st)


def on_prev(st, choice):
    if not _guard(st):
        return (st,) + _nochange_view()
    _save(st, choice)
    st["cur"] = max(0, st["cur"] - 1)
    return (st,) + view_updates(st)


def on_next(st, choice):
    if not _guard(st):
        return (st,) + _nochange_view()
    _save(st, choice)
    st["cur"] = min(len(st["questions"]) - 1, st["cur"] + 1)
    return (st,) + view_updates(st)


def on_jump(st, choice, num):
    if not _guard(st):
        return (st,) + _nochange_view()
    _save(st, choice)
    try:
        n = int(float(num))
    except Exception:
        n = st["cur"] + 1
    st["cur"] = min(max(1, n), len(st["questions"])) - 1
    return (st,) + view_updates(st)


def on_mark(st, choice):
    if not _guard(st):
        return (st,) + _nochange_view()
    _save(st, choice)
    st["marked"][st["cur"]] = not st["marked"][st["cur"]]
    return (st,) + view_updates(st, set_radio=False)


def on_clear(st):
    if not _guard(st):
        return (st,) + _nochange_view()
    st["answers"][st["cur"]] = ""
    return (st,) + view_updates(st, set_radio=True)


def on_ask_submit(st, choice):
    if not _guard(st):
        return st, upd(), upd(), upd(), upd(), upd()
    _save(st, choice)
    n = len(st["questions"])
    ans = sum(1 for a in st["answers"] if a)
    mk = sum(st["marked"])
    msg = (f"<div class='ce-warn'>Submit karna hai? <br>Answered: <b>{ans}</b> / {n} &nbsp;·&nbsp; "
           f"Not attempted: <b>{n - ans}</b> &nbsp;·&nbsp; Marked: <b>{mk}</b><br>"
           f"Submit ke baad answers change nahi ho sakte.</div>")
    return st, upd(visible=True), msg, progress_html(st), palette_html(st), question_html(st)


def on_cancel_submit():
    return upd(visible=False)


def finish_core(st):
    """8 values -> [setup, test, result, confirm, summary, review, retest_msg, timer]."""
    res = st.get("result") or submit_test(st)
    sc = _screen("result")
    return (sc[0], sc[1], sc[2], sc[3], summary_html(st, res), review_html(st, res), "", timer_ctl(False))


def on_confirm_submit(st, choice):
    if not st or not st.get("questions"):
        return (st,) + tuple(upd() for _ in range(8))
    if st.get("active"):
        _save(st, choice)
        submit_test(st)
    return (st,) + finish_core(st)


def on_tick(st, choice):
    """Every second while a test is active. Auto-submits at 0."""
    if not _guard(st):
        return tuple(upd() for _ in range(9))
    rem = st["end_ts"] - time.time()
    if rem > 0:
        return (timer_html(st),) + tuple(upd() for _ in range(8))
    _save(st, choice)
    st["auto"] = True
    submit_test(st)
    return (timer_html(st),) + finish_core(st)


# =====================================================================
#  9. BUILD UI
# =====================================================================
def build_app():
    sel = build_selectors()
    blocks_kw = {"title": "ChetExam AI"}
    if GR_MAJOR < 6:
        blocks_kw["css"] = CSS

    with gr.Blocks(**blocks_kw) as demo:
        gr.HTML(value=f"<style>{CSS}</style>")
        state = gr.State(None)
        gr.HTML(value=HEADER_HTML)

        # ---------------- SETUP SCREEN ----------------
        with gr.Column(visible=True) as setup_col:
            exam_dd = gr.Dropdown(choices=sel["exam"][0], value=sel["exam"][1],
                                  label="1️⃣ Exam", filterable=False, interactive=True)
            subject_dd = gr.Dropdown(choices=sel["subject"][0], value=sel["subject"][1],
                                     label="2️⃣ Subject", filterable=False, interactive=True)
            topic_dd = gr.Dropdown(choices=sel["topic"][0], value=sel["topic"][1],
                                   label="3️⃣ Topic", filterable=False, interactive=True)
            diff_dd = gr.Dropdown(choices=sel["diff"][0], value=sel["diff"][1],
                                  label="4️⃣ Difficulty", filterable=False, interactive=True)
            count_rd = gr.Radio(choices=COUNT_CHOICES, value=COUNT_CHOICES[0],
                                label="5️⃣ Number of Questions (1 minute per question)")
            info_html = gr.HTML(value=sel["info"])
            start_btn = gr.Button("🚀 START TEST", variant="primary", size="lg")
            setup_msg = gr.HTML(value="")

        # ---------------- TEST SCREEN ----------------
        with gr.Column(visible=False) as test_col:
            timer_box = gr.HTML(value="", elem_id="timer_box")
            progress_box = gr.HTML(value="")
            question_box = gr.HTML(value="")
            answer_rd = gr.Radio(choices=LETTERS, value=None, label="Your Answer", elem_id="ans_radio",
                                 interactive=True)
            with gr.Row(elem_id="act_row"):
                clear_btn = gr.Button("🧹 Clear", variant="secondary", min_width=80)
                mark_btn = gr.Button("🔖 Mark for Review", variant="secondary", min_width=80)
            with gr.Row(elem_id="nav_row"):
                prev_btn = gr.Button("⬅ Previous", variant="secondary", min_width=80)
                next_btn = gr.Button("Next ➡", variant="primary", min_width=80)
            with gr.Row(elem_id="jump_row"):
                jump_num = gr.Number(label="Jump to question no.", value=1, precision=0, scale=3, min_width=100)
                jump_btn = gr.Button("Go", variant="secondary", scale=1, min_width=60)
            palette_box = gr.HTML(value="")
            submit_btn = gr.Button("📤 Submit Test", variant="stop", size="lg")
            with gr.Column(visible=False) as confirm_col:
                confirm_html = gr.HTML(value="")
                with gr.Row():
                    yes_btn = gr.Button("✅ Yes, Submit", variant="stop", min_width=100)
                    no_btn = gr.Button("↩ Wapas jao", variant="secondary", min_width=100)

        # ---------------- RESULT SCREEN ----------------
        with gr.Column(visible=False) as result_col:
            summary_box = gr.HTML(value="")
            retest_msg = gr.HTML(value="")
            retest_wrong_btn = gr.Button("🔄 RETEST WRONG QUESTIONS", variant="primary", size="lg")
            retest_all_btn = gr.Button("🔄 RETEST WRONG + NOT ATTEMPTED", variant="secondary")
            new_btn = gr.Button("🏠 New Test", variant="secondary")
            review_box = gr.HTML(value="")

        timer = gr.Timer(1.0, active=False)

        # ---------------- WIRING ----------------
        VIEW_OUTS = [timer_box, progress_box, question_box, answer_rd, palette_box, mark_btn, jump_num]
        SCREEN_OUTS = [state, setup_col, test_col, result_col, confirm_col, setup_msg, retest_msg, timer]
        START_OUTS = SCREEN_OUTS + VIEW_OUTS
        NAV_OUTS = [state] + VIEW_OUTS
        FINISH_OUTS = [setup_col, test_col, result_col, confirm_col, summary_box, review_box, retest_msg, timer]
        assert len(VIEW_OUTS) == N_VIEW and len(SCREEN_OUTS) == N_SCREEN and len(FINISH_OUTS) == 8

        sel_in = [exam_dd, subject_dd, topic_dd, diff_dd]
        sel_out = [subject_dd, topic_dd, diff_dd, info_html]
        for dd in sel_in:
            dd.input(on_select, sel_in, sel_out)

        start_btn.click(on_start, [state] + sel_in + [count_rd], START_OUTS)

        answer_rd.input(on_answer, [state, answer_rd], [state, progress_box, question_box, palette_box])
        prev_btn.click(on_prev, [state, answer_rd], NAV_OUTS)
        next_btn.click(on_next, [state, answer_rd], NAV_OUTS)
        jump_btn.click(on_jump, [state, answer_rd, jump_num], NAV_OUTS)
        jump_num.submit(on_jump, [state, answer_rd, jump_num], NAV_OUTS)
        mark_btn.click(on_mark, [state, answer_rd], NAV_OUTS)
        clear_btn.click(on_clear, [state], NAV_OUTS)

        submit_btn.click(on_ask_submit, [state, answer_rd],
                         [state, confirm_col, confirm_html, progress_box, palette_box, question_box])
        no_btn.click(on_cancel_submit, None, [confirm_col])
        yes_btn.click(on_confirm_submit, [state, answer_rd], [state] + FINISH_OUTS)
        timer.tick(on_tick, [state, answer_rd], [timer_box] + FINISH_OUTS)

        retest_wrong_btn.click(on_retest_wrong, [state], START_OUTS)
        retest_all_btn.click(on_retest_all, [state], START_OUTS)
        new_btn.click(on_new_test, None, SCREEN_OUTS)

    try:
        demo.queue(default_concurrency_limit=16)
    except TypeError:
        demo.queue()
    return demo


# =====================================================================
#  10. MAIN
# =====================================================================
def main():
    global DB, REPORT
    raw = load_database()
    DB, REPORT = normalize_database(raw)
    print_diagnostics(REPORT)
    if DB.empty:
        raise RuntimeError("Koi valid MCQ nahi mila. Upar diagnostics me 'Invalid' reasons dekho "
                           "(Correct Answer column A/B/C/D me hona chahiye).")
    demo = build_app()
    launch_kw = dict(share=True, debug=True, show_error=True)
    if GR_MAJOR >= 6:
        try:
            demo.launch(css=CSS, **launch_kw)      # Gradio 6: css goes into launch()
            return
        except TypeError:
            pass
    demo.launch(**launch_kw)


if os.environ.get("CHETEXAM_SKIP_LAUNCH") != "1":
    main()
