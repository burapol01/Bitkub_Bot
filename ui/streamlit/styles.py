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


def render_hero() -> None:
    st.markdown(
        """
        <div class="hero">
          <div class="hero-kicker">Bitkub Bot Control Surface</div>
          <div class="hero-title">Live operations, diagnostics, and reports in one place</div>
          <p class="hero-sub">This dashboard sits on top of the current console bot and reuses the same SQLite, private API, and execution services.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
