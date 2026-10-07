/* Sudo dashboard -- interface translations.
 *
 * Four languages: English, Spanish (neutral, Latin-American friendly),
 * Simplified Chinese and Modern Standard Arabic (right-to-left).
 *
 * Static markup carries data-i18n="key" (text), data-i18n-html="key" (text
 * with a little inline formatting) or data-i18n-attr="attr:key,attr:key"
 * (placeholder, aria-label, title, alt). applyLanguage() walks those in place,
 * so switching never rebuilds the page or loses event handlers. Strings built
 * in the page script go through t(key, vars) / tn(key, count, vars).
 *
 * Every string here is static and written by us. The only markup ever parsed
 * is from these dictionaries, run through a tag allowlist, and any {variable}
 * dropped into such a string is HTML-escaped first.
 *
 * Not translated on purpose: anything the agent or the person writes in chat,
 * brand and product names, model names, code, IDs and web addresses.
 */
(function () {
  'use strict';

  var LANGS = ['en', 'es', 'zh', 'ar'];
  var NATIVE_NAMES = { en: 'English', es: 'Español', zh: '中文', ar: 'العربية' };
  var HTML_LANG = { en: 'en', es: 'es', zh: 'zh-CN', ar: 'ar' };
  var RTL = { ar: true };
  var STORE_KEY = 'sudo-ui-language';
  var DICT = { en: {}, es: {}, zh: {}, ar: {} };
  var current = 'en';
  var vars = { agent: 'sudo' };

  function supported(lang) { return LANGS.indexOf(lang) >= 0; }

  function has(lang, key) {
    return Object.prototype.hasOwnProperty.call(DICT[lang], key);
  }

  function lookup(key) {
    if (has(current, key)) return DICT[current][key];
    if (has('en', key)) return DICT.en[key];
    return key;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function fill(text, values, forHtml) {
    var all = {};
    var k;
    for (k in vars) all[k] = vars[k];
    if (values) for (k in values) all[k] = values[k];
    return text.replace(/\{(\w+)\}/g, function (whole, name) {
      if (!Object.prototype.hasOwnProperty.call(all, name)) return whole;
      return forHtml ? escapeHtml(all[name]) : String(all[name]);
    });
  }

  // Plain text, for textContent and attributes.
  function t(key, values) { return fill(lookup(key), values, false); }

  // Plural-aware: looks up key.one / key.other / key.two / key.few ... using
  // the language's own rules (Arabic has six forms, Chinese one).
  function pluralKey(key, n) {
    var form = 'other';
    try { form = new Intl.PluralRules(HTML_LANG[current]).select(n); } catch (e) { /* old browser */ }
    if (has(current, key + '.' + form)) return key + '.' + form;
    if (has(current, key + '.other')) return key + '.other';
    return key + (n === 1 ? '.one' : '.other');
  }

  function tn(key, n, values) {
    var all = { n: n };
    if (values) for (var k in values) all[k] = values[k];
    return t(pluralKey(key, n), all);
  }

  // Formatted strings (a few <strong>/<em>/<a>/<code>/<br>) for innerHTML-like
  // places, with variables escaped.
  function tHtml(key, values) { return fill(lookup(key), values, true); }

  var ALLOWED = { STRONG: 1, EM: 1, B: 1, CODE: 1, BR: 1, A: 1, SMALL: 1, BDI: 1 };

  function clean(node) {
    Array.prototype.slice.call(node.childNodes).forEach(function (child) {
      if (child.nodeType === 3) return;
      if (child.nodeType !== 1 || !ALLOWED[child.tagName]) {
        node.replaceChild(document.createTextNode(child.textContent || ''), child);
        return;
      }
      var href = child.tagName === 'A' ? child.getAttribute('href') : null;
      while (child.attributes.length) child.removeAttribute(child.attributes[0].name);
      if (href && /^https:\/\//.test(href)) {
        child.setAttribute('href', href);
        child.setAttribute('target', '_blank');
        child.setAttribute('rel', 'noopener noreferrer');
      }
      clean(child);
    });
  }

  function setHtml(el, markup) {
    var tpl = document.createElement('template');
    tpl.innerHTML = markup;
    clean(tpl.content);
    el.replaceChildren(tpl.content);
  }

  function translateTree(root) {
    var scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    scope.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      setHtml(el, tHtml(el.getAttribute('data-i18n-html')));
    });
    scope.querySelectorAll('[data-i18n-attr]').forEach(function (el) {
      el.getAttribute('data-i18n-attr').split(',').forEach(function (pair) {
        var at = pair.indexOf(':');
        if (at > 0) el.setAttribute(pair.slice(0, at).trim(), t(pair.slice(at + 1).trim()));
      });
    });
  }

  function setDocumentLanguage(lang) {
    var html = document.documentElement;
    html.lang = HTML_LANG[lang];
    html.dir = RTL[lang] ? 'rtl' : 'ltr';
  }

  function applyLanguage(lang) {
    current = supported(lang) ? lang : 'en';
    setDocumentLanguage(current);
    translateTree(document);
    document.documentElement.classList.remove('i18n-pending');
    document.dispatchEvent(new CustomEvent('i18n:change', { detail: { lang: current } }));
    return current;
  }

  function remembered() {
    try {
      var saved = localStorage.getItem(STORE_KEY);
      return supported(saved) ? saved : null;
    } catch (e) { return null; }
  }

  function remember(lang) {
    try { localStorage.setItem(STORE_KEY, lang); } catch (e) { /* private mode */ }
  }

  function fromBrowser() {
    var list = [];
    try {
      if (navigator.language) list.push(navigator.language);
      if (navigator.languages) list = list.concat(navigator.languages);
    } catch (e) { /* ignore */ }
    for (var i = 0; i < list.length; i += 1) {
      var base = String(list[i] || '').toLowerCase().split('-')[0];
      if (supported(base)) return base;
    }
    return 'en';
  }

  // Before the device has said anything: the choice cached in this browser,
  // or the browser's own language when it is one of ours.
  function initial() { return remembered() || fromBrowser(); }

  // {agent} in a string is the agent's own name. Re-translate only when the
  // name actually changes, since every status poll reports it.
  function setVars(values) {
    var changed = false;
    for (var k in values) {
      if (vars[k] !== values[k]) { vars[k] = values[k]; changed = true; }
    }
    if (changed) translateTree(document);
  }

  function add(lang, table) {
    for (var k in table) DICT[lang][k] = table[k];
  }

  // A few error texts come from the device itself, in English. Known ones are
  // swapped for a translation; anything else is shown as sent.
  var SERVER_ERRORS = {
    'Invalid password': 'srv.invalidPassword',
    'Too many login attempts': 'srv.tooManyAttempts',
    'Password required': 'srv.passwordRequired',
    'Invalid JSON': 'srv.invalidRequest',
    'Not found': 'srv.notFound',
  };

  function serverError(message) {
    var key = SERVER_ERRORS[message];
    return key ? t(key) : message;
  }

  window.I18N = {
    LANGS: LANGS,
    NATIVE_NAMES: NATIVE_NAMES,
    add: add,
    t: t,
    tn: tn,
    tHtml: tHtml,
    setHtml: setHtml,
    apply: applyLanguage,
    translate: translateTree,
    lang: function () { return current; },
    htmlLang: function () { return HTML_LANG[current]; },
    isRtl: function () { return !!RTL[current]; },
    supported: supported,
    initial: initial,
    remember: remember,
    setVars: setVars,
    setDocumentLanguage: setDocumentLanguage,
    serverError: serverError,
    _dict: function () { return DICT; },
  };
  window.t = t;
  window.tn = tn;
  window.applyLanguage = applyLanguage;
})();

// ---- English: page markup --------------------------------------------------
I18N.add('en', {
  "nav.primaryNavigation": "Primary navigation",
  "nav.menu": "menu",
  "nav.finishSetup": "Finish setup",
  "nav.chat": "Chat",
  "nav.channels": "Channels",
  "nav.apps": "Apps",
  "nav.connectors": "Connectors",
  "nav.settings": "Settings",
  "top.knowledgeHub": "Knowledge hub",
  "hint.keepSudoOneTap": "Keep sudo one tap away",
  "hint.dismiss": "Dismiss",
  "welcome.letSMakeThis": "let’s make this yours.",
  "welcome.firstKeepMeSomewhere": "First, keep me somewhere easy to reach. There’s no app to install — this page becomes the app.",
  "welcome.doneNext": "Done — next",
  "welcome.skipForNow": "Skip for now",
  "welcome.pickHowIShould": "pick how I should look.",
  "welcome.pickALookTo": "Pick a look to start with — tap one to try it. Later on you and I can shape this into something properly yours: your own colours, your own layout, whatever you actually use it for.",
  "welcome.default": "Default",
  "welcome.lightPurpleAndCoral": "Light, purple and coral.",
  "welcome.minimal": "Minimal",
  "welcome.nearMonochromeQuiet": "Near-monochrome. Quiet.",
  "welcome.terminal": "Terminal",
  "welcome.darkCoralAccents": "Dark, coral accents.",
  "welcome.midnight": "Midnight",
  "welcome.darkVioletAccents": "Dark, violet accents.",
  "welcome.next": "Next",
  "welcome.back": "← Back",
  "welcome.sayHiToEach": "say hi to each other.",
  "welcome.twoThingsAndWe": "Two things and we can start talking. Everything else can wait — it’s all in Settings later.",
  "welcome.whatShouldICall": "What should I call you?",
  "welcome.yourName": "your name",
  "welcome.andWhatShouldYou": "And what should you call me?",
  "welcome.sayHello": "Say hello",
  "login.thisOneNeedsA": "this one needs a password.",
  "login.youAreOpeningSudo": "You are opening sudo over its public link, so it asks before letting anyone in. The password is shown in Settings when you open the dashboard at home.",
  "login.password": "Password",
  "login.password2": "password",
  "login.unlock": "Unlock",
  "chat.availableAnywhere": "Available anywhere",
  "chat.copyLink": "copy link",
  "chat.settingUpTheOn": "Setting up the on-device brain",
  "chat.giveMeAProper": "Give me a proper brain",
  "chat.iMRunningOn": "I'm running on the small on-device model — good for chatting, not for real work. Connect a cloud model whenever you're ready.",
  "chat.goToOpenrouter": "Go to OpenRouter",
  "chat.setUp": "Set up",
  "chat.thinking": "thinking",
  "chat.startAFreshChat": "Start a fresh chat",
  "chat.freshChat": "Fresh chat",
  "chat.messageYourAgent": "Message your agent",
  "chat.askSudoAnything": "ask sudo anything…",
  "chat.chooseTheModel": "Choose the model",
  "chat.sendMessage": "Send message",
  "guide.cloudModel": "Cloud model",
  "guide.yourApps": "Your apps",
  "guide.allSet": "All set.",
  "guide.yourAgentHasA": "Your agent has a cloud brain, WhatsApp and your apps. This guide now lives under the book icon at the top right, so you can come back to it any time.",
  "guide.goToChat": "Go to chat",
  "guide.cloud.connectACloudModel": "Connect a cloud model",
  "guide.cloud.giveYourAgentA": "Give your agent a proper brain · about 5 minutes",
  "guide.cloud.toDo": "To do",
  "guide.cloud.doneYourAgentIs": "Done. Your agent is answering with a cloud model. The rest is here whenever you want to read it again.",
  "guide.cloud.whatSOnYour": "What’s on your Sudo today",
  "guide.cloud.yourSudoAlreadyHas": "Your Sudo already has a brain of its own: <strong>Gemma 3 270M</strong>, a small model that runs entirely on the device. “270M” means 270 million <em>parameters</em>, the numbers a model learns with. More parameters usually means a more capable model.",
  "guide.cloud.onYourSudoGemma": "On your Sudo · Gemma 3 270M",
  "guide.cloud.270Million": "270 million",
  "guide.cloud.mostAPi5": "Most a Pi 5 (4GB) can run*",
  "guide.cloud.4Billion": "≈ 4 billion",
  "guide.cloud.frontierCloudModelsChatgpt": "Frontier cloud models (ChatGPT, Claude…)",
  "guide.cloud.notPublishedEstimatedHundreds": "Not published · estimated hundreds of billions to trillions",
  "guide.cloud.parametersOnALog": "Parameters, on a log scale: each gridline is 10× the one before. OpenAI does not publish how big ChatGPT’s frontier model is, so that bar is an outside estimate, shown fading out. *A rule of thumb, not a hard limit.",
  "guide.cloud.whyAbout4Billion": "Why about 4 billion on a Pi?",
  "guide.cloud.thePi5In": "The Pi 5 in your Sudo has 4GB of memory, shared with everything else it runs. As a rule of thumb that fits a model of roughly 4 billion parameters at most, and only heavily compressed (“quantized”), so each parameter takes about half a byte instead of two. Anything bigger simply doesn’t fit, and even that size answers slowly.",
  "guide.cloud.ourAdviceTheOn": "<strong>Our advice</strong> The on-device model is fine for a chat. For everyday work like email, research and planning, we recommend a cloud model.",
  "guide.cloud.whatYourAgentIs": "What your agent is made of",
  "guide.cloud.whatAnAgentIs": "What an agent is made of",
  "guide.cloud.memoryContextAndTools": "Memory, context and tools stay with you. The LLM plugs in underneath and can be swapped for ChatGPT, Claude, DeepSeek or any other model.",
  "guide.cloud.yoursToKeepGrows": "YOURS TO KEEP · GROWS EVERY DAY",
  "guide.cloud.memory": "Memory",
  "guide.cloud.whatItKnows": "what it knows",
  "guide.cloud.context": "Context",
  "guide.cloud.yourLifeFiles": "your life, files",
  "guide.cloud.tools": "Tools",
  "guide.cloud.appsTheWeb": "apps, the web",
  "guide.cloud.llmSwappable": "LLM · swappable",
  "guide.cloud.swapAnyTime": "swap any time",
  "guide.cloud.orAlmostAnyOther": "…or almost any other model",
  "guide.cloud.memoryContextAndTools2": "<strong>Memory, context and tools are yours.</strong> They’re what your agent knows about you and can do for you, and they grow every day you use it.",
  "guide.cloud.theLlmIsThe": "<strong>The LLM is the swappable part.</strong> AI companies keep leapfrogging each other: one month ChatGPT leads, the next it’s Claude or DeepSeek. With Sudo you’re not locked in. Switch models and keep your memory and context. No starting over.",
  "guide.cloud.openrouterOneKeyNearly": "OpenRouter: one key, nearly every model",
  "guide.cloud.youAddCreditTo": "You add credit to OpenRouter; Sudo uses one OpenRouter key; OpenRouter reaches OpenAI, Anthropic, Google, DeepSeek and many more.",
  "guide.cloud.you": "You",
  "guide.cloud.credit": "credit",
  "guide.cloud.oneKey": "one key",
  "guide.cloud.manyMore": "+ many more",
  "guide.cloud.openrouterIsOneAccount": "OpenRouter is one account with one universal API key that reaches nearly every AI model. You top up credit there, and it pays the AI companies for you.",
  "guide.cloud.createAnAccountAt": "Create an account at <a href=\"https://openrouter.ai\">openrouter.ai</a>.",
  "guide.cloud.addCreditYouChoose": "Add credit. You choose how much.",
  "guide.cloud.createAKeyUnder": "Create a key under <a href=\"https://openrouter.ai/settings/keys\">Keys</a> and paste it into Sudo: Settings → API keys. It starts with <code>sk-or-</code>.",
  "guide.cloud.sudoTakesNoCut": "Sudo takes no cut",
  "guide.cloud.youPayTheAi": "You pay the AI companies through OpenRouter. Your key stays on this device, and we never see your bill.",
  "guide.cloud.openrouterSSmallFee": "OpenRouter’s small fee",
  "guide.cloud.openrouterAddsAFee": "OpenRouter adds a fee when you buy credit: 5.5% by card (5% with crypto). Model prices are passed through with no markup. Sudo adds nothing. <a href=\"https://openrouter.ai/pricing\">Their pricing</a>",
  "guide.cloud.seeEveryCent": "See every cent",
  "guide.cloud.yourOpenrouterAccountShows": "Your OpenRouter account shows credit left and a log of every request: which model, how many tokens, what it cost. <a href=\"https://openrouter.ai/settings/credits\">Credits</a> · <a href=\"https://openrouter.ai/logs\">Logs</a>",
  "guide.cloud.aSpendingGuard": "A spending guard",
  "guide.cloud.sudoSCircuitBreaker": "Sudo’s circuit breaker pauses your agent once it hits a daily message limit. You choose the limit.",
  "guide.cloud.openSpendingGuard": "Open Spending guard",
  "guide.cloud.whatItCosts": "What it costs",
  "guide.cloud.modelsChargePerToken": "Models charge per token, roughly ¾ of a word, and prices differ hugely:",
  "guide.cloud.model": "Model",
  "guide.cloud.in": "In",
  "guide.cloud.out": "Out",
  "guide.cloud.deepseekV4FlashRecommended": "DeepSeek V4 Flash <em>recommended</em>",
  "guide.cloud.usDollarsPerMillion": "US dollars per million tokens on openrouter.ai, as of October 2026. Bars show the output price to scale. DeepSeek V4 Flash is served by several providers at different prices; this is the rate most of them charge. Prices change, so check a model’s page for today’s.",
  "guide.cloud.aMonthIsWhat": "a month is what most people should expect with DeepSeek V4 Flash as the main model, our recommended default. <small>A typical estimate, not a guarantee: heavy use, helpers and pricier models cost more.</small>",
  "guide.cloud.preferFullyPrivate": "Prefer fully private?",
  "guide.cloud.switchBackToThe": "Switch back to the on-device model any time in Settings → Model &amp; routing → <em>Keep it on this device</em>. It’s fully private and free. We’re also working on fine-tuned, compressed local models made for Sudo. Coming soon.",
  "guide.cloud.openModelRouting": "Open Model & routing",
  "guide.cloud.addMyOpenrouterKey": "Add my OpenRouter key",
  "guide.cloud.openOpenrouterAi": "Open openrouter.ai →",
  "guide.cloud.thenTryAsking": "Then try asking",
  "guide.cloud.pleaseSwitchToThe": "“Please switch to the new MiniMax model”",
  "guide.cloud.putsItInThe": "Puts it in the chat box. Nothing is sent until you press send.",
  "guide.wa.connectWhatsapp": "Connect WhatsApp",
  "guide.wa.talkToYourAgent": "Talk to your agent where you already chat · about 5 minutes",
  "guide.wa.doneWhatsappIsLinked": "Done. WhatsApp is linked. The options below are still yours to change.",
  "guide.wa.twoWaysToLink": "Two ways to link it",
  "guide.wa.yourPhone": "your phone",
  "guide.wa.yourOwnWhatsapp": "Your own WhatsApp",
  "guide.wa.yourAgentLivesIn": "Your agent lives in your “Message yourself” chat.",
  "guide.wa.itNeverMessagesYour": "It never messages your contacts.",
  "guide.wa.nothingExtraToBuy": "Nothing extra to buy.",
  "guide.wa.agentSSim": "agent’s SIM",
  "guide.wa.itsOwnNumber": "Its own number",
  "guide.wa.youNeedASecond": "You need a second SIM or phone number that you own.",
  "guide.wa.makeANewWhatsapp": "Make a new WhatsApp account on it for your agent.",
  "guide.wa.yourAgentMessagesYou": "Your agent messages you from its own contact.",
  "guide.wa.youCanLinkBoth": "You can link both. Then your agent reaches you from its own number.",
  "guide.wa.easiestOnAComputer": "<strong>Easiest on a computer</strong> You’ll scan a QR code with your phone’s camera, so open this page on a computer first, then go to Channels → WhatsApp.",
  "guide.wa.yourPrivacyChoices": "Your privacy choices",
  "guide.wa.bothAreOffUntil": "Both are off until you switch them on. Change them any time here or in Channels.",
  "guide.wa.letMyAgentRead": "Let my agent read my other WhatsApp chats",
  "guide.wa.readOnlyItCan": "Read-only: it can see your chats with other people on your own linked WhatsApp, and never replies to them.",
  "guide.wa.whatThisAllows": "What this allows ⓘ",
  "guide.wa.includeWhatsappInMy": "Include WhatsApp in my daily summary",
  "guide.wa.yourDailySummaryAnd": "Your daily summary and reminders also list who may still need a reply. Needs the switch above on.",
  "guide.wa.setUpWhatsappIn": "Set up WhatsApp in Channels",
  "guide.wa.sendMeMyDaily": "“Send me my daily to-do on my WhatsApp”",
  "guide.apps.connectYourApps": "Connect your apps",
  "guide.apps.letYourAgentUse": "Let your agent use Gmail, Calendar, Shopify and more · about 5 minutes",
  "guide.apps.doneYourComposioKey": "Done. Your Composio key is saved. Ask your agent to connect any app.",
  "guide.apps.whatComposioIs": "What Composio is",
  "guide.apps.composioKeepsReadyMade": "Composio keeps ready-made connections to 1,600+ apps. You sign in to an app once, and your agent can use it: send the email, add the event, check the order.",
  "guide.apps.whyWeUseIt": "Why we use it, how logins work, and your privacy ⓘ",
  "guide.apps.getYourKeyIn": "Get your key in three steps",
  "guide.apps.makeAFreeAccount": "Make a free account at <a href=\"https://dashboard.composio.dev\">dashboard.composio.dev</a>.",
  "guide.apps.goToConnectSettings": "Go to Connect → Settings → Sessions &amp; API Key and copy your key. It starts with <code>ck_</code>.",
  "guide.apps.pasteItInConnectors": "Paste it in Connectors and press <em>Save key</em>.",
  "guide.apps.thenToConnectAn": "Then, to connect an app, just ask your agent. It sends you a sign-in link for that app.",
  "guide.apps.openConnectors": "Open Connectors",
  "guide.apps.pleaseHelpMeConnect": "“Please help me connect to my Shopify store”",
  "apps.focusedSpacesSudoCan": "Focused spaces sudo can help you run.",
  "apps.comingSoon": "coming soon",
  "apps.financialTracker": "Financial tracker",
  "apps.incomeSpendingAndWhat": "Income, spending, and what’s left at a glance.",
  "apps.healthTracker": "Health tracker",
  "apps.sleepMovementAndHow": "Sleep, movement, and how you’re really feeling.",
  "apps.journal": "Journal",
  "apps.aQuietPlaceTo": "A quiet place to think out loud with sudo.",
  "apps.home": "Home",
  "apps.lightsLocksAndSmall": "Lights, locks, and small daily routines.",
  "apps.createYourOwn": "Create your own",
  "apps.describeWhatYouWant": "Describe what you want and sudo builds it with you.",
  "channels.chooseWhereYouWant": "Choose where you want to talk with sudo.",
  "set.channels.comingSoon": "Coming soon",
  "set.channels.wantToGiveYour": "<strong>Want to give your agent its own phone number?</strong> We use Tello, a third-party phone carrier — <a href=\"https://www.amazon.com/dp/B08H5SM9M9\">get a Tello SIM</a>. With a second number you can make a separate WhatsApp account for your agent, so it messages you from its own contact instead of inside your own chat. <small>Not sponsored — it’s just what we use. Any carrier that gives you a number that can receive a text will work, so we’d encourage you to look around for what suits you.</small>",
  "set.channels.close": "Close",
  "set.channels.whatsappOptions": "WhatsApp options",
  "set.channels.offByDefaultRead": "Off by default. Read-only: it can see your chats with other people on your own linked WhatsApp, and never replies to them.",
  "set.channels.whatItAllowsYour": "<strong>What it allows.</strong> Your agent can read the chats on <em>your own</em> linked WhatsApp, meaning your conversations with other people, so it can remind you of things and notice who is waiting on you.<br><br><strong>What it never does.</strong> It never replies to anyone in those chats or messages them. It still only talks to you.<br><br><strong>Only your own account.</strong> This applies only to the WhatsApp linked as “Your WhatsApp”. The agent’s own number has no other chats to read.<br><br><strong>Off by default.</strong> Nothing is read until you switch it on. Switching it off stops it.",
  "set.channels.offByDefaultYour": "Off by default. Your daily summary and reminders also list who may still need a reply. Needs the switch above on.",
  "conn.letSudoUseYour": "Let sudo use your other apps — sending email, adding calendar events, filing issues — instead of only talking about them.",
  "set.connectors.connectorsLetSudoUse": "Connectors let sudo use your other apps — sending email, adding calendar events, filing issues — instead of only talking about them.",
  "set.connectors.composioApiKey": "Composio API key",
  "set.connectors.pasteYourKey": "paste your key",
  "set.connectors.fromDashboardComposioDev": "From <strong>dashboard.composio.dev</strong> → Connect → Settings → Sessions &amp; API Key. It starts with <code>ck_</code>. Stored on this device only — saving it switches connectors on.",
  "set.connectors.saveKey": "Save key",
  "set.connectors.removeKey": "Remove key",
  "set.connectors.whatYouCanConnect": "what you can connect",
  "set.connectors.1600AppsYour": "1,600+ apps your agent can use",
  "set.connectors.andMuchMoreTo": "…and much more. To connect one, just ask your agent — it sends you a sign-in link. <a href=\"https://composio.dev/toolkits\">See every app</a>",
  "set.connectors.whatIsComposioWhy": "What is Composio? Why we use it, and your privacy ⓘ",
  "set.connectors.whyWeUseIt": "<strong>Why we use it.</strong> Connecting an agent to your apps one by one means building each connection yourself — and they break whenever an app changes. Composio keeps ready-made, maintained connections to 1,600+ apps, so you sign in to an app once and sudo can use it.<br><br><strong>Your logins.</strong> You sign in on each app’s own page, so sudo never sees your password. Composio stores the access it’s given encrypted, uses it only for the moment sudo asks to do something, and never passes it to sudo or the AI model. You can disconnect any app at any time from your Composio dashboard.<br><br><strong>Privacy.</strong> Composio says it doesn’t sell or share data from your Google account and doesn’t use your data to train AI. It’s independently audited (SOC 2 Type II, ISO 27001). By default it keeps a log of what sudo did in each app for up to a year; there’s a setting to stop that (Zero Data Retention), and you can ask support@composio.dev to delete your data. <a href=\"https://composio.dev/privacy\">Privacy policy</a> · <a href=\"https://trust.composio.dev\">Security</a>",
  "set.connectors.otherWaysToExtend": "other ways to extend sudo",
  "set.connectors.mcpServers": "MCP servers ⓘ",
  "set.connectors.mcpIsAnOpen": "<strong>MCP</strong> is an open standard for giving an agent new abilities. An MCP server is a small program exposing tools — read this database, search these docs, control this app — and sudo can call them.<br><br>Composio is the no-setup option; MCP is what you reach for when you want something specific or self-hosted. It runs on your device, so nothing leaves your network.<br><br><em>Not wired up yet</em> — it needs the same care as running commands, since an MCP server is code you chose to trust.",
  "set.connectors.cliTools": "CLI tools ⓘ",
  "set.connectors.commandLineProgramsAlready": "Command-line programs already on the device — <code>git</code>, <code>curl</code>, <code>ffmpeg</code>, and anything else you install. The broadest option and the one with the fewest guardrails.<br><br>Turned on under <strong>What sudo can do → Run commands on this device</strong>. Read the warning there first: a web page can contain text written to trick an agent into running something harmful.",
  "settings.backToSettings": "Back to settings",
  "settings.agent": "agent",
  "set.profile.agentPersonality": "Agent & personality",
  "set.profile.yourName": "Your name",
  "set.profile.agentName": "Agent name",
  "set.profile.personality": "Personality",
  "set.profile.friendly": "Friendly",
  "set.profile.warmAndEasygoing": "Warm and easygoing.",
  "set.profile.professional": "Professional",
  "set.profile.crispAndMeasured": "Crisp and measured.",
  "set.profile.concise": "Concise",
  "set.profile.onlyTheWordsNeeded": "Only the words needed.",
  "set.profile.creative": "Creative",
  "set.profile.playfulAndCurious": "Playful and curious.",
  "set.profile.saveSettings": "Save settings",
  "set.routing.modelRouting": "Model & routing",
  "set.routing.whichModelAnswersYou": "Which model answers you. Automatic is the cheapest sensible choice on every message; fixed always uses the one you pick.",
  "set.routing.answeringRightNow": "Answering right now",
  "set.routing.keepItOnThis": "Keep it on this device",
  "set.routing.answerWithTheOn": "Answer with the on-device model even when a key is saved. Nothing you type leaves the house.",
  "set.routing.preferTheOnDevice": "Prefer the on-device model",
  "set.routing.whatYouGiveUp": "What you give up ⓘ",
  "set.routing.theOnDeviceModel": "The on-device model is small — roughly a thousandth the size of the cloud ones. It can greet you, chat, and handle simple things. It cannot search the web, reason through anything long, or look at images.<br><br>It costs nothing to run and works with the internet down.<br><br>If it is not installed yet, messages still go to the cloud rather than failing.",
  "set.routing.automatic": "Automatic",
  "set.routing.picksPerMessageCheapest": "Picks per message. Cheapest that can do the job.",
  "set.routing.alwaysThisOne": "Always this one",
  "set.routing.sameModelEveryTime": "Same model every time.",
  "set.routing.howAutomaticRoutingPicks": "How automatic routing picks ⓘ",
  "set.routing.shortAndSimpleAsks": "<strong>Short and simple asks</strong> go to the cheapest text model, which is where most messages land.<br><br><strong>Anything with an image</strong> goes to a model that can actually see — the default is text-only, so without this it would just guess.<br><br><strong>Long or multi-step work</strong> steps up to a stronger model, because a cheap model looping on a hard task costs more than the good model doing it once.<br><br>You are billed per message, so automatic usually costs less than pinning everything to one strong model.",
  "set.routing.aboutModels": "About models",
  "set.routing.theBrainYourAgent": "The brain your agent thinks with. All four cost you differently per message — cheaper ones are quicker and fine for everyday chat, pricier ones reason better on hard problems.<br><br><strong>DeepSeek V4 Flash</strong> — the default, and by far the cheapest. Text only, so it can't look at photos you send.<br><strong>GPT-4o mini</strong> — cheap and quick, and can read images.<br><strong>Claude Haiku 4.5</strong> — a step up in quality, reads images.<br><strong>Claude Sonnet 4.5</strong> — the strongest here and the most expensive. Best for tricky, multi-step work.<br><br>Switch whenever you like. If you send a photo to a model that can't see, sudo hands it to one that can.",
  "set.routing.onDeviceGreeter": "on-device greeter",
  "set.routing.whatThisIs": "What this is ⓘ",
  "set.routing.gemma3270mAbout": "<strong>Gemma 3 270M</strong>, about 200MB, running on the Pi rather than in the cloud. It greets you and keeps sudo talking when there is no API key or no internet.<br><br>It is deliberately small: quick to answer, but it cannot browse, remember past chats, or answer factual questions. Those go to the cloud model.<br><br>It downloads by itself a few minutes after the device first joins Wi-Fi. If it says <em>not installed</em> long after that, the device could not reach the download.",
  "set.files.whatSudoKnows": "What sudo knows",
  "set.files.thesePlainTextFiles": "These plain-text files are your agent's personality and everything it has remembered about you. Nothing is hidden — read any of it.",
  "set.files.storedOnThisDevice": "Stored on this device at <code>/opt/sudo/agent-workspace</code>. To edit them, turn on terminal access below.",
  "set.theme.look": "Look",
  "set.theme.coloursOnlyNothingChanges": "Colours only — nothing changes about how sudo behaves. Applies as soon as you pick.",
  "settings.apiManagement": "api management",
  "set.providers.apiKeysBillingSource": "API keys & billing source",
  "set.providers.whereYourAiUsage": "Where your AI usage is billed",
  "set.providers.aboutApiKeys": "About API keys",
  "set.providers.yourAgentThinksBy": "Your agent thinks by calling AI models, and someone has to pay for each call.<br><br><strong>Your own key</strong> — you sign up at <code>openrouter.ai</code>, paste your key here, and pay them directly. The key lives on this device and never reaches us. No accounts to create with us, no subscription.",
  "set.providers.sudoCredits": "Sudo credits",
  "set.providers.weHandleBilling": "We handle billing",
  "set.providers.myOwnKey": "My own key",
  "set.providers.youBillDirect": "You bill direct",
  "set.providers.openrouterApiKey": "OpenRouter API key",
  "set.providers.savedOnThisDevice": "Saved on this device and sent only to the provider it belongs to. It never reaches us. Anyone who can get into the device can read it, so treat it like the password to your account there.",
  "set.providers.save": "Save",
  "set.providers.bringYourOwnKeys": "bring your own keys",
  "set.providers.pasteAKeyAnd": "Paste a key and sudo works out who it belongs to. Everything stays on this device.",
  "set.providers.pasteAnyApiKey": "Paste any API key",
  "set.providers.openrouterOpenaiAnthropicGemini": "OpenRouter, OpenAI, Anthropic, Gemini, Venice or DeepSeek — no need to say which.",
  "set.providers.orChooseTheProvider": "or choose the provider yourself",
  "set.providers.whichOneShouldI": "Which one should I add ⓘ",
  "set.providers.youDonTNeed": "<strong>You don't need any of these.</strong> Sudo credits cover everything out of the box.<br><br><strong>OpenRouter</strong> is the one to add if you only add one — a single key reaching most models from every provider.<br><br>A direct key (OpenAI, Anthropic, Gemini) is usually cheaper per message for that provider's own models, and worth it if you already pay them.<br><br>Keys never leave the device except to the provider they belong to. Delete one by clearing the box and saving.",
  "settings.security": "security",
  "set.abilities.whatSudoCanDo": "What sudo can do",
  "set.abilities.abilitiesYouCanGrant": "Abilities you can grant your agent. Everything here is your call — turn things on as you need them.",
  "set.abilities.searchTheWeb": "Search the web",
  "set.abilities.lookUpCurrentInformation": "Look up current information to answer you.",
  "set.abilities.on": "on",
  "set.abilities.runCommandsOnThis": "Run commands on this device",
  "set.abilities.onByDefaultThe": "On by default. The agent can use the terminal — install things, edit files, check the system. Switch off to keep it talking only.",
  "set.abilities.toggleCommandAccess": "Toggle command access",
  "set.abilities.aboutCommandAccess": "About command access",
  "set.abilities.whyItSOn": "<strong>Why it's on.</strong> Doing things is the point. With the terminal the agent can actually install, fix and check things on your Pi instead of describing what you could do yourself.<br><br><strong>What you're accepting.</strong> It has the same power on this device that you do when you log in. It also reads web pages, and a page can contain text written to trick it into running something — it can't always tell instructions from content. Blocked-command patterns catch obvious attempts, not clever ones.<br><br><strong>When to switch it off.</strong> If this Pi holds anything you'd mind losing, or if you've turned on <em>Access from anywhere</em> — that puts the dashboard on a public link with no password, and anyone who has it would be talking to something that can run commands.",
  "set.abilities.workWithHelpers": "Work with helpers",
  "set.abilities.splitBigJobsAcross": "Split big jobs across copies of itself, then combine the results.",
  "set.abilities.toggleSubAgents": "Toggle sub-agents",
  "set.abilities.aboutHelpers": "About helpers",
  "set.abilities.forAJobWith": "For a job with several parts, {agent} can start temporary helpers, give each a piece, and gather the answers. Useful for research or anything with a lot of steps.<br><br><strong>It costs more.</strong> Every helper is its own conversation and is billed separately, so one request can cost several times a normal message. Limited to 2 helpers at a time.",
  "set.abilities.installSkills": "Install skills",
  "set.abilities.downloadReadyMadeAbilities": "Download ready-made abilities from the ClawHub library.",
  "set.abilities.toggleSkills": "Toggle skills",
  "set.abilities.aboutSkills": "About skills",
  "set.abilities.skillsAreSmallAdd": "Skills are small add-ons other people have written, fetched from clawhub.ai and run on your device.<br><br><strong>They are code from strangers.</strong> We don't review them. Leave this off unless you want a specific skill and trust where it came from.",
  "set.abilities.fullDeviceAccess": "Full device access",
  "set.abilities.offByDefaultLets": "Off by default. Lets {agent} change this dashboard itself, add things to it, and reach anywhere on the device — not just its own workspace.",
  "set.abilities.toggleFullDeviceAccess": "Toggle full device access",
  "set.abilities.aboutFullDeviceAccess": "About full device access",
  "set.abilities.whyItSOff": "<strong>Why it's off.</strong> Normally {agent} is boxed into its own workspace — its memory, its notes, nothing else. This takes the box away, the same as running it on a regular computer with no restriction at all.<br><br><strong>What you're accepting.</strong> It can rewrite this dashboard, install things, and change anything on the device — genuine ownership, but with no undo button if an edit goes wrong. It also means it could, if asked the right way by whoever it's talking to, read its own configuration file out loud — and that file holds your live API key. Treat it as equivalent to handing out that key.<br><br><strong>When to turn it on.</strong> When you want it building or changing things here yourself, and you've accepted that a bad edit is yours to fix — the same trust you'd put in a program you run directly on your own machine.",
  "set.abilities.speakUpOnIts": "Speak up on its own",
  "set.abilities.onByDefaultAgent": "On by default. {agent} can message you first now and then — a reminder about something you said you'd do, or a reply you're waiting on. Most checks end in silence.",
  "set.abilities.toggleProactiveMessages": "Toggle proactive messages",
  "set.abilities.aboutProactiveMessages": "About proactive messages",
  "set.abilities.whatItDoesEvery": "<strong>What it does.</strong> Every half hour {agent} takes a cheap look at what it knows about you and decides whether there is anything genuinely worth sending. If there isn't, it stays quiet — you won't get a scheduled \"just checking in\".<br><br><strong>Only after you've messaged it.</strong> It never opens a WhatsApp conversation by itself. Until you write to it once there is nowhere to send, and it does nothing. No messages between 22:00 and 08:00.<br><br><strong>Why it's on.</strong> An agent that only ever answers is a search box. Speaking first is what makes it feel like it's actually paying attention. Switch it off if you'd rather it stayed silent unless spoken to.",
  "set.spend.spendingGuard": "Spending guard",
  "set.spend.aSafetyCapOn": "A safety cap on how much {agent} talks to the paid model. Past the limit it pauses itself until tomorrow, so a runaway loop or a curious kid can't burn through your API budget.",
  "set.spend.pauseWhenTheLimit": "Pause when the limit is hit",
  "set.spend.onByDefaultSwitch": "On by default. Switch off to remove the cap entirely — you keep full control of your own device, but nothing stops a runaway.",
  "set.spend.toggleSpendingGuard": "Toggle spending guard",
  "set.spend.aboutTheSpendingGuard": "About the spending guard",
  "set.spend.whatItDoesEvery": "<strong>What it does.</strong> Every message to {agent} costs a little, and a stuck loop can spend a lot before anyone notices. This counts those calls right here on the device and stops them once you've hit the daily number.<br><br><strong>No dollar guessing.</strong> It counts messages, not money, and it never sees your key or touches the network — the count lives on your Pi.<br><br><strong>It can't lock you out.</strong> If anything goes wrong reading the count, it lets the message through. Worst case you're back to no cap, never a bricked agent.",
  "set.spend.messagesPerDay": "Messages per day",
  "set.spend.burstLimitPerMinute": "Burst limit (per minute)",
  "set.spend.pauseLengthAfterA": "Pause length after a burst (minutes)",
  "set.update.updates": "Updates",
  "set.update.newVersionsInstallOver": "New versions install over Wi‑Fi — no reflashing the card. Your files, memory, keys and WhatsApp pairing are kept; only the app changes.",
  "set.update.currentVersion": "Current version",
  "set.update.automaticUpdates": "Automatic updates",
  "set.update.checksOnceADay": "Checks once a day and installs new versions overnight.",
  "set.update.checkForUpdates": "Check for updates",
  "set.update.updateNow": "Update now",
  "set.update.whatSNewIn": "What’s new in each version",
  "set.update.seeTheCode": "See the code",
  "settings.deviceDeveloper": "device & developer",
  "set.access.accessNetwork": "Access & network",
  "set.access.deviceName": "Device name",
  "set.access.thisIsTheWeb": "This is the web address you use to reach sudo. Letters, numbers and hyphens.",
  "set.access.renameDevice": "Rename device",
  "set.access.network": "network",
  "set.access.homeNetwork": "Home network",
  "set.access.anyoneOnThisWi": "Anyone on this Wi-Fi can open the dashboard.",
  "set.access.connected": "connected",
  "set.access.wiFi": "Wi-Fi",
  "set.access.change": "Change",
  "set.access.accessFromAnywhere": "Access from anywhere",
  "set.access.createsATemporaryPublic": "Creates a temporary public Cloudflare link.",
  "set.access.toggleRemoteAccess": "Toggle remote access",
  "set.access.networkPassword": "network password",
  "set.access.username": "Username",
  "set.access.thisNetworkAsksFor": "this network asks for one",
  "set.access.username2": "username",
  "set.access.joinNetwork": "Join network",
  "set.access.pickANetworkAbove": "Pick a network above, type its password, then join. If you are opening the dashboard on the home network, the page will drop for a few seconds while sudo moves — it comes back on the new address.",
  "set.access.aboutAccessFromAnywhere": "About access from anywhere",
  "set.access.normallyYourDashboardOnly": "Normally your dashboard only works on your home Wi‑Fi. Turn this on and sudo creates a public web address that reaches your device from anywhere — useful on the go.<br><br><strong>Treat that link like a password.</strong> There's no login screen: anyone who has it can chat with your agent and change these settings. Don't post it or send it to anyone you wouldn't hand your unlocked phone to.<br><br>The link changes when your device restarts. Turn this off when you don't need it.",
  "set.access.copy": "Copy",
  "set.access.passwordForThePublic": "password for the public link",
  "set.access.askForAPassword": "Ask for a password",
  "set.access.appliesToThePublic": "Applies to the public link only. Your home network never asks.",
  "set.access.requireAPasswordOn": "Require a password on the public link",
  "set.access.changeIt": "Change it",
  "set.access.atLeast10Characters": "at least 10 characters",
  "set.access.savePassword": "Save password",
  "set.access.stronglyRecommendedLeaveThis": "<strong>Strongly recommended: leave this on.</strong> With it off, the public link is a web address that opens your dashboard for anyone who has it — no login, no limit on attempts, from anywhere in the world. They could read your chats, change your settings, and use your API credit. Links get shared, logged by services you paste them into, and forwarded by accident.",
  "set.access.localDashboardHttpRaspberrypi": "Local dashboard: <strong>http://raspberrypi.local</strong>",
  "set.access.wiFiRegion": "Wi‑Fi region",
  "set.access.autoDetectedAtSetup": "Auto-detected at setup. Controls which Wi‑Fi channels the device is allowed to use — only change this if you've moved it to a different country.",
  "set.ssh.developerAccess": "Developer access",
  "set.ssh.terminalAccessSsh": "Terminal access (SSH)",
  "set.ssh.logIntoThisDevice": "Log into this device from your computer and work on it directly.",
  "set.ssh.toggleTerminalAccess": "Toggle terminal access",
  "set.ssh.aboutTerminalAccess": "About terminal access",
  "set.ssh.itSYourDevice": "It's your device, so you can have full access to it. This opens a way to log in from a computer on your home Wi‑Fi and use it like any other machine — install things, read files, change how sudo works.<br><br><strong>You don't need this for normal use.</strong> Everything the device is meant to do, it does from this dashboard.<br><br><strong>Home Wi‑Fi only.</strong> This is never reachable from the internet, even with <em>Access from anywhere</em> switched on.<br><br>Changes you make by hand are undone if the device is ever re‑flashed.",
  "set.ssh.aboutThisPassword": "About this password",
  "set.ssh.generatedJustNowFor": "Generated just now for your device's user account. Copy it somewhere safe — you can always come back here to see it again while you're on your home Wi‑Fi.",
  "set.ssh.openThisPageOn": "Open this page on your home Wi‑Fi to see the password — it isn't shown over the public link.",
  "set.ssh.preferToUseYour": "Prefer to use your own SSH key?",
  "set.ssh.aboutSshKeys": "About SSH keys",
  "set.ssh.anSshKeyIs": "An SSH key is a pair of files on your computer — a private half that never leaves it, and a public half that's safe to share. You put the public half here, and your computer proves it holds the private half when it connects. No password to type or remember.<br><br><strong>If you don't have one</strong>, skip this — the password above works fine. To make one, run <code>ssh-keygen -t ed25519</code> on your computer, then paste the contents of <code>~/.ssh/id_ed25519.pub</code>.",
  "set.ssh.useThisKeyInstead": "Use this key instead",
  "set.ssh.aboutClaudeCode": "About Claude Code",
  "set.ssh.claudeCodeIsAn": "Claude Code is an AI assistant that runs in a terminal and can read and change the files on this device — handy if you want to customise how sudo works.<br><br>We don't ship it preinstalled, so the device stays small and you always get the current version. Installing takes a few minutes and needs internet. You can watch the progress right here.",
  "set.ssh.installClaudeCode": "Install Claude Code",
  "set.ssh.installedOpenATerminal": "Installed. Open a terminal on your computer and run these:",
  "set.ssh.openaiSTerminalAssistant": "OpenAI's terminal assistant. Same idea, different provider — you can have both.",
  "set.ssh.installCodex": "Install Codex",
  "set.github.giveSudoAGithub": "Give sudo a GitHub token and it can work with your repositories directly — cloning one, pushing changes, opening an issue — instead of only talking you through it.",
  "set.github.githubUsername": "GitHub username",
  "set.github.optional": "optional",
  "set.github.yourHandle": "your-handle",
  "set.github.personalAccessToken": "Personal access token",
  "set.github.ghpOrGithubPat": "ghp_… or github_pat_…",
  "set.github.createOneAtGithub": "Create one at <strong>github.com → Settings → Developer settings → Personal access tokens</strong>. Choose only the scopes sudo needs; a fine-grained token limited to the repos you care about is the safest. Stored on this device only.",
  "set.github.saveToken": "Save token",
  "set.github.letSudoUseMy": "Let sudo use my repos",
  "set.github.whenOffTheToken": "When off, the token is saved but sudo cannot reach GitHub.",
  "set.github.whatTheTokenCan": "What the token can do ⓘ",
  "set.github.aTokenIsA": "A token is a key to your GitHub account. What it can reach is exactly what you ticked when you made it — repo access lets sudo clone, push and open issues; nothing else.<br><br><strong>Treat it like a password.</strong> Keep it off unless you want sudo working in your repos, and revoke it on GitHub the moment you are done. It is stored on this device only and never leaves it except to talk to GitHub.",
  "settings.billingCredits": "billing & credits",
  "set.billing.billingCredits": "Billing & credits",
  "set.billing.donTWantTo": "<strong>Don't want to deal with API keys?</strong> Top up here and we handle the routing for you — no accounts to sign up for, no keys to paste, no per-provider billing to keep track of.",
  "set.billing.usdCreditsAvailable": "USD credits available",
  "set.billing.startSudoPro10": "Start Sudo Pro · $10/month",
  "set.billing.includesA7Day": "Includes a 7-day trial. Checkout opens securely in Stripe.",
  "settings.reset": "reset",
  "set.reset.resetSetup": "Reset setup",
  "set.reset.backToABrand": "Back to a brand-new device",
  "set.reset.putsThisDeviceBack": "Puts this device back to how it was out of the box: your home Wi‑Fi is forgotten and the <strong>RaspiSetup</strong> hotspot comes back, so the next boot walks the whole setup flow from the start.",
  "set.reset.thisErasesYourSetup": "<strong>This erases your setup.</strong> Your name, the agent's name and personality, its model, your saved API keys, and the agent's memory all go. Terminal access and Claude Code are <em>kept</em>, so you can still get back in to test. To go back to a fresh device you must run setup again.",
  "set.reset.resetThisDevice": "Reset this device…",
  "set.reset.lastChanceThisReboots": "Last chance — this reboots the device and cannot be undone from here.",
  "set.reset.typeResetToConfirm": "Type RESET to confirm",
  "set.reset.resetAndReboot": "Reset and reboot",
  "set.reset.cancel": "Cancel",
  "settings.sudoV04Running": "sudo · v0.4 · running on your Raspberry Pi",
  "bnav.setup": "setup",
  "bnav.chat": "chat",
  "bnav.channels": "channels",
  "bnav.apps": "apps",
  "bnav.connect": "connect",
  "bnav.settings": "settings",
  "set.routing.optDefault": "DeepSeek V4 Flash · default",
  "set.routing.optFast": "GPT-4o mini · fast",
  "set.routing.optBudget": "Claude Haiku 4.5 · balanced",
  "set.routing.optSmart": "Claude Sonnet 4.5 · smart",
  "nav.sudoHome": "Sudo home",
});

// ---- English: strings built by the page script ---------------------------
I18N.add('en', {
  "nav.sudoHome": "Sudo home",
  "top.online": "online",
  "top.connectionIssue": "connection issue",
  "rail.active": "Session active",
  "rail.offline": "Offline",

  "common.saving": "Saving…",
  "common.saved": "Saved.",
  "common.savedTick": "Saved ✓",
  "common.save": "Save",
  "common.checking": "Checking…",
  "common.starting": "Starting…",
  "common.removing": "Removing…",
  "common.unlinking": "Unlinking…",
  "common.turningOn": "Turning on…",
  "common.turningOff": "Turning off…",
  "common.off": "Off",
  "common.copied": "copied ✓",
  "common.copyPrompt": "Copy this link:",
  "common.pasteKeyFirst": "Paste a key first.",
  "common.friend": "friend",

  "api.failed": "Request failed ({status})",
  "api.timeout": "That took too long. Check you are still on the same Wi-Fi, then try again.",
  "api.network": "Can’t reach your Sudo right now. Check you are on the same Wi-Fi, then try again.",
  "srv.invalidPassword": "That password isn’t right.",
  "srv.tooManyAttempts": "Too many tries. Wait a few minutes, then try again.",
  "srv.passwordRequired": "This needs the password.",
  "srv.invalidRequest": "Something went wrong sending that. Try again.",
  "srv.notFound": "Not found.",

  "chat.you": "you",
  "chat.stillThinking": "still thinking",
  "chat.firstStart": "this can take a minute — probably starting up for the first time",
  "chat.slowReply": "That is taking longer than expected — the agent may be starting up for the first time. It is still working; try again shortly.",
  "chat.noResponse": "(no response)",
  "chat.freshStart": "fresh start. what are we working on?",

  "greet.noProfile": "hey — let’s make this box yours. start in settings, then come back and tell me what you need.",
  "greet.first": "Hi {name}, I'm {agent} — good to meet you. Tell me a bit about what you do and I'll figure out where I can help.",
  "greet.back": "hey {name} — {agent} here. what are you working on?",
  "greet.there": "there",

  "welcome.stepOf": "Step {n} of {total}",
  "welcome.settingUp": "Setting up…",
  "how.ios.title": "On your iPhone or iPad",
  "how.ios.s1": "Tap the Share button at the bottom of Safari",
  "how.ios.s2": "Scroll down and tap “Add to Home Screen”",
  "how.ios.s3": "Tap Add — I’ll appear with your other apps",
  "how.ios.note": "Safari only. Chrome on iOS can’t add to the home screen.",
  "how.android.title": "On your Android phone",
  "how.android.s1": "Open the browser menu (the three dots)",
  "how.android.s2": "Tap “Add to Home screen” or “Install app”",
  "how.android.s3": "Confirm — I’ll appear with your other apps",
  "how.android.note": "",
  "how.tablet.title": "On your tablet",
  "how.tablet.s1": "Open your browser menu",
  "how.tablet.s2": "Choose “Add to Home screen” or “Install”",
  "how.tablet.s3": "Confirm to pin me",
  "how.tablet.note": "",
  "how.desktop.title": "On this computer",
  "how.desktop.s1": "Right-click this page’s tab at the top of the browser",
  "how.desktop.s2": "Choose “Pin”",
  "how.desktop.s3": "That’s it — I stay open, one click away",
  "how.desktop.note": "Want a standalone app window instead? In Chrome: ⋮ → Cast, save and share → “Install page as app”. Safari: File → Add to Dock. Firefox: just bookmark this page.",
  "hint.ios": "Tap the Share button, then “Add to Home Screen”.",
  "hint.other": "Open your browser menu, then “Add to Home screen”.",
  "login.enter": "Enter the password.",

  "keys.pasteFirst": "Paste your OpenRouter key first, or pick Sudo credits.",
  "keys.savedRestarting": "Saved. Your agent is restarting with the new key.",
  "keys.endsIn": "saved key ends in {hint}",
  "keys.none": "no key saved",

  "ssh.summaryKey": "Terminal on · using your key",
  "ssh.summaryPassword": "Terminal on · password",
  "ssh.summaryOff": "Terminal off",
  "ssh.isOn": "Terminal access is on.",
  "ssh.isOff": "Terminal access is off.",
  "ssh.pasteKeyFirst": "Paste your public key first.",
  "ssh.keySaved": "Key saved. Password login is now off.",
  "dev.installing": "Installing…",
  "dev.reinstall": "Reinstall / repair",
  "dev.install": "Install {name}",

  "files.none": "Nothing yet — these appear once your agent is set up.",
  "files.summary.one": "{n} file · identity and memory",
  "files.summary.other": "{n} files · identity and memory",
  "files.summaryDefault": "Identity, notes and memory",

  "spend.summaryOn": "On · {n} messages/day",
  "spend.live": "{used} of {cap} messages used today.",
  "spend.livePaused": "Paused right now — clears on its own. {used} of {cap} used today.",
  "spend.waiting": "Waiting for a count…",

  "wa.connected": "connected",
  "wa.notConnected": "not connected",
  "wa.tipPhone": "You're doing this from a phone — easier from a computer, where you can point this phone's camera at the screen instead.",
  "wa.tipComputer": "Tip: open this page on a computer, then scan with your phone.",
  "wa.bridgeScan": "On your phone: <strong>WhatsApp → Settings → Linked Devices → Link a Device</strong>, then scan this.",
  "wa.pairingCode": "WhatsApp pairing code",
  "wa.connectedTitle": "Connected",
  "wa.connectedAs": "Connected as {number}",
  "wa.unlink": "Unlink",
  "wa.error": "Something went wrong setting this up. Try again from Settings.",
  "wa.owner.title": "Your WhatsApp",
  "wa.owner.scanWith": "your own phone",
  "wa.owner.how": "Talk to your agent in your “Message yourself” chat. It never messages your contacts.",
  "wa.agent.title": "Agent’s own number",
  "wa.agent.scanWith": "the phone with your agent’s SIM",
  "wa.agent.how": "Your agent gets its own WhatsApp contact and messages you from it. It only answers you.",
  "wa.agent.needs": "You’ll need a second SIM or phone number that you own. Put it in a spare phone, create a new WhatsApp account on that number for your agent, then scan the code below from that phone.",
  "wa.setupStopped": "Setup stopped. Tap Set up to try again.",
  "wa.settingUp": "Setting up WhatsApp…",
  "wa.moveHead": "Easier on a computer",
  "wa.move": "You’ll scan a QR code with your phone’s camera, so open this page on a computer first, then come back to Channels → WhatsApp there.",
  "wa.moveHost": "You’ll scan a QR code with your phone’s camera, so open this page on a computer first — go to {host} — then come back to Channels → WhatsApp there.",
  "wa.linkIntro": "Link one or both. With both, your agent messages you from its own number.",
  "wa.linked": "Linked",
  "wa.linkedAs": "Linked as {number}",
  "wa.notLinked": "Not linked",
  "wa.ownNumber": "Your own WhatsApp number",
  "wa.ownNumberHint": "So your agent knows it’s you messaging it. With country code.",
  "wa.scanWith": "Scan with <strong>{who}</strong>: WhatsApp → Settings → Linked Devices → Link a Device.",
  "wa.pairingAlt": "{title} pairing code",
  "wa.showQr": "Show QR code",
  "wa.gettingCode": "Getting a code…",

  "local.ready": "Ready",
  "local.downloading": "Downloading…",
  "local.failed": "Could not install",
  "local.absent": "Not installed yet",
  "local.doneNote": "Answers even with no key and no internet.",
  "local.pendingNote": "Downloads on its own once the device is online.",
  "local.staying": "Staying on this device.",
  "local.usingCloud": "Using the cloud when a key is saved.",

  "update.version": "Version {v}",
  "update.upToDate": "up to date",
  "update.available": "update available",
  "update.error": "error",
  "update.checking": "checking",
  "update.downloading": "downloading",
  "update.applying": "applying",
  "update.updated": "updated",
  "update.starting": "Starting update…",

  "reset.typeIt": "Type RESET to confirm.",
  "reset.resetting": "Resetting — the device will reboot and vanish from this Wi-Fi.",
  "reset.done": "Done. When this page stops loading, join the “RaspiSetup” network and open http://192.168.4.1/",

  "ready.failed": "Having trouble installing my on-device brain. Add an API key in Settings so I can chat while that gets sorted out.",
  "ready.settingUp": "Setting up my on-device brain…",
  "ready.firstReplies": "First replies can take a few minutes.",
  "ready.still": "Still setting up my on-device brain — first replies can take a few minutes. I'll be ready shortly, no need to do anything.",

  "routing.fixed": "Fixed",
  "routing.auto": "Auto",
  "routing.chipAria": "Model: {name}",

  "prov.hint.openrouter": "openrouter.ai — one key, most models",
  "prov.hint.openai": "platform.openai.com — GPT models",
  "prov.hint.anthropic": "console.anthropic.com — Claude models",
  "prov.hint.gemini": "aistudio.google.com — Gemini models",
  "prov.hint.venice": "venice.ai — private, uncensored models",
  "prov.hint.deepseek": "platform.deepseek.com — direct, cheapest",
  "prov.savedTag": "saved ····{hint}",
  "prov.replacePlaceholder": "saved — paste a new key to replace",
  "prov.remove": "Remove this key",
  "prov.saveKeys": "Save keys",
  "prov.keysSaved.one": "{n} key saved",
  "prov.keysSaved.other": "{n} keys saved",
  "prov.usingCredits": "Using sudo credits",
  "prov.savedAs": "Saved as {name}.",
  "prov.removing": "Removing {name}…",
  "prov.removed": "{name} key removed.",

  "rpw.homeOnly": "Only shown on your home network.",
  "rpw.hidden": "Hidden here because you are on the public link. Open the dashboard at home to see it.",
  "rpw.notSet": "not set yet",
  "rpw.setAtBoot": "Set at first boot. Change it below if you like.",
  "rpw.setOneFirst": "Set one below before switching the password on.",
  "rpw.tooShort": "Use at least 10 characters.",
  "rpw.changed": "Password changed.",
  "rpw.willAsk": "The public link will ask for the password.",
  "rpw.nowOpen": "The public link is now open to anyone who has it.",

  "host.nameFirst": "Give the device a name first.",
  "host.invalid": "Letters, numbers and hyphens only — no spaces or dots, and it cannot start or end with a hyphen.",
  "host.renaming": "Renaming…",
  "host.renamed": "Renamed. From now on open <strong>{url}</strong><br><br><em>{old}</em> will stop working. If this page goes blank, that is why — open the new address.",
  "host.same": "That is already its name.",
  "access.summaryLocal": "{host} · no password",
  "access.starting": "Starting public link…",
  "access.ready": "Public link ready",

  "composio.savedOn": "Key saved — connectors on.",
  "composio.savedLater": "Key saved — connectors switch on with this device’s next big update.",
  "composio.manage": "Manage your connected apps on Composio →",
  "composio.signUp": "New to Composio? Sign in or create a free account →",
  "composio.removed": "Key removed — connectors off.",

  "github.pasteFirst": "Paste a token first.",
  "github.tokenEnds": "saved token ends in {hint}",
  "github.noToken": "no token saved",
  "github.notConnected": "Not connected",
  "github.connected": "Connected",
  "github.savedOff": "Token saved · off",

  "wifi.unknown": "unknown",
  "wifi.unavailable": "Wi-Fi not available on this device",
  "wifi.notConnected": "Not connected",
  "wifi.none": "No networks found. Move the device nearer the router and try again.",
  "wifi.open": "open",
  "wifi.enterprise": "enterprise",
  "wifi.secured": "secured",
  "wifi.noPassword": "no password needed",
  "wifi.selected": "Selected {ssid}.",
  "wifi.scanning": "Scanning for networks…",
  "wifi.pickFirst": "Pick a network from the list first.",
  "wifi.joining": "Joining…",
  "wifi.joiningSsid": "Joining {ssid}. This page may drop for a few seconds.",

  "setup.whatsapp": "WhatsApp",
  "setup.nextCloud": "connect a cloud model",
  "setup.nextWhatsapp": "connect WhatsApp",
  "setup.nextApps": "connect your apps",
  "setup.navAria.one": "Finish setup, {n} step left",
  "setup.navAria.other": "Finish setup, {n} steps left",
  "setup.count": "{count} of {total} done",
  "setup.next": "Next: {step}",
  "setup.allThree": "All three done. Nice work.",
  "setup.lede": "Three steps turn sudo from a friendly chat into a real helper. Once all three are done, this guide moves to the book icon at the top.",
  "setup.ledeHub": "How your agent works — models and costs, WhatsApp, and your apps. Come back any time.",
  "setup.done": "Done",
  "setup.stateDone": "done",
  "setup.stateTodo": "to do",
  "setup.trackAria": "Step {n}, {label}: {state}",
  "prompt.minimax": "Please switch to the new MiniMax model",
  "prompt.dailyTodo": "Send me my daily to-do on my WhatsApp",
  "prompt.shopify": "Please help me connect to my Shopify store",
  "apps.buildPrompt": "Help me build an app that ",

  "abil.web": "Web search on",
  "abil.cmdOn": "commands on",
  "abil.cmdOff": "commands off",
  "abil.helpers": "helpers on",
  "abil.skills": "skills on",
  "abil.full": "full access on",
  "abil.speaks": "speaks up",

  "billing.trial": "trial active",
  "billing.pastDue": "payment due",
  "billing.notSubscribed": "not subscribed",
  "billing.summary": "{plan} · {amount} available",
  "billing.loadFailed": "Could not load plan",
  "billing.confirming": "Confirming payment…",

  "lang.title": "Language",
  "lang.pickLabel": "Dashboard language",
  "lang.dashHead": "dashboard language",
  "lang.dashIntro": "The words on these screens. Changes right away, on every phone and computer that opens this dashboard.",
  "lang.agentHead": "agent’s reply language",
  "lang.agentIntro": "The language {agent} answers you in. This only changes the agent’s replies, not this dashboard. You can always ask it for another language right in the chat.",
  "lang.agentAuto": "Match how I write to it",
  "lang.agentAutoNote": "Replies in whatever language you use",
  "lang.agentAutoShort": "replies match you",
  "lang.agentFixedShort": "replies in {name}",
  "lang.summary": "{ui} · {agent}",
  "lang.savedEverywhere": "Saved. Every screen that opens this dashboard will use it.",
});
