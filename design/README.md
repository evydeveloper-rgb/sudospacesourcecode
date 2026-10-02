# Design

`sudo-mobile-app.dc.html` — Claude Design mockup of the mobile dashboard,
received 2026-08-06.

It is a **mockup, not runnable code**. It uses Claude Design's own runtime
(`<x-dc>`, `<sc-if>`, `<sc-for>`, `DCLogic`) and references four assets that
did not come with it:

    ./support.js
    ./ios-frame.jsx
    _ds/sudo-design-system-3b00530e-.../_shared.css
    _ds/sudo-design-system-3b00530e-.../colors_and_type.css
    _ds/sudo-design-system-3b00530e-.../_ds_bundle.js

Ask whoever exported it for the `_ds/` bundle if we want it to render locally.

Porting it into `raspi-factory/sudo-dashboard/` means translating the
visuals by hand — the two files share almost no design tokens, and the live
dashboard is plain HTML/CSS/JS served by `server.py` with no build step.
