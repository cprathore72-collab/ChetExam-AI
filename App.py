# ================================================================
# ChetExam AI — FINAL RENDER VERSION
# Google Sheet → Dynamic Exam Test Platform
# ================================================================

import os
import re
import html
import time
import random
import unicodedata
from collections import Counter

import numpy as np
import pandas as pd

# ----------------------------------------------------------------
# huggingface_hub compatibility patch
# gradio 4.44.1 uses HfFolder which was removed in
# huggingface_hub >= 0.24.0. This stub prevents the ImportError
# on newer server environments (like Raven).
# ----------------------------------------------------------------
try:
    from huggingface_hub import HfFolder as _HfFolderTest  # noqa
except ImportError:
    import huggingface_hub as _hfh

    class _HfFolderStub:
        @staticmethod
        def get_token():
            return None

        @staticmethod
        def save_token(token):
            pass

        @staticmethod
        def delete_token():
            pass

    _hfh.HfFolder = _HfFolderStub
    del _hfh
# ----------------------------------------------------------------

import gradio as gr


# ================================================================
# 1. CONFIG
# ================================================================

SHEET_ID = "1Hw2cRzZs9ZvCPDrpC2ehi83PHgmeTAgKlNGMs8hHZVw"
SHEET_TAB = "MCQ_DATABASE"

MAX_Q = 100
SECONDS_PER_Q = 60

DEFAULT_EXAM = "RAS"
MIN_ROWS_FOR_NEW_EXAM = 30

ALL_TOPICS = "All Topics"
ALL_DIFF = "All"

LETTERS = ["A", "B", "C", "D"]

DIFFS = [
    "Easy",
    "Medium",
    "Hard"
]

COUNT_CHOICES = [
    "10",
    "20",
    "50",
    "100",
    "All Available (max 100)"
]

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

SUBJECT_ALIASES = {}
TOPIC_ALIASES = {}

EXCLUDE_CHECK_KEYWORDS = []

SHOW_TOPICS_IN_DIAGNOSTICS = True


# ================================================================
# 1B. STUDENT REGISTRATION CONFIG
# ================================================================
#
# 👉 ONE-TIME SETUP NEEDED (see chat reply for full explanation):
# The existing Apps Script Web App below currently requires a secret
# that we don't have. Redeploy it with the no-secret version of the
# script given in the chat reply, then paste the resulting /exec URL
# here. If you reuse the SAME URL after removing the secret check
# inside the script, you don't even need to change this constant.

STUDENT_SHEET_ID = "1jG3fhzP8_TPki3SqeV3rtC5sY28bXkmXVmQLyXv7dyA"
STUDENT_SHEET_TAB = "STUDENTS"

STUDENT_WEBAPP_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbxBWWufizRwwFlcTxkbPabv7c4hvq2ocLNlfMePhAeZH7FxvgKzAD-VlwNfwlHzRnK/exec"
)

REGISTRATION_SOURCE = "ChetExam AI"

# One-time WhatsApp share gate.
# The browser counts three WhatsApp share launches before registration
# is allowed. WhatsApp does not expose delivery/recipient confirmation
# to a third-party website, so this is a share-action gate, not delivery verification.
REQUIRED_WHATSAPP_SHARES = 3

# Optional: if the Apps Script Web App uses a secret,
# set STUDENT_API_SECRET in the hosting environment.
# If the Web App does not use a secret, leave it unset.
STUDENT_API_SECRET = os.environ.get(
    "STUDENT_API_SECRET",
    ""
).strip()

MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


# ================================================================
# 2. DATABASE COLUMNS
# ================================================================

COLS = [
    "id",
    "exam_raw",
    "subject_raw",
    "topic_raw",
    "question",
    "opt_A",
    "opt_B",
    "opt_C",
    "opt_D",
    "correct_raw",
    "explanation",
    "difficulty_raw",
    "check_result",
    "ai_check",
]

HEADER_KEYS = [
    "id",
    "exam",
    "subject",
    "topic",
    "question",
    "option a",
    "option b",
    "option c",
    "option d",
    "correct answer",
    "explanation",
    "difficulty",
    "check result",
    "ai check",
]


DB = pd.DataFrame()
REPORT = {}


# ================================================================
# 3. TEXT HELPERS
# ================================================================

_ZW = {
    ord(c): None
    for c in "​‌‍⁠﻿"
}

_JUNK = {
    "nan",
    "none",
    "null",
    "nat",
    "n/a",
    "-",
    "--",
    "—",
}


def clean(x):
    if x is None:
        return ""

    if isinstance(x, float) and np.isnan(x):
        return ""

    s = unicodedata.normalize(
        "NFC",
        str(x)
    )

    s = (
        s.replace("\xa0", " ")
         .replace("\ufeff", "")
    )

    s = re.sub(
        r"[ \t\f\v\r]+",
        " ",
        s
    )

    s = re.sub(
        r" ?\n ?",
        "\n",
        s
    )

    return s.strip()


def clean_meta(x):
    s = clean(x)

    if s.lower() in _JUNK:
        return ""

    return s


def key(x):
    s = unicodedata.normalize(
        "NFKC",
        clean(x)
    ).translate(_ZW).casefold()

    s = re.sub(
        r"\s+",
        " ",
        s
    )

    return s.strip(
        " .:-–—_,;"
    )


def esc(s):
    return html.escape(
        str(s if s is not None else "")
    ).replace(
        "\n",
        "<br>"
    )


def fmt_time(sec):
    sec = max(
        0,
        int(sec)
    )

    h, r = divmod(
        sec,
        3600
    )

    m, s = divmod(
        r,
        60
    )

    if h:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m:02d}:{s:02d}"


# ================================================================
# 4. GOOGLE SHEET LOADER
# ================================================================

def load_google_sheet():
    """
    Google Sheet ko public CSV ke through read karta hai.
    Sheet me koi modification nahi karta.
    """

    from urllib.parse import quote

    url = (
        f"https://docs.google.com/spreadsheets/d/"
        f"{SHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet="
        f"{quote(SHEET_TAB)}"
    )

    print("")
    print("=" * 70)
    print("📥 Loading Google Sheet...")
    print("=" * 70)

    print(
        f"Sheet ID : {SHEET_ID}"
    )

    print(
        f"Sheet Tab: {SHEET_TAB}"
    )

    try:
        df = pd.read_csv(
            url,
            dtype=str,
            keep_default_na=False,
            header=None
        )

    except Exception as e:
        raise RuntimeError(
            "\n❌ Google Sheet load nahi ho paayi.\n\n"
            "Check karo:\n"
            "1. Google Sheet 'Anyone with the link' Viewer ho.\n"
            "2. Sheet ID correct ho.\n"
            "3. Tab name MCQ_DATABASE ho.\n\n"
            f"Original error: {type(e).__name__}: {e}"
        )

    if df.empty:
        raise RuntimeError(
            "❌ Google Sheet empty hai."
        )

    print(
        f"✅ Raw rows loaded: {len(df)}"
    )

    return df.values.tolist()


# ================================================================
# 4B. STUDENT REGISTRATION HELPERS
# ================================================================

def normalize_mobile(raw):
    """
    Strips spaces, +91, hyphens, leading 0 etc.
    Returns digits-only string (not validated yet).
    """

    s = re.sub(
        r"\D",
        "",
        str(raw or "")
    )

    # Indian mobiles never start with 0, so stripping leading
    # zeros first safely handles "0091...", "091...", "0...".
    s = s.lstrip("0")

    if (
        s.startswith("91")
        and len(s) == 12
    ):
        s = s[2:]

    return s


def validate_student(name, mobile):
    """
    Returns (clean_name, clean_mobile, error_message).
    error_message is "" when valid.
    """

    clean_name = clean(name)

    clean_mobile = normalize_mobile(
        mobile
    )

    if len(clean_name) < 2:
        return (
            None,
            None,
            "⚠️ Naam kam se kam 2 characters ka hona chahiye."
        )

    if not MOBILE_RE.match(clean_mobile):
        return (
            None,
            None,
            "⚠️ Mobile number 10 digit ka hona chahiye "
            "aur 6, 7, 8 ya 9 se start hona chahiye."
        )

    return (
        clean_name,
        clean_mobile,
        ""
    )


def register_student(name, mobile, consent):
    """
    Best-effort write to the STUDENTS Google Sheet via the Apps
    Script Web App, using ONLY the Python standard library (no
    'requests' dependency, no secret, no environment variable).

    This never raises — a network/script failure is only printed
    to the console so the student's test is never blocked by it.
    """

    if not STUDENT_WEBAPP_URL:
        print(
            "⚠️ STUDENT_WEBAPP_URL not set — "
            "skipping student sheet write."
        )
        return

    import json
    import urllib.request
    import urllib.error

    payload_data = {
        "name": name,
        "mobile": mobile,
        "consent": bool(consent),
        "source": REGISTRATION_SOURCE,
    }

    if STUDENT_API_SECRET:
        payload_data["secret"] = STUDENT_API_SECRET

    payload = json.dumps(
        payload_data
    ).encode("utf-8")

    request = urllib.request.Request(
        STUDENT_WEBAPP_URL,
        data=payload,
        headers={
            "Content-Type": "application/json"
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=8
        ) as response:
            response.read()

        print(
            f"✅ Student registered in sheet: {name} ({mobile})"
        )

    except Exception as e:
        print(
            "⚠️ Student sheet write failed "
            "(test will still continue): "
            f"{type(e).__name__}: {e}"
        )


# ================================================================
# 5. RAW ROWS → DATAFRAME
# ================================================================

def rows_to_df(rows):

    width = len(COLS)

    rows = [
        (
            list(r)
            + [""] * width
        )[:max(width, len(r))]
        for r in rows
        if r is not None
    ]

    if not rows:
        raise RuntimeError(
            "❌ Sheet me koi data nahi mila."
        )

    header = [
        key(h)
        for h in rows[0]
    ]

    body = rows[1:]

    header_like = (
        "question" in header
        or (
            header
            and header[0] == "id"
        )
    )

    if header_like:

        pos = {
            h: i
            for i, h in enumerate(header)
            if h
        }

        if all(
            k in pos
            for k in HEADER_KEYS[:12]
        ):

            idxs = [
                pos.get(k)
                for k in HEADER_KEYS
            ]

        else:

            idxs = list(
                range(width)
            )

    else:

        body = rows

        idxs = list(
            range(width)
        )

    output = []

    for r in body:

        output.append(
            [
                (
                    r[i]
                    if (
                        i is not None
                        and i < len(r)
                    )
                    else ""
                )
                for i in idxs
            ]
        )

    return pd.DataFrame(
        output,
        columns=COLS
    )


# ================================================================
# 6. SUBJECT / EXAM DETECTION
# ================================================================

_SUBJECT_HINTS_DEV = [
    "इतिहास",
    "भूगोल",
    "राजव्यवस्था",
    "राजनीति",
    "अर्थव्यवस्था",
    "अर्थशास्त्र",
    "विज्ञान",
    "प्रौद्योगिकी",
    "कला",
    "संस्कृति",
    "साहित्य",
    "गणित",
    "तर्कशक्ति",
    "समसामयिक",
    "पर्यावरण",
    "सामान्य ज्ञान",
    "संविधान",
    "कृषि",
    "हिंदी",
    "हिन्दी",
    "अंग्रेजी",
    "जनसंख्या",
]

_SUBJECT_HINTS_ASCII = re.compile(
    r"(?<![a-z])"
    r"(history|geography|polity|economy|economics|science|"
    r"technology|culture|art|environment|ecology|reasoning|"
    r"aptitude|maths?|mathematics|gk|constitution|agriculture|"
    r"literature|current affairs|general knowledge|hindi|"
    r"english|biology|physics|chemistry)"
    r"(?![a-z])"
)

_OTHER_EXAM_RE = re.compile(
    r"(?<![a-z])"
    r"(upsc|ias|ips|ssc|cgl|chsl|reet|rtet|ctet|patwari|"
    r"constable|si|ldc|udc|rssb|ibps|sbi|rbi|rrb|ntpc|net|"
    r"jrf|gate|neet|jee|cds|nda|clat|cuet|banking|railway|"
    r"vdo|police|fci)"
    r"(?![a-z])"
)


def looks_like_subject(k):

    if not k:
        return False

    if _SUBJECT_HINTS_ASCII.search(k):
        return True

    return any(
        h in k
        for h in _SUBJECT_HINTS_DEV
    )


def classify_exam(
    raw,
    cnt,
    subject_keys
):

    k = key(raw)

    if not k:
        return (
            "blank",
            DEFAULT_EXAM
        )

    if k in EXAM_ALIASES:
        return (
            "exam",
            EXAM_ALIASES[k]
        )

    if (
        re.search(
            r"(?<![a-z])ras(?![a-z])",
            k
        )
        or "rajasthan administrative" in k
    ):

        if (
            re.search(
                r"(?<![a-z])"
                r"(pre|prelim|prelims|preliminary)"
                r"(?![a-z])",
                k
            )
            or "प्रारंभिक" in k
            or "प्री" in k
        ):
            return (
                "exam",
                "RAS Pre"
            )

        if (
            "mains" in k
            or "मुख्य" in k
        ):
            return (
                "exam",
                "RAS Mains"
            )

        return (
            "exam",
            "RAS"
        )

    if _OTHER_EXAM_RE.search(k):

        return (
            "exam",
            clean(raw)
        )

    if (
        looks_like_subject(k)
        or k in subject_keys
    ):
        return (
            "subject",
            None
        )

    if cnt >= MIN_ROWS_FOR_NEW_EXAM:

        return (
            "exam",
            clean(raw)
        )

    return (
        "unknown-small",
        DEFAULT_EXAM
    )


# ================================================================
# 7. ANSWER NORMALIZATION
# ================================================================

_DEV_LETTER = {
    "क": "A",
    "ख": "B",
    "ग": "C",
    "घ": "D",
}

_NUM_LETTER = {
    "1": "A",
    "2": "B",
    "3": "C",
    "4": "D",
}


def normalize_answer(
    raw,
    opts
):

    s = clean(raw)

    if not s:
        return ""

    u = unicodedata.normalize(
        "NFKC",
        s
    )

    up = u.upper().strip()

    m = re.fullmatch(
        r"[\(\[\{]?\s*([ABCD1-4])\s*[\)\]\}\.\:]?",
        up
    )

    if m:

        c = m.group(1)

        return _NUM_LETTER.get(
            c,
            c
        )

    m = re.fullmatch(
        r"[\(\[\{]?\s*([कखगघ])\s*[\)\]\}\.\:]?",
        u
    )

    if m:
        return _DEV_LETTER[
            m.group(1)
        ]

    m = re.search(
        r"(?:OPTION|ANS(?:WER)?|उत्तर|विकल्प)"
        r"\s*[:\-]?\s*"
        r"[\(\[]?\s*([ABCD1-4])",
        up
    )

    if m:

        c = m.group(1)

        return _NUM_LETTER.get(
            c,
            c
        )

    m = re.match(
        r"[\(\[]?([ABCD])[\)\]\.\:]\s",
        up
    )

    if m:
        return m.group(1)

    ks = key(s)

    for L in LETTERS:

        if (
            opts.get(L)
            and key(opts[L]) == ks
        ):
            return L

    return ""


# ================================================================
# 8. DIFFICULTY
# ================================================================

def normalize_difficulty(raw):

    k = key(raw)

    if not k:
        return "Medium"

    if k in (
        "1",
        "2",
        "3"
    ):

        return {
            "1": "Easy",
            "2": "Medium",
            "3": "Hard",
        }[k]

    if any(
        x in k
        for x in (
            "hard",
            "difficult",
            "tough",
            "advanced",
            "कठिन",
            "मुश्किल",
            "कठीन",
        )
    ):
        return "Hard"

    if any(
        x in k
        for x in (
            "easy",
            "simple",
            "basic",
            "आसान",
            "सरल",
            "सुगम",
        )
    ):
        return "Easy"

    if any(
        x in k
        for x in (
            "medium",
            "moderate",
            "average",
            "मध्यम",
            "मध्य",
        )
    ):
        return "Medium"

    return "Medium"


# ================================================================
# 9. CANONICAL LABELS
# ================================================================

def canonical_labels(
    series,
    aliases
):

    alias_map = {
        key(a): v
        for a, v in aliases.items()
    }

    keys = series.map(key)

    temp = pd.DataFrame({
        "k": keys,
        "v": series,
    })

    display = (
        temp
        .groupby("k")["v"]
        .agg(
            lambda x:
            x.value_counts().idxmax()
        )
    )

    out = keys.map(
        display
    )

    return pd.Series(
        [
            alias_map.get(
                k,
                v
            )
            for k, v in zip(
                keys,
                out
            )
        ],
        index=series.index
    )


# ================================================================
# 10. NORMALIZE DATABASE
# ================================================================

def normalize_database(raw):

    df = raw.copy()

    total_rows = len(df)

    for c in [
        "id",
        "question",
        "opt_A",
        "opt_B",
        "opt_C",
        "opt_D",
        "explanation",
        "correct_raw",
    ]:

        df[c] = df[c].map(
            clean
        )

    for c in [
        "exam_raw",
        "subject_raw",
        "topic_raw",
        "difficulty_raw",
        "check_result",
        "ai_check",
    ]:

        df[c] = df[c].map(
            clean_meta
        )

    blank_mask = (
        (df["question"] == "")
        &
        (
            df[
                [
                    "opt_A",
                    "opt_B",
                    "opt_C",
                    "opt_D",
                ]
            ] == ""
        ).all(axis=1)
    )

    blank_rows = int(
        blank_mask.sum()
    )

    df = (
        df[
            ~blank_mask
        ]
        .reset_index(drop=True)
    )

    subject_keys = set(
        df["subject_raw"].map(key)
    ) - {""}

    exam_counts = (
        df["exam_raw"]
        .value_counts()
    )

    exam_info = {
        raw: classify_exam(
            raw,
            int(cnt),
            subject_keys
        )
        for raw, cnt
        in exam_counts.items()
    }

    if "" not in exam_info:

        exam_info[""] = (
            "blank",
            DEFAULT_EXAM
        )

    exams = []
    subjects = []
    topics = []
    rerouted = 0

    for (
        e_raw,
        s_raw,
        t_raw
    ) in zip(
        df["exam_raw"],
        df["subject_raw"],
        df["topic_raw"]
    ):

        kind, canon = exam_info[
            e_raw
        ]

        if kind == "subject":

            rerouted += 1

            exam = DEFAULT_EXAM

            subject = e_raw

            topic = (
                t_raw
                or (
                    s_raw
                    if key(s_raw)
                    != key(e_raw)
                    else ""
                )
            )

        else:

            exam = canon

            subject = (
                s_raw
                or "General"
            )

            topic = t_raw

        exams.append(exam)

        subjects.append(
            subject
        )

        topics.append(
            topic or "Other"
        )

    df["exam"] = exams

    df["subject"] = subjects

    df["topic"] = topics

    df["exam"] = canonical_labels(
        df["exam"],
        {}
    )

    df["subject"] = canonical_labels(
        df["subject"],
        SUBJECT_ALIASES
    )

    df["topic"] = canonical_labels(
        df["topic"],
        TOPIC_ALIASES
    )

    correct = []
    reasons = []

    exclude_words = [
        key(x)
        for x in EXCLUDE_CHECK_KEYWORDS
    ]

    for i in range(len(df)):

        opts = {
            L: df.at[
                i,
                f"opt_{L}"
            ]
            for L in LETTERS
        }

        ans = normalize_answer(
            df.at[
                i,
                "correct_raw"
            ],
            opts
        )

        correct.append(ans)

        n_opts = sum(
            1
            for L in LETTERS
            if opts[L]
        )

        reason = ""

        if len(
            df.at[i, "question"]
        ) < 3:

            reason = "empty question"

        elif n_opts < 2:

            reason = "fewer than 2 options"

        elif ans == "":

            reason = (
                "correct answer "
                "not understood"
            )

        elif not opts[ans]:

            reason = (
                "correct option "
                "is empty"
            )

        elif exclude_words:

            combined = (
                key(
                    df.at[
                        i,
                        "check_result"
                    ]
                )
                + " "
                + key(
                    df.at[
                        i,
                        "ai_check"
                    ]
                )
            )

            if any(
                w in combined
                for w in exclude_words
            ):
                reason = (
                    "excluded by "
                    "check keyword"
                )

        reasons.append(reason)

    df["correct"] = correct

    df["difficulty"] = (
        df["difficulty_raw"]
        .map(
            normalize_difficulty
        )
    )

    df["qkey"] = [
        key(q)
        + "||"
        + "|".join(
            key(o)
            for o in (
                a,
                b,
                c,
                d
            )
        )
        for q, a, b, c, d
        in zip(
            df["question"],
            df["opt_A"],
            df["opt_B"],
            df["opt_C"],
            df["opt_D"],
        )
    ]

    df["_reason"] = reasons

    valid = (
        df[
            df["_reason"] == ""
        ]
        .copy()
        .reset_index(drop=True)
    )

    report = {
        "total_rows": total_rows,
        "blank_rows": blank_rows,
        "valid": len(valid),
        "invalid_reasons": Counter(
            r
            for r in reasons
            if r
        ),
        "exam_map": sorted(
            [
                (
                    raw
                    if raw
                    else "(blank)",
                    int(
                        exam_counts.get(
                            raw,
                            0
                        )
                    ),
                    kind,
                    canon
                    or "→ Subject",
                )
                for raw, (
                    kind,
                    canon
                )
                in exam_info.items()
                if int(
                    exam_counts.get(
                        raw,
                        0
                    )
                ) > 0
            ],
            key=lambda x: -x[1]
        ),
        "rerouted": rerouted,
        "duplicates": int(
            valid["qkey"]
            .duplicated()
            .sum()
        ),
    }

    return valid, report


# ================================================================
# 11. QUERY ENGINE
# ================================================================

def scope(
    exam=None,
    subject=None,
    topic=None,
    difficulty=None
):

    d = DB

    if exam:
        d = d[
            d["exam"] == exam
        ]

    if subject:
        d = d[
            d["subject"] == subject
        ]

    if (
        topic
        and topic != ALL_TOPICS
    ):
        d = d[
            d["topic"] == topic
        ]

    if (
        difficulty
        and difficulty != ALL_DIFF
    ):
        d = d[
            d["difficulty"]
            == difficulty
        ]

    return d


def count_questions(
    exam=None,
    subject=None,
    topic=None,
    difficulty=None
):

    if DB.empty:
        return 0

    return int(
        scope(
            exam,
            subject,
            topic,
            difficulty
        )["qkey"]
        .nunique()
    )


def get_exams():

    if DB.empty:
        return []

    counts = (
        DB.groupby("exam")[
            "qkey"
        ].nunique()
    )

    names = list(
        counts.index
    )

    preferred = [
        e
        for e in (
            "RAS",
            "RAS Pre"
        )
        if e in names
    ]

    rest = sorted(
        [
            e
            for e in names
            if e not in preferred
        ],
        key=lambda e: (
            -int(counts[e]),
            e
        )
    )

    return preferred + rest


def get_subject_counts(exam):

    if not exam:
        return {}

    d = scope(exam)

    counts = (
        d.groupby("subject")[
            "qkey"
        ].nunique()
    )

    return dict(
        sorted(
            [
                (
                    s,
                    int(n)
                )
                for s, n
                in counts.items()
            ],
            key=lambda x: (
                -x[1],
                x[0]
            )
        )
    )


def get_subjects(exam):

    return list(
        get_subject_counts(
            exam
        ).keys()
    )


def get_topic_counts(
    exam,
    subject
):

    if not exam or not subject:
        return {}

    d = scope(
        exam,
        subject
    )

    counts = (
        d.groupby("topic")[
            "qkey"
        ].nunique()
    )

    return dict(
        sorted(
            [
                (
                    t,
                    int(n)
                )
                for t, n
                in counts.items()
            ],
            key=lambda x:
            x[0].casefold()
        )
    )


def get_topics(
    exam,
    subject
):

    return [
        ALL_TOPICS
    ] + list(
        get_topic_counts(
            exam,
            subject
        ).keys()
    )


def get_difficulties(
    exam,
    subject,
    topic
):

    d = scope(
        exam,
        subject,
        topic
    )

    out = {
        ALL_DIFF: int(
            d["qkey"]
            .nunique()
        )
    }

    for level in DIFFS:

        out[level] = int(
            d[
                d["difficulty"]
                == level
            ]["qkey"]
            .nunique()
        )

    return out


# ================================================================
# 12. SELECTOR BUILDER
# ================================================================

def build_selectors(
    exam=None,
    subject=None,
    topic=None,
    difficulty=None
):

    exams = get_exams()

    if exam not in exams:

        exam = (
            exams[0]
            if exams
            else None
        )

    subject_counts = (
        get_subject_counts(
            exam
        )
    )

    subjects = list(
        subject_counts.keys()
    )

    if subject not in subjects:

        subject = (
            subjects[0]
            if subjects
            else None
        )

    topic_counts = (
        get_topic_counts(
            exam,
            subject
        )
    )

    topics = [
        ALL_TOPICS
    ] + list(
        topic_counts.keys()
    )

    if topic not in topics:

        topic = ALL_TOPICS

    difficulty_counts = (
        get_difficulties(
            exam,
            subject,
            topic
        )
    )

    if (
        difficulty
        not in difficulty_counts
    ):

        difficulty = ALL_DIFF

    exam_choices = [
        (
            f"{e} ({count_questions(e)})",
            e
        )
        for e in exams
    ]

    subject_choices = [
        (
            f"{s} ({n})",
            s
        )
        for s, n
        in subject_counts.items()
    ]

    total_subject = count_questions(
        exam,
        subject
    )

    topic_choices = [
        (
            f"{ALL_TOPICS} "
            f"({total_subject})",
            ALL_TOPICS
        )
    ]

    topic_choices += [
        (
            f"{t} ({n})",
            t
        )
        for t, n
        in topic_counts.items()
    ]

    difficulty_choices = [
        (
            (
                "All Levels"
                if k == ALL_DIFF
                else k
            )
            + f" ({n})",
            k
        )
        for k, n
        in difficulty_counts.items()
    ]

    if exam and subject:

        info = (
            "<div class='ce-info'>"
            f"📚 <b>{esc(exam)}</b>"
            " › "
            f"<b>{esc(subject)}</b>"
            " › "
            f"<b>{esc(topic)}</b><br>"
            f"Available: "
            f"<b>{difficulty_counts[ALL_DIFF]}</b>"
            " questions &nbsp;·&nbsp; "
            f"Easy {difficulty_counts['Easy']} · "
            f"Medium {difficulty_counts['Medium']} · "
            f"Hard {difficulty_counts['Hard']}<br>"
            f"Selected difficulty "
            f"<b>{esc(difficulty)}</b>"
            " → "
            f"<b>{difficulty_counts.get(difficulty, 0)}</b>"
            " questions"
            "</div>"
        )

    else:

        info = (
            "<div class='ce-info'>"
            "⚠️ Database me valid "
            "question nahi mila."
            "</div>"
        )

    return {
        "exam": (
            exam_choices,
            exam
        ),
        "subject": (
            subject_choices,
            subject
        ),
        "topic": (
            topic_choices,
            topic
        ),
        "diff": (
            difficulty_choices,
            difficulty
        ),
        "info": info,
        "available":
            difficulty_counts.get(
                difficulty,
                0
            ),
    }


# ================================================================
# 13. TEST ENGINE
# ================================================================

def parse_count(choice):

    s = str(
        choice or "10"
    ).strip()

    if s.lower().startswith(
        "all"
    ):
        return "all"

    try:

        return int(
            re.sub(
                r"\D",
                "",
                s
            )
            or 10
        )

    except Exception:

        return 10


def row_to_question(row):

    return {
        "id": row.id,
        "exam": row.exam,
        "subject": row.subject,
        "topic": row.topic,
        "difficulty": row.difficulty,
        "question": row.question,
        "options": {
            "A": row.opt_A,
            "B": row.opt_B,
            "C": row.opt_C,
            "D": row.opt_D,
        },
        "correct": row.correct,
        "explanation":
            row.explanation,
    }


def generate_test(
    exam,
    subject,
    topic,
    difficulty,
    count_choice
):

    if exam not in get_exams():

        return (
            None,
            None,
            "⚠️ Valid Exam select karo."
        )

    if subject not in get_subjects(
        exam
    ):

        return (
            None,
            None,
            "⚠️ Valid Subject select karo."
        )

    if topic not in get_topics(
        exam,
        subject
    ):

        topic = ALL_TOPICS

    if difficulty not in (
        ALL_DIFF,
        *DIFFS
    ):

        difficulty = ALL_DIFF

    pool = (
        scope(
            exam,
            subject,
            topic,
            difficulty
        )
        .drop_duplicates(
            "qkey"
        )
    )

    available = len(pool)

    label = (
        f"{exam} › {subject} › "
        f"{topic} › {difficulty}"
    )

    if available == 0:

        return (
            None,
            None,
            "<b>⚠️ Is selection me "
            "question nahi hai.</b><br>"
            + esc(label)
        )

    requested = parse_count(
        count_choice
    )

    if requested == "all":

        n = min(
            available,
            MAX_Q
        )

    else:

        n = requested

    if n > available:

        return (
            None,
            None,
            f"<b>⚠️ Sirf {available} "
            f"questions available hain.</b><br>"
            f"Aapne {n} maange.<br>"
            "Kam number choose karo "
            "ya All Available select karo."
        )

    selected = pool.sample(
        n=n
    )

    questions = [
        row_to_question(
            row
        )
        for row
        in selected.itertuples(
            index=False
        )
    ]

    meta = {
        "exam": exam,
        "subject": subject,
        "topic": topic,
        "difficulty": difficulty,
    }

    return (
        questions,
        meta,
        ""
    )


def new_test_state(
    questions,
    meta,
    label="Test"
):

    n = len(
        questions
    )

    duration = (
        n
        * SECONDS_PER_Q
    )

    return {
        "active": True,
        "submitted": False,
        "questions": questions,
        "answers": [""] * n,
        "marked": [False] * n,
        "cur": 0,
        "duration": duration,
        "end_ts":
            time.time()
            + duration,
        "meta": meta,
        "label": label,
        "result": None,
        "auto": False,
    }


def submit_test(st):

    questions = st[
        "questions"
    ]

    answers = st[
        "answers"
    ]

    rows = []

    correct = 0
    wrong = 0
    not_attempted = 0

    for i, q in enumerate(
        questions
    ):

        answer = answers[i]

        if not answer:

            status = "na"

            not_attempted += 1

        elif answer == q[
            "correct"
        ]:

            status = "correct"

            correct += 1

        else:

            status = "wrong"

            wrong += 1

        rows.append({
            "idx": i,
            "your": answer,
            "correct":
                q["correct"],
            "status": status,
            "marked":
                bool(
                    st["marked"][i]
                ),
        })

    attempted = (
        correct
        + wrong
    )

    elapsed = (
        st["duration"]
        - max(
            0,
            st["end_ts"]
            - time.time()
        )
    )

    result = {
        "total":
            len(questions),

        "correct":
            correct,

        "wrong":
            wrong,

        "na":
            not_attempted,

        "marked":
            int(
                sum(
                    st["marked"]
                )
            ),

        "accuracy":
            round(
                (
                    100.0
                    * correct
                    / attempted
                )
                if attempted
                else 0.0,
                1
            ),

        "rows": rows,

        "time_used":
            min(
                st["duration"],
                max(
                    0,
                    int(elapsed)
                )
            ),
    }

    st["submitted"] = True
    st["active"] = False
    st["result"] = result

    return result


def retry_wrong(
    st,
    include_not_attempted=False
):

    if (
        not st
        or not st.get(
            "questions"
        )
    ):
        return []

    output = []

    for i, q in enumerate(
        st["questions"]
    ):

        answer = st[
            "answers"
        ][i]

        wrong = (
            answer
            and answer
            != q["correct"]
        )

        not_attempted = (
            not answer
        )

        if (
            wrong
            or (
                include_not_attempted
                and not_attempted
            )
        ):

            output.append(
                dict(
                    q,
                    options=dict(
                        q["options"]
                    )
                )
            )

    random.shuffle(
        output
    )

    return output


# ================================================================
# 14. HTML RENDERERS
# ================================================================

def timer_html(st):

    if not st:
        return ""

    remaining = (
        st["end_ts"]
        - time.time()
    )

    cls = (
        "ce-timer ce-timer-low"
        if remaining <= 60
        else "ce-timer"
    )

    return (
        f"<div class='{cls}'>"
        "⏱ Time Left: "
        f"<b>{fmt_time(remaining)}</b>"
        "</div>"
    )


def progress_html(st):

    n = len(
        st["questions"]
    )

    i = st["cur"]

    answered = sum(
        bool(a)
        for a in st["answers"]
    )

    marked = sum(
        st["marked"]
    )

    return (
        "<div class='ce-progress'>"
        f"<div class='ce-qno'>"
        f"Question {i + 1} / {n}"
        "</div>"
        "<div class='ce-sub'>"
        f"✅ Answered {answered}"
        " &nbsp;·&nbsp; "
        f"🔖 Marked {marked}"
        " &nbsp;·&nbsp; "
        f"⚪ Left {n - answered}"
        "</div>"
        "</div>"
    )


def question_html(st):

    i = st["cur"]

    q = st[
        "questions"
    ][i]

    selected = st[
        "answers"
    ][i]

    options_html = ""

    for letter in LETTERS:

        text = q[
            "options"
        ].get(
            letter,
            ""
        )

        if not text:
            continue

        cls = (
            "ce-opt ce-opt-sel"
            if selected == letter
            else "ce-opt"
        )

        options_html += (
            f"<div class='{cls}'>"
            f"<span class='ce-letter'>"
            f"{letter}."
            "</span> "
            f"{esc(text)}"
            "</div>"
        )

    marked = (
        "<span class='ce-badge "
        "ce-badge-mark'>"
        "🔖 Marked"
        "</span>"
        if st["marked"][i]
        else ""
    )

    return (
        "<div class='ce-card'>"

        "<div class='ce-tags'>"

        f"<span class='ce-badge'>"
        f"{esc(q['subject'])}"
        "</span>"

        f"<span class='ce-badge'>"
        f"{esc(q['topic'])}"
        "</span>"

        f"<span class='ce-badge'>"
        f"{esc(q['difficulty'])}"
        "</span>"

        f"{marked}"

        "</div>"

        f"<div class='ce-q'>"
        f"{esc(q['question'])}"
        "</div>"

        f"{options_html}"

        "</div>"
    )


def palette_html(st):

    chips = ""

    for i in range(
        len(
            st["questions"]
        )
    ):

        if i == st["cur"]:

            cls = (
                "ce-chip "
                "ce-chip-cur"
            )

        elif st["marked"][i]:

            cls = (
                "ce-chip "
                "ce-chip-mark"
            )

        elif st["answers"][i]:

            cls = (
                "ce-chip "
                "ce-chip-ans"
            )

        else:

            cls = "ce-chip"

        chips += (
            f"<span class='{cls}'>"
            f"{i + 1}"
            "</span>"
        )

    return (
        "<div class='ce-pal'>"
        "<div class='ce-pal-t'>"
        "Question Palette"
        "</div>"
        f"{chips}"
        "<div class='ce-legend'>"
        "🟦 Current &nbsp; "
        "🟩 Answered &nbsp; "
        "🟧 Marked &nbsp; "
        "⬜ Not attempted"
        "</div>"
        "</div>"
    )


def summary_html(
    st,
    result
):

    meta = st.get(
        "meta",
        {}
    )

    auto = (
        "<div class='ce-warn'>"
        "⏰ Time khatam — "
        "test automatically submit ho gaya."
        "</div>"
        if st.get("auto")
        else ""
    )

    where = " › ".join(
        esc(
            meta.get(k, "")
        )
        for k in (
            "exam",
            "subject",
            "topic",
            "difficulty"
        )
        if meta.get(k)
    )

    return (
        "<div class='ce-card'>"

        f"{auto}"

        "<div class='ce-res-title'>"
        "🎯 TEST RESULT"
        "</div>"

        f"<div class='ce-sub'>"
        f"{where}"
        "</div>"

        f"<div class='ce-score'>"
        f"Score: {result['correct']} / "
        f"{result['total']}"
        "</div>"

        "<div class='ce-grid'>"

        "<div class='ce-stat ce-s-ok'>"
        "✅ Correct<br>"
        f"<b>{result['correct']}</b>"
        "</div>"

        "<div class='ce-stat ce-s-bad'>"
        "❌ Wrong<br>"
        f"<b>{result['wrong']}</b>"
        "</div>"

        "<div class='ce-stat ce-s-na'>"
        "⚪ Not Attempted<br>"
        f"<b>{result['na']}</b>"
        "</div>"

        "<div class='ce-stat ce-s-mk'>"
        "🔖 Marked<br>"
        f"<b>{result['marked']}</b>"
        "</div>"

        "</div>"

        f"<div class='ce-acc'>"
        f"Accuracy: <b>{result['accuracy']}%</b>"
        " <span class='ce-sub'>"
        "(correct ÷ attempted)"
        "</span>"
        "</div>"

        f"<div class='ce-sub'>"
        f"Time used: "
        f"{fmt_time(result['time_used'])}"
        " of "
        f"{fmt_time(st['duration'])}"
        "</div>"

        "</div>"
    )


def review_html(
    st,
    result
):

    output = [
        "<div class='ce-rev-head'>"
        "📋 Detailed Review"
        "</div>"
    ]

    for row in result[
        "rows"
    ]:

        q = st[
            "questions"
        ][row["idx"]]

        number = (
            row["idx"]
            + 1
        )

        if row[
            "status"
        ] == "correct":

            box = (
                "ce-rev "
                "ce-rev-ok"
            )

            verdict = "✅ Correct"

        elif row[
            "status"
        ] == "wrong":

            box = (
                "ce-rev "
                "ce-rev-bad"
            )

            verdict = "❌ Wrong"

        else:

            box = (
                "ce-rev "
                "ce-rev-na"
            )

            verdict = (
                "⚪ Not Attempted"
            )

        if row["your"]:

            your = (
    f"<b>{row['your']}</b>"
    " — "
    f"{esc(q['options'].get(row['your'], ''))}"
            )

        else:

            your = (
                "<b>—</b>"
                " (not attempted)"
            )

        correct = (
    f"<b>{q['correct']}</b>"
    " — "
    f"{esc(q['options'].get(q['correct'], ''))}"
)

        marked = (
            " <span class='ce-badge "
            "ce-badge-mark'>"
            "🔖 Marked"
            "</span>"
            if row["marked"]
            else ""
        )

        explanation = (
            q["explanation"].strip()
            if q["explanation"]
            else ""
        )

        explanation_html = (
            esc(explanation)
            if explanation
            else
            "Is question ki explanation "
            "database me available nahi hai."
        )

        output.append(
            "<div "
            f"class='{box}'>"

            f"<div class='ce-rev-q'>"
            f"Q{number}. "
            f"{esc(q['question'])}"
            f"{marked}"
            "</div>"

            f"<div class='ce-line'>"
            f"Your Answer: {your}"
            "</div>"

            f"<div class='ce-line'>"
            f"Correct Answer: {correct}"
            "</div>"

            f"<div class='ce-line'>"
            f"Result: <b>{verdict}</b>"
            "</div>"

            "<div class='ce-expl'>"
            "📖 <b>Explanation:</b><br>"
            f"{explanation_html}"
            "</div>"

            "</div>"
        )

    return "".join(
        output
    )


# ================================================================
# 15. CSS
# ================================================================

HEADER_HTML = (
    "<div class='ce-head'>"
    "<div class='ce-h1'>"
    "📝 ChetExam AI"
    "</div>"
    "<div class='ce-h2'>"
    "Free RAS Exam Practice Tests"
    "</div>"
    "</div>"
)


REGISTRATION_HEADER_HTML = (
    "<div class='ce-info'>"
    "👋 <b>Welcome to ChetExam AI</b><br>"
    "Test start karne se pehle basic details enter karein."
    "</div>"
)


CSS = """
:root,
body,
.gradio-container {
    --body-background-fill:#eef2f7;
    --body-text-color:#111827;
    --body-text-color-subdued:#334155;
    --background-fill-primary:#ffffff;
    --background-fill-secondary:#f1f5f9;
    --block-background-fill:#ffffff;
    --block-border-color:#cbd5e1;
    --border-color-primary:#cbd5e1;
    --input-background-fill:#ffffff;
    --input-border-color:#94a3b8;
    --input-text-color:#111827;
    color-scheme:light;
}

body,
.gradio-container {
    background:#eef2f7 !important;
    color:#111827 !important;
}

.gradio-container {
    max-width:760px !important;
    margin:0 auto !important;
    font-size:16px !important;
}

.gradio-container label span,
.gradio-container .block-title,
.gradio-container legend {
    color:#111827 !important;
}

.gradio-container input,
.gradio-container textarea,
.gradio-container select {
    color:#111827 !important;
    background:#ffffff !important;
}

.gradio-container button {
    font-size:16px !important;
    min-height:46px;
}

.gradio-container button.primary {
    background:#1d4ed8 !important;
    color:white !important;
}

.gradio-container button.secondary {
    background:#e2e8f0 !important;
    color:#111827 !important;
}

.gradio-container button.stop {
    background:#dc2626 !important;
    color:white !important;
}

#ans_radio label {
    background:#ffffff !important;
    color:#111827 !important;
    border:2px solid #94a3b8 !important;
    border-radius:12px !important;
    padding:12px 18px !important;
    font-size:20px !important;
    font-weight:700 !important;
    justify-content:center;
}

#ans_radio label:has(input:checked) {
    background:#dbeafe !important;
    border-color:#1d4ed8 !important;
    color:#1e3a8a !important;
}

#ans_radio .wrap {
    display:grid !important;
    grid-template-columns:repeat(4,1fr);
    gap:8px;
}

#nav_row,
#act_row,
#jump_row {
    flex-wrap:nowrap !important;
    gap:8px;
}

#timer_box {
    position:sticky;
    top:0;
    z-index:100;
}

.ce-head {
    text-align:center;
    padding:6px 0 2px;
}

.ce-h1 {
    font-size:28px;
    font-weight:800;
    color:#1e3a8a !important;
}

.ce-h2 {
    font-size:15px;
    color:#334155 !important;
}

.ce-info {
    background:#eff6ff;
    border:1px solid #93c5fd;
    border-radius:12px;
    padding:12px;
    color:#0f172a !important;
    line-height:1.6;
}

.ce-msg {
    background:#fef2f2;
    border:1px solid #f87171;
    border-radius:12px;
    padding:12px;
    color:#7f1d1d !important;
}

.ce-ok-msg {
    background:#ecfdf5;
    border:1px solid #34d399;
    border-radius:12px;
    padding:12px;
    color:#064e3b !important;
}

.ce-timer {
    background:#1e3a8a;
    color:white !important;
    text-align:center;
    font-size:20px;
    padding:10px;
    border-radius:12px;
}

.ce-timer-low {
    background:#b91c1c;
}

.ce-progress {
    background:white;
    border:1px solid #cbd5e1;
    border-radius:12px;
    padding:10px 14px;
}

.ce-qno {
    font-size:22px;
    font-weight:800;
    color:#111827 !important;
}

.ce-sub {
    font-size:14px;
    color:#334155 !important;
}

.ce-card {
    background:white;
    border:1px solid #cbd5e1;
    border-radius:14px;
    padding:14px;
    color:#111827 !important;
}

.ce-badge {
    display:inline-block;
    background:#e0e7ff;
    color:#1e3a8a !important;
    border-radius:999px;
    padding:2px 10px;
    font-size:13px;
    margin:0 6px 6px 0;
}

.ce-badge-mark {
    background:#ffedd5;
    color:#9a3412 !important;
}

.ce-q {
    font-size:19px;
    line-height:1.6;
    font-weight:600;
    color:#111827 !important;
    margin:6px 0 12px;
}

.ce-opt {
    background:#f8fafc;
    border:2px solid #cbd5e1;
    border-radius:12px;
    padding:12px;
    margin:8px 0;
    font-size:17px;
    line-height:1.5;
    color:#111827 !important;
}

.ce-opt-sel {
    background:#dbeafe;
    border-color:#1d4ed8;
}

.ce-letter {
    font-weight:800;
    color:#1d4ed8 !important;
}

.ce-pal {
    background:white;
    border:1px solid #cbd5e1;
    border-radius:12px;
    padding:10px;
}

.ce-pal-t {
    font-weight:700;
    margin-bottom:6px;
}

.ce-chip {
    display:inline-block;
    width:38px;
    height:38px;
    line-height:38px;
    text-align:center;
    border-radius:8px;
    background:#e2e8f0;
    color:#111827 !important;
    font-weight:700;
    margin:3px;
}

.ce-chip-ans {
    background:#bbf7d0;
    color:#14532d !important;
}

.ce-chip-mark {
    background:#fed7aa;
    color:#7c2d12 !important;
}

.ce-chip-cur {
    background:#1d4ed8;
    color:white !important;
}

.ce-legend {
    font-size:13px;
    color:#334155 !important;
    margin-top:6px;
}

.ce-warn {
    background:#fef3c7;
    border:1px solid #f59e0b;
    color:#78350f !important;
    border-radius:10px;
    padding:10px;
    margin-bottom:10px;
}

.ce-res-title {
    font-size:26px;
    font-weight:800;
    color:#1e3a8a !important;
}

.ce-score {
    font-size:34px;
    font-weight:800;
    color:#111827 !important;
    margin:8px 0;
}

.ce-grid {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px;
    margin:8px 0;
}

.ce-stat {
    border-radius:10px;
    padding:10px;
    text-align:center;
    font-size:15px;
}

.ce-stat b {
    font-size:24px;
}

.ce-s-ok {
    background:#dcfce7;
}

.ce-s-bad {
    background:#fee2e2;
}

.ce-s-na {
    background:#e5e7eb;
}

.ce-s-mk {
    background:#ffedd5;
}

.ce-acc {
    font-size:20px;
    color:#111827 !important;
    margin-top:6px;
}

.ce-rev-head {
    font-size:22px;
    font-weight:800;
    color:#1e3a8a !important;
    margin:8px 0;
}

.ce-rev {
    background:white;
    border:1px solid #cbd5e1;
    border-left:6px solid #94a3b8;
    border-radius:12px;
    padding:12px;
    margin:10px 0;
}

.ce-rev-ok {
    border-left-color:#16a34a;
    background:#f0fdf4;
}

.ce-rev-bad {
    border-left-color:#dc2626;
    background:#fef2f2;
}

.ce-rev-na {
    border-left-color:#6b7280;
    background:#f9fafb;
}

.ce-rev-q {
    font-size:17px;
    font-weight:700;
    line-height:1.55;
    margin-bottom:6px;
}

.ce-line {
    font-size:16px;
    line-height:1.6;
}

.ce-expl {
    background:#fffbeb;
    border-left:4px solid #f59e0b;
    border-radius:8px;
    padding:10px;
    margin-top:8px;
    font-size:16px;
    line-height:1.6;
}

@media(max-width:600px) {

    .gradio-container {
        padding:8px !important;
    }

    #ans_radio .wrap {
        grid-template-columns:repeat(2,1fr);
    }

    .ce-q {
        font-size:17px;
    }

    .ce-opt {
        font-size:16px;
    }

}
"""


# ================================================================
# 16. STATE / UI HELPERS
# ================================================================

def upd(**kwargs):
    """
    Gradio 6 compatible component update.
    """

    return gr.update(
        **kwargs
    )


def screen(mode):
    """
    Returns visibility updates for
    (register_col, setup_col, test_col, result_col, confirm_col)
    in that order.
    """

    values = {
        "register":
            (True, False, False, False, False),

        "setup":
            (False, True, False, False, False),

        "test":
            (False, False, True, False, False),

        "result":
            (False, False, False, True, False),
    }

    return tuple(
        upd(visible=x)
        for x in values[mode]
    )


def save_answer(
    state,
    choice
):

    if (
        state
        and state.get(
            "active"
        )
    ):

        state[
            "answers"
        ][
            state["cur"]
        ] = (
            choice
            if choice in LETTERS
            else ""
        )


def view_updates(
    state,
    set_radio=True
):

    i = state["cur"]

    radio_update = (
        upd(
            value=(
                state["answers"][i]
                or None
            )
        )
        if set_radio
        else upd()
    )

    return (
        timer_html(state),
        progress_html(state),
        question_html(state),
        radio_update,
        palette_html(state),
        upd(
            value=(
                "❌ Unmark"
                if state["marked"][i]
                else
                "🔖 Mark for Review"
            )
        ),
        upd(
            value=i + 1
        ),
    )


def nochange_view():

    return tuple(
        upd()
        for _ in range(7)
    )


# ================================================================
# 17. EVENT HANDLERS
# ================================================================

def on_register(
    name,
    mobile,
    share_count=0
):
    """
    Validates Name + Mobile, best-effort writes to the STUDENTS
    sheet, then reveals the existing Exam Setup screen. The browser
    enforces the one-time three-share gate before this handler is exposed.
    Outputs: (register_col, setup_col, reg_message, welcome_box)
    """

    try:
        share_count = int(share_count or 0)
    except Exception:
        share_count = 0

    if share_count < REQUIRED_WHATSAPP_SHARES:
        return (
            upd(),
            upd(),
            (
                "<div class='ce-msg'>"
                "🔒 Pehle 3 WhatsApp shares complete karein."
                "</div>"
            ),
            upd()
        )

    clean_name, clean_mobile, error = validate_student(
        name,
        mobile
    )

    if error:

        return (
            upd(),
            upd(),
            (
                "<div class='ce-msg'>"
                f"{error}"
                "</div>"
            ),
            upd()
        )

    register_student(
        clean_name,
        clean_mobile,
        False
    )

    welcome = (
        "<div class='ce-info'>"
        f"Welcome {esc(clean_name)} 👋<br>"
        "Ab test start kar sakte ho."
        "</div>"
    )

    return (
        upd(visible=False),
        upd(visible=True),
        "",
        welcome
    )


def on_selector_change(
    exam,
    subject,
    topic,
    difficulty
):

    s = build_selectors(
        exam,
        subject,
        topic,
        difficulty
    )

    return (
        upd(
            choices=s["subject"][0],
            value=s["subject"][1]
        ),
        upd(
            choices=s["topic"][0],
            value=s["topic"][1]
        ),
        upd(
            choices=s["diff"][0],
            value=s["diff"][1]
        ),
        s["info"],
    )


def start_test(
    old_state,
    exam,
    subject,
    topic,
    difficulty,
    count
):

    questions, meta, message = (
        generate_test(
            exam,
            subject,
            topic,
            difficulty,
            count
        )
    )

    if questions is None:

        return (
            old_state,
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            (
                "<div class='ce-msg'>"
                f"{message}"
                "</div>"
            ),
            upd(),
            upd(),
            *nochange_view()
        )

    state = new_test_state(
        questions,
        meta
    )

    screens = screen(
        "test"
    )

    return (
        state,
        screens[0],
        screens[1],
        screens[2],
        screens[3],
        screens[4],
        "",
        "",
        gr.Timer(
            active=True
        ),
        *view_updates(
            state
        )
    )


def answer_changed(
    state,
    choice
):

    if not state or not state.get(
        "active"
    ):

        return (
            state,
            upd(),
            upd(),
            upd()
        )

    save_answer(
        state,
        choice
    )

    return (
        state,
        progress_html(state),
        question_html(state),
        palette_html(state)
    )


def previous_question(
    state,
    choice
):

    if not state or not state.get(
        "active"
    ):

        return (
            state,
            *nochange_view()
        )

    save_answer(
        state,
        choice
    )

    state["cur"] = max(
        0,
        state["cur"] - 1
    )

    return (
        state,
        *view_updates(
            state
        )
    )


def next_question(
    state,
    choice
):

    if not state or not state.get(
        "active"
    ):

        return (
            state,
            *nochange_view()
        )

    save_answer(
        state,
        choice
    )

    state["cur"] = min(
        len(
            state["questions"]
        ) - 1,
        state["cur"] + 1
    )

    return (
        state,
        *view_updates(
            state
        )
    )


def jump_question(
    state,
    choice,
    number
):

    if not state or not state.get(
        "active"
    ):

        return (
            state,
            *nochange_view()
        )

    save_answer(
        state,
        choice
    )

    try:

        number = int(
            float(number)
        )

    except Exception:

        number = (
            state["cur"]
            + 1
        )

    number = min(
        max(
            1,
            number
        ),
        len(
            state["questions"]
        )
    )

    state["cur"] = (
        number - 1
    )

    return (
        state,
        *view_updates(
            state
        )
    )


def mark_question(
    state,
    choice
):

    if not state or not state.get(
        "active"
    ):

        return (
            state,
            *nochange_view()
        )

    save_answer(
        state,
        choice
    )

    i = state[
        "cur"
    ]

    state["marked"][i] = not (
        state["marked"][i]
    )

    return (
        state,
        *view_updates(
            state,
            set_radio=False
        )
    )


def clear_answer(
    state
):

    if not state or not state.get(
        "active"
    ):

        return (
            state,
            *nochange_view()
        )

    state[
        "answers"
    ][
        state["cur"]
    ] = ""

    return (
        state,
        *view_updates(
            state
        )
    )


def ask_submit(
    state,
    choice
):

    if not state or not state.get(
        "active"
    ):

        return (
            state,
            upd(),
            upd(),
            upd(),
            upd(),
            upd()
        )

    save_answer(
        state,
        choice
    )

    total = len(
        state["questions"]
    )

    answered = sum(
        bool(x)
        for x in state["answers"]
    )

    marked = sum(
        state["marked"]
    )

    message = (
        "<div class='ce-warn'>"
        "Submit karna hai?<br>"
        f"Answered: <b>{answered}</b>"
        f" / {total}"
        " &nbsp;·&nbsp; "
        f"Not attempted: "
        f"<b>{total - answered}</b>"
        " &nbsp;·&nbsp; "
        f"Marked: <b>{marked}</b><br>"
        "Submit ke baad answers "
        "change nahi ho sakte."
        "</div>"
    )

    return (
        state,
        upd(
            visible=True
        ),
        message,
        progress_html(state),
        palette_html(state),
        question_html(state)
    )


def cancel_submit():

    return upd(
        visible=False
    )


def finish_test(
    state
):

    if not state:
        return state

    if not state.get(
        "result"
    ):

        submit_test(
            state
        )

    screens = screen(
        "result"
    )

    return (
        state,
        screens[0],
        screens[1],
        screens[2],
        screens[3],
        screens[4],
        summary_html(
            state,
            state["result"]
        ),
        review_html(
            state,
            state["result"]
        ),
        "",
        gr.Timer(
            active=False
        )
    )


def confirm_submit(
    state,
    choice
):

    if (
        not state
        or not state.get(
            "questions"
        )
    ):

        return (
            state,
            *[
                upd()
                for _ in range(9)
            ]
        )

    if state.get(
        "active"
    ):

        save_answer(
            state,
            choice
        )

        submit_test(
            state
        )

    return finish_test(
        state
    )


def timer_tick(
    state,
    choice
):

    if not state or not state.get(
        "active"
    ):

        return (
            timer_html(state) if state else "",
            state,
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(
                active=False
            )
        )

    remaining = (
        state["end_ts"]
        - time.time()
    )

    if remaining > 0:

        return (
            timer_html(state),
            state,
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(
                active=True
            )
        )

    save_answer(
        state,
        choice
    )

    state["auto"] = True

    submit_test(
        state
    )

    finished = finish_test(
        state
    )

    return (
        timer_html(state),
        *finished
    )

def new_test():

    screens = screen(
        "setup"
    )

    return (
        None,
        screens[0],
        screens[1],
        screens[2],
        screens[3],
        screens[4],
        "",
        "",
        gr.Timer(
            active=False
        )
    )


def retest(
    old_state,
    include_not_attempted
):

    questions = retry_wrong(
        old_state,
        include_not_attempted
    )

    if not questions:

        what = (
            "galat ya un-attempted"
            if include_not_attempted
            else "galat"
        )

        return (
            old_state,
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            upd(),
            (
                "<div class='ce-ok-msg'>"
                f"🎉 Koi {what} question "
                "nahi hai."
                "</div>"
            ),
            upd(),
            *nochange_view()
        )

    meta = dict(
        (
            old_state or {}
        ).get(
            "meta",
            {}
        )
    )

    state = new_test_state(
        questions,
        meta,
        "Retest"
    )

    screens = screen(
        "test"
    )

    return (
        state,
        screens[0],
        screens[1],
        screens[2],
        screens[3],
        screens[4],
        "",
        "",
        gr.Timer(
            active=True
        ),
        *view_updates(
            state
        )
    )


# ================================================================
# 18. BUILD APP
# ================================================================

def build_app():

    selectors = build_selectors()

    with gr.Blocks(
        title="ChetExam AI"
    ) as demo:

        gr.HTML(
            value=f"<style>{CSS}</style>"
        )

        state = gr.State(
            value=None
        )

        gr.HTML(
            value=HEADER_HTML
        )

        # --------------------------------------------------------
        # STUDENT REGISTRATION (shown first, once per session)
        # --------------------------------------------------------

        with gr.Column(
            visible=True
        ) as reg_col:

            gr.HTML(
                value=REGISTRATION_HEADER_HTML
            )

            reg_name = gr.Textbox(
                label="👤 Name",
                placeholder="Apna naam likhein",
                interactive=True,
                elem_id="ce-reg-name"
            )

            reg_mobile = gr.Textbox(
                label="📱 Mobile Number",
                placeholder="10 digit mobile number",
                interactive=True,
                elem_id="ce-reg-mobile"
            )


            whatsapp_share_count = gr.State(0)

            share_gate = gr.HTML(
                value=f"""
                <div style="margin-top:14px;padding:16px;border:1px solid #d9d9e3;border-radius:14px;background:#fafafa;">
                  <div style="font-size:18px;font-weight:700;margin-bottom:6px;">📲 Pehle 3 WhatsApp Shares</div>
                  <div style="font-size:14px;line-height:1.5;">
                    Test access unlock karne ke liye is ChetExam link ko WhatsApp par <b>3 baar share</b> karein.
                  </div>
                </div>
                """
            )

            share_button = gr.Button(
                "🟢 Share on WhatsApp (0/3)",
                variant="secondary",
                size="lg",
                elem_id="ce-wa-share"
            )

            share_count_box = gr.Markdown(
                "**Shares completed: 0 / 3**\n\nWhatsApp share screen par **Send** dabane ke baad next share karein."
            )

            reg_button = gr.Button(
                "➡️ CONTINUE TO TEST",
                variant="primary",
                size="lg",
                interactive=False,
                elem_id="ce-register-button"
            )

            reg_message = gr.HTML(
                value=""
            )

        # --------------------------------------------------------
        # SETUP
        # --------------------------------------------------------

        with gr.Column(
            visible=False
        ) as setup_col:

            welcome_box = gr.HTML(
                value=""
            )

            exam_dd = gr.Dropdown(
                choices=selectors[
                    "exam"
                ][0],
                value=selectors[
                    "exam"
                ][1],
                label="1️⃣ Exam",
                filterable=False,
                interactive=True
            )

            subject_dd = gr.Dropdown(
                choices=selectors[
                    "subject"
                ][0],
                value=selectors[
                    "subject"
                ][1],
                label="2️⃣ Subject",
                filterable=False,
                interactive=True
            )

            topic_dd = gr.Dropdown(
                choices=selectors[
                    "topic"
                ][0],
                value=selectors[
                    "topic"
                ][1],
                label="3️⃣ Topic",
                filterable=False,
                interactive=True
            )

            difficulty_dd = gr.Dropdown(
                choices=selectors[
                    "diff"
                ][0],
                value=selectors[
                    "diff"
                ][1],
                label="4️⃣ Difficulty",
                filterable=False,
                interactive=True
            )

            count_radio = gr.Radio(
                choices=COUNT_CHOICES,
                value=COUNT_CHOICES[0],
                label=(
                    "5️⃣ Number of Questions "
                    "(1 minute per question)"
                ),
                interactive=True
            )

            info_html = gr.HTML(
                value=selectors[
                    "info"
                ]
            )

            start_button = gr.Button(
                "🚀 START TEST",
                variant="primary",
                size="lg"
            )

            setup_message = gr.HTML(
                value=""
            )

        # --------------------------------------------------------
        # TEST
        # --------------------------------------------------------

        with gr.Column(
            visible=False
        ) as test_col:

            timer_box = gr.HTML(
                value="",
                elem_id="timer_box"
            )

            progress_box = gr.HTML(
                value=""
            )

            question_box = gr.HTML(
                value=""
            )

            answer_radio = gr.Radio(
                choices=LETTERS,
                value=None,
                label="Your Answer",
                elem_id="ans_radio",
                interactive=True
            )

            with gr.Row(
                elem_id="act_row"
            ):

                clear_button = gr.Button(
                    "🧹 Clear",
                    variant="secondary"
                )

                mark_button = gr.Button(
                    "🔖 Mark for Review",
                    variant="secondary"
                )

            with gr.Row(
                elem_id="nav_row"
            ):

                previous_button = gr.Button(
                    "⬅ Previous",
                    variant="secondary"
                )

                next_button = gr.Button(
                    "Next ➡",
                    variant="primary"
                )

            with gr.Row(
                elem_id="jump_row"
            ):

                jump_number = gr.Number(
                    label="Jump to question no.",
                    value=1,
                    precision=0,
                    minimum=1
                )

                jump_button = gr.Button(
                    "Go",
                    variant="secondary"
                )

            palette_box = gr.HTML(
                value=""
            )

            submit_button = gr.Button(
                "📤 Submit Test",
                variant="stop",
                size="lg"
            )

            with gr.Column(
                visible=False
            ) as confirm_col:

                confirm_html = gr.HTML(
                    value=""
                )

                with gr.Row():

                    yes_button = gr.Button(
                        "✅ Yes, Submit",
                        variant="stop"
                    )

                    no_button = gr.Button(
                        "↩ Wapas jao",
                        variant="secondary"
                    )

        # --------------------------------------------------------
        # RESULT
        # --------------------------------------------------------

        with gr.Column(
            visible=False
        ) as result_col:

            summary_box = gr.HTML(
                value=""
            )

            retest_message = gr.HTML(
                value=""
            )

            retest_wrong_button = gr.Button(
                "🔄 RETEST WRONG QUESTIONS",
                variant="primary",
                size="lg"
            )

            retest_all_button = gr.Button(
                "🔄 RETEST WRONG + NOT ATTEMPTED",
                variant="secondary"
            )

            new_test_button = gr.Button(
                "🏠 New Test",
                variant="secondary"
            )

            review_box = gr.HTML(
                value=""
            )

        timer = gr.Timer(
            value=1,
            active=False
        )

        # --------------------------------------------------------
        # OUTPUT GROUPS
        # --------------------------------------------------------

        view_outputs = [
            timer_box,
            progress_box,
            question_box,
            answer_radio,
            palette_box,
            mark_button,
            jump_number,
        ]

        screen_outputs = [
            state,
            reg_col,
            setup_col,
            test_col,
            result_col,
            confirm_col,
            setup_message,
            retest_message,
            timer,
        ]

        start_outputs = (
            screen_outputs
            + view_outputs
        )

        nav_outputs = [
            state
        ] + view_outputs

        finish_outputs = [
            state,
            reg_col,
            setup_col,
            test_col,
            result_col,
            confirm_col,
            summary_box,
            review_box,
            retest_message,
            timer,
        ]

        # --------------------------------------------------------
        # ONE-TIME BROWSER ACCESS RESTORE
        # --------------------------------------------------------
        # After a successful first registration, keep the student's name/mobile
        # locally so the same browser can return directly to Exam Setup.
        def restore_saved_student():
            return None

        demo.load(
            fn=restore_saved_student,
            inputs=[],
            outputs=[],
            js="""
            () => {
              try {
                const done = localStorage.getItem('chetexam_registered_v1');
                const name = localStorage.getItem('chetexam_name_v1') || '';
                const mobile = localStorage.getItem('chetexam_mobile_v1') || '';
                const shares = parseInt(localStorage.getItem('chetexam_whatsapp_share_count_v2') || '0', 10) || 0;
                if (done === '1' && name && mobile && shares >= 3) {
                  const setVal = (id, value) => {
                    const root = document.getElementById(id);
                    const input = root && root.querySelector('input, textarea');
                    if (!input) return;
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                    if (setter) setter.call(input, value); else input.value = value;
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                  };
                  setTimeout(() => {
                    setVal('ce-reg-name', name);
                    setVal('ce-reg-mobile', mobile);
                    const b = document.querySelector('#ce-register-button button');
                    if (b) { b.disabled = false; b.click(); }
                  }, 700);
                }
              } catch (e) {}
              return [];
            }
            """
        )

        # --------------------------------------------------------
        # WHATSAPP SHARE GATE - RELIABLE IMPLEMENTATION
        # --------------------------------------------------------

        def increment_share_count(current):
            try:
                c = int(current or 0)
            except Exception:
                c = 0
            c = min(REQUIRED_WHATSAPP_SHARES, c + 1)
            
            btn_text = f"✅ {REQUIRED_WHATSAPP_SHARES} Shares Complete" if c >= REQUIRED_WHATSAPP_SHARES else f"🟢 Share on WhatsApp ({c}/{REQUIRED_WHATSAPP_SHARES})"
            md_text = f"**Shares completed: {c} / {REQUIRED_WHATSAPP_SHARES}**\n\nWhatsApp share screen par **Send** dabane ke baad wapas website par aayein."
            
            if c >= REQUIRED_WHATSAPP_SHARES:
                md_text = f"**Shares completed: {c} / {REQUIRED_WHATSAPP_SHARES}**\n\n✅ Ab **CONTINUE TO TEST** dabakar test start karein."
                
            return c, upd(value=btn_text), upd(value=md_text), upd(interactive=(c >= REQUIRED_WHATSAPP_SHARES))

        share_return_sync = gr.Button("sync", visible=False, elem_id="ce-wa-return-sync")

        share_button.click(
            fn=None,
            inputs=[],
            outputs=[],
            js="""
            () => {
                const count = parseInt(localStorage.getItem('chetexam_whatsapp_share_count_v2') || '0', 10);
                if (count >= 3) return [];
                
                sessionStorage.setItem('ce_wa_pending', '1');
                
                const url = window.location.href.split('#')[0];
                const text = encodeURIComponent('ChetExam AI par free exam test dein 👇\n' + url);
                window.open('https://wa.me/?text=' + text, '_blank');
                return [];
            }
            """
        )

        sync_event = share_return_sync.click(
            fn=increment_share_count,
            inputs=[whatsapp_share_count],
            outputs=[whatsapp_share_count, share_button, share_count_box, reg_button]
        )

        sync_event.then(
            fn=None,
            inputs=[whatsapp_share_count],
            outputs=[],
            js="""
            (c) => { 
                localStorage.setItem('chetexam_whatsapp_share_count_v2', String(c)); 
                return []; 
            }
            """
        )

        demo.load(
            fn=None,
            inputs=[],
            outputs=[],
            js="""
            () => {
                if (window.__ceWaInit) return [];
                window.__ceWaInit = true;
                
                const checkReturn = () => {
                    if (document.visibilityState !== 'visible') return;
                    if (sessionStorage.getItem('ce_wa_pending') === '1') {
                        // Immediately clear to prevent multiple triggers
                        sessionStorage.setItem('ce_wa_pending', '0');
                        
                        // Find and click the hidden sync button robustly
                        const wrapper = document.getElementById('ce-wa-return-sync');
                        if (wrapper) {
                            const btn = wrapper.tagName.toLowerCase() === 'button' ? wrapper : wrapper.querySelector('button');
                            if (btn) btn.click();
                        }
                    }
                };
                
                document.addEventListener('visibilitychange', checkReturn);
                window.addEventListener('focus', checkReturn);
                setInterval(checkReturn, 1000);
                
                return [];
            }
            """
        )

        # --------------------------------------------------------
        # REGISTRATION
        # --------------------------------------------------------
