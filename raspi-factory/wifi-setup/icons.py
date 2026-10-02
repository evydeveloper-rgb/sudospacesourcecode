"""Inline SVG replacements for the Material Symbols spans.

The webfont Google serves for Material Symbols carries no ligature
substitutions, so <span class="material-symbols-outlined">wifi</span> never
becomes an icon — it renders the literal word in the icon face, which is the
garbled overlap seen on device. Inline SVG has no load step and no failure
mode that shows text.

Each path uses currentColor so the existing Tailwind text-* classes still
tint it, and 1em sizing so text-xl / text-2xl still scale it.
"""

ICONS = {
    "wifi": '<path d="M5 12.5a11 11 0 0 1 14 0"/><path d="M8.5 16a6 6 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1.1" fill="currentColor" stroke="none"/>',
    "signal_wifi_4_bar": '<path d="M2.5 9.5a15 15 0 0 1 19 0"/><path d="M5.5 12.8a11 11 0 0 1 13 0"/><path d="M8.5 16a6 6 0 0 1 7 0"/><circle cx="12" cy="19.4" r="1.1" fill="currentColor" stroke="none"/>',
    "signal_wifi_3_bar": '<path d="M2.5 9.5a15 15 0 0 1 19 0" opacity=".25"/><path d="M5.5 12.8a11 11 0 0 1 13 0"/><path d="M8.5 16a6 6 0 0 1 7 0"/><circle cx="12" cy="19.4" r="1.1" fill="currentColor" stroke="none"/>',
    "signal_wifi_2_bar": '<path d="M2.5 9.5a15 15 0 0 1 19 0" opacity=".25"/><path d="M5.5 12.8a11 11 0 0 1 13 0" opacity=".25"/><path d="M8.5 16a6 6 0 0 1 7 0"/><circle cx="12" cy="19.4" r="1.1" fill="currentColor" stroke="none"/>',
    "signal_wifi_1_bar": '<path d="M2.5 9.5a15 15 0 0 1 19 0" opacity=".25"/><path d="M5.5 12.8a11 11 0 0 1 13 0" opacity=".25"/><path d="M8.5 16a6 6 0 0 1 7 0" opacity=".25"/><circle cx="12" cy="19.4" r="1.1" fill="currentColor" stroke="none"/>',
    "lock": '<rect x="4.5" y="10.5" width="15" height="10" rx="2.2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>',
    "check_circle": '<circle cx="12" cy="12" r="9.2"/><path d="M8.2 12.3l2.7 2.7 5-5.2"/>',
    "key": '<circle cx="8" cy="12" r="3.6"/><path d="M11.6 12H21"/><path d="M17.6 12v3.2"/><path d="M20.4 12v2.2"/>',
    "visibility": '<path d="M2 12s3.8-6.8 10-6.8S22 12 22 12s-3.8 6.8-10 6.8S2 12 2 12z"/><circle cx="12" cy="12" r="2.8"/>',
    "visibility_off": '<path d="M10.6 6a9.9 9.9 0 0 1 1.4-.1c6.2 0 10 6.8 10 6.8a17 17 0 0 1-3 3.8"/><path d="M6.4 7.7A17.4 17.4 0 0 0 2 12.7s3.8 6.8 10 6.8a10 10 0 0 0 4-.8"/><path d="M9.9 10.5a3 3 0 0 0 4.2 4.2"/><path d="M3 3l18 18"/>',
    "arrow_forward": '<path d="M4.5 12h14"/><path d="M13 6.5l5.5 5.5L13 17.5"/>',
    "refresh": '<path d="M20.5 5.5v5h-5"/><path d="M19.6 14.4a8 8 0 1 1-1.2-7.4l2.1 2"/>',
    "add": '<path d="M12 5.5v13"/><path d="M5.5 12h13"/>',
    "terminal": '<path d="M4.5 17l6-5-6-5"/><path d="M12.5 19h7"/>',
    "palette": '<path d="M12 3.2a8.8 8.8 0 1 0 0 17.6c1.2 0 1.9-.8 1.9-1.7 0-.5-.2-.9-.5-1.2a1.6 1.6 0 0 1 1.2-2.7h1.9A5 5 0 0 0 21 10c0-3.9-4-6.8-9-6.8z"/><circle cx="7.6" cy="11.4" r="1.1" fill="currentColor" stroke="none"/><circle cx="10.4" cy="7.4" r="1.1" fill="currentColor" stroke="none"/><circle cx="15" cy="8" r="1.1" fill="currentColor" stroke="none"/>',
    "downloading": '<path d="M12 4.2v9"/><path d="M8.4 9.6l3.6 3.6 3.6-3.6"/><path d="M4.6 15.4a8 8 0 0 0 14.8 0"/>',
    "smart_toy": '<rect x="3.6" y="8" width="16.8" height="11" rx="3.2"/><path d="M12 8V4.6"/><circle cx="9" cy="13.2" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="13.2" r="1.2" fill="currentColor" stroke="none"/>',
}


def svg(name: str, classes: str = "") -> str:
    body = ICONS.get(name)
    if body is None:
        return ""
    cls = f' class="{classes}"' if classes else ""
    return (
        f'<svg{cls} width="1em" height="1em" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true" focusable="false">{body}</svg>'
    )
