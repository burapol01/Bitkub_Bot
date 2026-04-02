from __future__ import annotations

import streamlit as st


CUSTOM_CSS = """
<style>
:root {
  --bg-0: #f3ecdf;
  --bg-1: #fffaf2;
  --panel: rgba(255, 251, 243, 0.86);
  --panel-strong: rgba(253, 247, 234, 0.96);
  --line: rgba(104, 79, 46, 0.16);
  --ink: #2b2117;
  --muted: #7b6850;
  --accent: #a34a28;
  --accent-2: #1f6a73;
  --good: #2f7d32;
  --warn: #9a6700;
  --bad: #b42318;
}

.stApp {
  background:
    radial-gradient(circle at 12% 18%, rgba(163, 74, 40, 0.14), transparent 24%),
    radial-gradient(circle at 82% 14%, rgba(31, 106, 115, 0.14), transparent 20%),
    linear-gradient(180deg, var(--bg-0), var(--bg-1));
  color: var(--ink);
}

[data-testid="stAppViewContainer"] > .main .block-container {
  max-width: min(1920px, calc(100vw - 8rem));
  padding-top: 1.2rem;
  padding-left: 1.1rem;
  padding-right: 1.1rem;
}

@media (max-width: 1200px) {
  [data-testid="stAppViewContainer"] > .main .block-container {
    max-width: 100%;
    padding-left: 1rem;
    padding-right: 1rem;
  }
}

html, body, [class*="css"] {
  font-family: "Palatino Linotype", "Book Antiqua", Georgia, serif;
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(251,245,233,0.98), rgba(245,236,217,0.98));
  border-right: 1px solid var(--line);
}

.hero {
  background: linear-gradient(135deg, rgba(255,249,240,0.94), rgba(247,235,215,0.88));
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 1.2rem 1.4rem 1rem 1.4rem;
  box-shadow: 0 14px 42px rgba(79, 56, 24, 0.08);
}

.hero-kicker {
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--accent);
  font-size: 0.74rem;
  margin-bottom: 0.55rem;
}

.hero-title {
  font-size: 2rem;
  line-height: 1.05;
  font-weight: 700;
  margin: 0 0 0.4rem 0;
  color: var(--ink);
}

.hero-sub {
  color: var(--muted);
  margin: 0;
  font-size: 1rem;
}

.hero-meta {
  margin-top: 0.75rem;
}

.hero-meta-sub {
  margin-top: 0.35rem;
  color: var(--muted);
  font-size: 0.9rem;
}

.metric-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 22px;
  padding: 1rem 1.05rem 0.9rem 1.05rem;
  min-height: 110px;
  box-shadow: 0 12px 34px rgba(66, 47, 24, 0.06);
}

.metric-label {
  font-size: 0.76rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--muted);
}

.metric-value {
  font-size: 1.7rem;
  line-height: 1.05;
  margin-top: 0.35rem;
  color: var(--ink);
}

.metric-note {
  margin-top: 0.45rem;
  color: var(--muted);
  font-size: 0.92rem;
}

.panel-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 0.5rem;
}

.section-shell {
  background: var(--panel-strong);
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 1rem 1.05rem 0.95rem 1.05rem;
  box-shadow: 0 12px 34px rgba(66, 47, 24, 0.05);
  margin-top: 0.35rem;
  margin-bottom: 1rem;
}

.section-kicker {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.16em;
  color: var(--accent);
  margin-bottom: 0.25rem;
}

.section-title {
  font-size: 1.12rem;
  font-weight: 700;
  color: var(--ink);
  margin: 0;
}

.section-sub {
  color: var(--muted);
  font-size: 0.94rem;
  margin-top: 0.28rem;
}

.callout {
  border-radius: 18px;
  border: 1px solid var(--line);
  padding: 0.82rem 0.95rem;
  margin: 0.45rem 0 0.8rem 0;
}

.callout-title {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  margin-bottom: 0.28rem;
}

.callout-body {
  font-size: 0.96rem;
  line-height: 1.42;
}

.callout.good { background: rgba(47,125,50,0.10); color: var(--good); border-color: rgba(47,125,50,0.20); }
.callout.warn { background: rgba(154,103,0,0.10); color: var(--warn); border-color: rgba(154,103,0,0.22); }
.callout.bad  { background: rgba(180,35,24,0.10); color: var(--bad); border-color: rgba(180,35,24,0.22); }
.callout.info { background: rgba(31,106,115,0.10); color: var(--accent-2); border-color: rgba(31,106,115,0.22); }

.status-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0.42rem;
  margin: 0.2rem 0 0.75rem 0;
}

.nav-shell {
  background: rgba(255, 250, 241, 0.84);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 0.85rem 0.9rem;
  margin-bottom: 0.9rem;
}

.nav-title {
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--muted);
  margin-bottom: 0.42rem;
}

.nav-body {
  color: var(--ink);
  font-size: 0.95rem;
  line-height: 1.4;
}

.page-gap {
  height: 0.25rem;
}

[data-testid="stSidebar"] .element-container,
[data-testid="stSidebar"] .stButton,
[data-testid="stSidebar"] .stRadio,
[data-testid="stSidebar"] .stCaption {
  margin-bottom: 0.45rem;
}

[data-testid="stSidebar"] .stRadio > div {
  gap: 0.3rem;
}

[data-testid="stSidebar"] .stRadio label {
  border-radius: 14px;
}

.main .block-container [data-testid="stHorizontalBlock"] {
  gap: 0.9rem;
}

.main .block-container .element-container,
.main .block-container .stDataFrame,
.main .block-container .stAlert,
.main .block-container .stMarkdown,
.main .block-container .stCaption,
.main .block-container .stExpander,
.main .block-container .stForm {
  margin-bottom: 0.7rem;
}

.main .block-container .stDataFrame {
  padding-top: 0.1rem;
}

.main .block-container .stForm {
  border: 1px solid var(--line);
  border-radius: 20px;
  background: rgba(255, 251, 243, 0.72);
  padding: 0.8rem 0.85rem 0.45rem 0.85rem;
}

.main .block-container hr {
  margin: 1rem 0;
}

.note-strip {
  border-left: 4px solid var(--accent);
  background: rgba(163, 74, 40, 0.08);
  border-radius: 0 16px 16px 0;
  padding: 0.8rem 0.9rem;
  color: var(--ink);
}

.badge {
  display: inline-block;
  padding: 0.18rem 0.56rem;
  border-radius: 999px;
  font-size: 0.78rem;
  margin-right: 0.35rem;
  border: 1px solid transparent;
}

.badge.good { background: rgba(47,125,50,0.12); color: var(--good); border-color: rgba(47,125,50,0.22); }
.badge.warn { background: rgba(154,103,0,0.12); color: var(--warn); border-color: rgba(154,103,0,0.22); }
.badge.bad  { background: rgba(180,35,24,0.12); color: var(--bad); border-color: rgba(180,35,24,0.22); }
.badge.info { background: rgba(31,106,115,0.12); color: var(--accent-2); border-color: rgba(31,106,115,0.22); }
</style>
"""


def inject_css() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def badge(text: str, tone: str = "info") -> str:
    return f'<span class="badge {tone}">{text}</span>'


def render_metric_card(label: str, value: str, note: str = "") -> None:
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">{label}</div>
          <div class="metric-value">{value}</div>
          <div class="metric-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_intro(title: str, subtitle: str = "", kicker: str = "") -> None:
    kicker_html = f'<div class="section-kicker">{kicker}</div>' if kicker else ""
    subtitle_html = f'<div class="section-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="section-shell">
          {kicker_html}
          <div class="section-title">{title}</div>
          {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_callout(title: str, message: str, tone: str = "info") -> None:
    st.markdown(
        f"""
        <div class="callout {tone}">
          <div class="callout-title">{title}</div>
          <div class="callout-body">{message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_block(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="nav-shell">
          <div class="nav-title">{title}</div>
          <div class="nav-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_hero(*, version_label: str = "", version_detail: str = "") -> None:
    version_html = ""
    if version_label or version_detail:
        detail_html = (
            f'<div class="hero-meta-sub">{version_detail}</div>'
            if version_detail
            else ""
        )
        version_html = (
            '<div class="hero-meta">'
            f'{badge(f"Version {version_label}", "info")}'
            f"{detail_html}"
            "</div>"
        )

    st.markdown(
        f"""
        <div class="hero">
          <div class="hero-kicker">Bitkub Bot Control Surface</div>
          <div class="hero-title">Live operations, diagnostics, and reports in one place</div>
          <p class="hero-sub">This dashboard sits on top of the current console bot and reuses the same SQLite, private API, and execution services.</p>
          {version_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
