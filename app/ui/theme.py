"""Shared look of the three pages (customer chat, staff review, developer debug).

Green theme and CSS from 01_Input/resolveright_gui_style_2.md: green for the customer parts, amber
for escalations, slate for the developer parts. Gradio 6 takes `theme` and `css` when an app is
mounted (app/server.py), so every page is mounted with the same two values.
"""

import json

import gradio as gr

THEME = gr.themes.Soft(
    primary_hue="emerald",
    secondary_hue="green",
    neutral_hue="slate",
    radius_size=gr.themes.sizes.radius_lg,
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "sans-serif"],
).set(
    body_background_fill="#F3FAF5",
    body_background_fill_dark="#0F172A",
    block_border_color="#CFE8D5",
    block_border_color_dark="#2F5D3A",
    button_primary_background_fill="#2E7D32",
    button_primary_background_fill_hover="#256628",
    button_primary_text_color="white",
)

CSS = """
.app-header {
  background: linear-gradient(90deg, #1B5E20, #2E7D32);
  color: #fff; padding: 16px 20px; border-radius: 12px; margin-bottom: 8px;
}
.app-header h1 { color: #fff; margin: 0; font-size: 1.5rem; }
.app-header p  { color: #E8F5E9; margin: 4px 0 0; font-size: 0.9rem; }
#chat { border: 1px solid #CFE8D5; border-radius: 12px; }
#chat .message.user { background: #DCEFE0; border: 1px solid #B7DDBF; }
#chat .message.bot  { background: #FFFFFF; border: 1px solid #CFE8D5; }
/* Gradio rewrites every rule with a long prefix, and only the FIRST selector of a comma list in a
   ".dark ..." rule stays usable. So the dark colours are variables set by one single-selector
   rule (.dark #msg-box below) and these light rules read them with a light fallback. */
#msg-box, #msg-box label, #msg-box textarea {
  background: var(--msg-bg, #DCEFE0) !important; border-color: var(--msg-border, #B7DDBF) !important;
}
#msg-box { border: 1px solid var(--msg-border, #B7DDBF) !important; border-radius: 12px; }
#msg-box textarea { border: none !important; box-shadow: none !important; }
/* dotted divider used above every "for testing only" section */
.test-section { border-top: 2px dotted #94A3B8; margin-top: 16px; padding-top: 8px; }
/* open-requests line (customer page): amber, one compact line */
.open-line {
  background: #FFF4E0; color: #5B3A00; border-left: 4px solid #F5B041;
  padding: 6px 12px; border-radius: 8px; font-size: 0.9rem;
}
.dark .open-line { background: #4A2E0A; color: #FFE8C2; }
/* My requests list: scrolls instead of pushing the message box down */
.my-requests { max-height: 220px; overflow-y: auto; }
.my-requests .row {
  display: flex; justify-content: space-between; gap: 12px; align-items: baseline;
  padding: 6px 0; border-bottom: 1px solid #CFE8D5; font-size: 0.9rem;
}
.dark .my-requests .row { border-bottom-color: #2F5D3A; }
.my-requests .done { color: #2E7D32; }
.dark .my-requests .done { color: #A5D6A7; }
.my-requests .open { color: #5B3A00; }
.dark .my-requests .open { color: #FFE8C2; }
.my-requests .code { font-size: 0.7rem; color: #64748B; }
.dark .my-requests .code { color: #94A3B8; }
.dark .footer-note { color: #94A3B8; }
.amber-note { background: #FFF4E0; border: 1px solid #F5B041; border-radius: 10px; padding: 8px 12px; }
.dev-code, .dev-code * { font-family: ui-monospace, Consolas, monospace; }
.dev-block { background: #F1F5F9; border: 1px solid #94A3B8; border-radius: 8px; }
.footer-note { color: #64748B; font-size: 0.8rem; }
.dark .test-section { border-top-color: #64748B; }
.dark .dev-block { background: #1E293B; border-color: #64748B; }
.dark #msg-box { --msg-bg: #1B4D2B; --msg-border: #2E7D32; }
/* typed text in the box left of Send: light on dark green, with a visible caret and placeholder */
.dark #msg-box textarea {
  color: #F1F5F9 !important; -webkit-text-fill-color: #F1F5F9 !important; caret-color: #F1F5F9;
}
.dark #msg-box textarea::placeholder {
  color: #CBD5E1 !important; -webkit-text-fill-color: #CBD5E1 !important; opacity: 1;
}
.dark #chat .message.user { background: #1B4D2B; border-color: #2E7D32; }
.dark #chat .message.bot { background: #1E293B; border-color: #2F5D3A; }
.dark .amber-note { background: #78350F; border-color: #B45309; }
"""


# All three pages open in dark mode instead of following the browser setting. Gradio 6 puts the
# `js` argument of mount_gradio_app into a <script> tag, so this must be a statement that runs, not
# a function that is only defined (that version never called it). Gradio's own theme code removes
# the `dark` class when the system theme is light or changes, so a small observer puts it back.
FORCE_DARK_JS = (
    "(() => {"
    " const b = document.body;"
    " const dark = () => { if (!b.classList.contains('dark')) b.classList.add('dark'); };"
    " dark();"
    " new MutationObserver(dark).observe(b, { attributes: true, attributeFilter: ['class'] });"
    " })();"
)


def title_js(title: str) -> str:
    """Script that sets the browser tab title.

    A page mounted on FastAPI (app/server.py) is served without a <title> tag, so the tab showed
    the address ("127.0.0.1:8000/ui/") instead of the title given to gr.Blocks. A small observer
    sets it and puts it back if something changes it.

    Args:
        title: Text for the browser tab.
    """
    return (
        "(() => {"
        f" const t = {json.dumps(title)};"
        " const set = () => { if (document.title !== t) document.title = t; };"
        " set();"
        " new MutationObserver(set).observe(document.head, "
        "{ childList: true, subtree: true, characterData: true });"
        " })();"
    )


def look(title: str) -> dict:
    """Theme, CSS and scripts shared by the three pages, with the tab title of this page.

    Args:
        title: The page's tab title (the same text given to gr.Blocks).
    """
    return {"theme": THEME, "css": CSS, "js": FORCE_DARK_JS + " " + title_js(title)}


def header(title: str, subtitle: str) -> str:
    """HTML of the green header banner shared by all three pages.

    Args:
        title: Page title, shown with its emoji.
        subtitle: One line under the title.
    """
    return f'<div class="app-header"><h1>{title}</h1><p>{subtitle}</p></div>'
