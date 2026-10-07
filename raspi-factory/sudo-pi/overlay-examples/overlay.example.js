// Your overlay. Copy this file to /opt/sudo/overlay.js and edit it.
//
// This loads on every dashboard page, though it can ask for data through the
// API whenever it runs. It survives updates: our code is rewritten, /opt/sudo
// is not.
//
// Two things are promised to you across releases:
//   1. Theme tokens (see theme.css) — colours, text, shape.
//   2. Slots — named mount points like `home.panels`. We keep those anchors
//      stable; the rest of the page is ours to change freely.
//
// This runs with the page's own privileges, on your device. There is no
// sandbox: it can do anything the dashboard can. That is fine for code you
// wrote yourself. Treat anything you did not write with the same care you
// would give a program on your computer.

(function () {
  // Put something into a slot, if the page has it.
  function mount(slotName, build) {
    const slot = document.querySelector(`[data-sudo-slot="${slotName}"]`);
    if (slot) build(slot);
  }

  mount("home.panels", (slot) => {
    const card = document.createElement("div");
    card.className = "model-card";
    const head = document.createElement("div");
    head.className = "model-card-head";
    const title = document.createElement("strong");
    title.textContent = "My own panel";
    head.append(title);
    const body = document.createElement("small");
    body.className = "microcopy";
    body.textContent = "Added by overlay.js. Edit it in /opt/sudo/overlay.js.";
    card.append(head, body);
    slot.append(card);
  });
})();
