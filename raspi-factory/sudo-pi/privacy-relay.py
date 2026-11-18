#!/usr/bin/env python3
"""privacy-relay -- keeps names and numbers on this sudo.

Sits between OpenClaw and the cloud model (OpenRouter, DeepSeek) on
127.0.0.1. Before a request leaves, it swaps the owner's contacts' names,
phone numbers, email addresses, card and bank numbers for stand-ins like
[PERSON_3] or [PHONE_2]. The model works with the stand-ins; on the way back
the relay puts the real values in again, in the reply text and in tool calls
(so "message [PERSON_3]" still reaches the right person). Nothing is
guessed by an AI model here: names come from the owner's own contacts and
the owner's name, numbers from plain patterns. A few thousandths of a second
per request.

The stand-ins never change for a value (kept in MAP_FILE), so the same
conversation looks the same to the model each time and its prompt cache keeps
working.

What it does not catch: a name that is not in the owner's contacts, an
address, anything described in words. The dashboard says so.

Turned off by the owner (config privacy_scrub: false), it forwards untouched.
Listens on 127.0.0.1 only.
"""
import copy
import http.client
import http.server
import json
import os
import re
import socketserver
import threading
import time

LISTEN = ("127.0.0.1", 18792)
UPSTREAMS = {"openrouter": "openrouter.ai", "deepseek": "api.deepseek.com"}
TEST_UPSTREAM = os.environ.get("PRIVACY_RELAY_TEST_UPSTREAM")   # host:port, plain http (tests only)
CONFIG = "/opt/sudo/config.json"
WORKSPACE = "/opt/sudo/agent-workspace"
PEOPLE_DIR = os.path.join(WORKSPACE, "knowledge", "people")
WA_CONTACTS = "/opt/sudo/whatsapp-contacts.json"
MAP_FILE = "/opt/sudo/privacy-map.json"

DROP_REQUEST = {"host", "content-length", "accept-encoding", "connection", "keep-alive",
                "transfer-encoding", "te", "upgrade", "proxy-connection"}
DROP_RESPONSE = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding",
                 "server", "cf-ray", "set-cookie", "alt-svc", "nel", "report-to"}
# Only what is said is scrubbed: not model names, ids or settings.
SCRUB_KEYS = {"messages", "system", "input", "prompt", "instructions", "contents"}
# Text that arrives in pieces; a stand-in can be cut between two pieces.
STREAM_KEYS = {"content", "text", "arguments", "partial_json", "reasoning", "reasoning_content",
               "thinking", "delta", "refusal"}
JSON_TEXT_KEYS = {"arguments", "partial_json"}   # tool input: JSON inside a string
KINDS = ("PERSON", "PHONE", "EMAIL", "CARD", "IBAN")
TOKEN_RE = re.compile(r"\[(PERSON|PHONE|EMAIL|CARD|IBAN)_(\d{1,5})(?:_([a-z]{1,2}))?\]")
LOOSE_RE = re.compile(r"(?<![\w\[])(PERSON|PHONE|EMAIL|CARD|IBAN)_(\d{1,5})(?:_([a-z]{1,2}))?(?![\w\]])")
# Labels of the conversation, not what was said: a contact called "Read" must
# not rename the read tool.
NOT_TEXT_KEYS = {"role", "name", "id", "type", "tool_call_id", "tool_use_id", "call_id", "model"}

EMAIL_RE = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}(?![\w-])")
CARD_RE = re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?![\w-])")
IBAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Za-z0-9])")
# A phone number either starts with + or is written with spaces, dashes or
# brackets. Bare digit runs are left alone: they are ids, timestamps and
# order numbers far more often than phone numbers, and changing them breaks
# tools.
PHONE_RE = re.compile(r"(?<![\w+])(?:\+\d[\d ()-]{6,18}\d|\(?\d{2,4}\)?[ -]\d{2,4}[ -]\d{3,5}(?:[ -]\d{2,5})?)(?![\w])")
DATE_RE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}-\d{1,2}-\d{4}\b")
# Contact names that are also everyday words are left out: scrubbing every
# "will" or "may" would garble what the agent reads.
COMMON = {
    "will", "may", "june", "april", "august", "mark", "bill", "grace", "hope", "joy", "faith", "rose",
    "summer", "dawn", "art", "rich", "frank", "sunny", "mom", "dad", "mum", "mama", "papa", "baby",
    "honey", "love", "home", "work", "office", "me", "you", "sudo", "the", "and", "chase", "page",
    "pat", "ray", "sky", "star", "king", "duke", "major", "max", "rob", "sue", "don", "guy", "jack",
    "jan", "jean", "lane", "lee", "long", "miles", "neil", "ruby", "sage", "terry", "victor", "young",
}

# Without this the model treats [PHONE_2] as something it was not allowed to
# see and refuses to use it (seen on a real device). Always the same words,
# so the prompt cache is unaffected.
NOTE = ("Privacy: some names, phone numbers, emails and card or bank numbers appear as stand-ins like "
        "[PERSON_3] or [PHONE_2]. Each stands for one real value that this device swaps back in "
        "automatically. Treat them as the real thing: repeat them, message them and pass them to tools "
        "exactly as written, and never say they are hidden or redacted.")

_lock = threading.Lock()


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def enabled():
    return read_json(CONFIG, {}).get("privacy_scrub", True) is not False


def luhn(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def iban_ok(text):
    s = text.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    s = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in s)) % 97 == 1
    except ValueError:
        return False


# --- The map: real value <-> stand-in ------------------------------------------
def alt_tag(i):
    """a, b ... z, aa, ab: which way of writing a value this stand-in is."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    return letters[i] if i < 26 else letters[i // 26 - 1] + letters[i % 26]


class Book:
    """Stand-ins that never change, and the owner's contacts' names."""

    def __init__(self):
        self.data = read_json(MAP_FILE, {})
        for kind in KINDS:
            self.data.setdefault(kind, {})
        self.reverse = {}
        for kind in KINDS:
            for key, entry in self.data[kind].items():
                self.reverse[(kind, entry["n"], "")] = entry["show"]
                for i, alt in enumerate(entry.get("alts") or []):
                    self.reverse[(kind, entry["n"], alt_tag(i))] = alt
        self.dirty = False
        self.names_sig = None
        self.name_re = None
        self.name_key = {}

    def token(self, kind, key, show):
        entry = self.data[kind].get(key)
        if not entry:
            n = max([e["n"] for e in self.data[kind].values()] or [0]) + 1
            entry = {"n": n, "show": show}
            self.data[kind][key] = entry
            self.reverse[(kind, n, "")] = show
            self.dirty = True
        if show == entry["show"]:
            return f"[{kind}_{entry['n']}]"
        # The same person or number written another way ("rosa", "Rosa Delgado",
        # "+1 555 123 4567") comes back exactly as it was written: a file name
        # or a phone number with the wrong spelling no longer works.
        alts = entry.setdefault("alts", [])
        if show not in alts:
            if len(alts) >= 702:
                return f"[{kind}_{entry['n']}]"
            alts.append(show)
            self.dirty = True
        tag = alt_tag(alts.index(show))
        self.reverse[(kind, entry["n"], tag)] = show
        return f"[{kind}_{entry['n']}_{tag}]"

    def save(self):
        if not self.dirty:
            return
        tmp = MAP_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, MAP_FILE)
            self.dirty = False
        except OSError as exc:
            log(f"could not save the map: {exc}")

    # Names: the owner, the people pages, the WhatsApp contacts list.
    def _people(self):
        people = []
        owner = str(read_json(CONFIG, {}).get("user_name") or "").strip()
        if owner:
            people.append(owner)
        try:
            files = sorted(n for n in os.listdir(PEOPLE_DIR) if n.endswith(".md"))
        except OSError:
            files = []
        for name in files:
            try:
                with open(os.path.join(PEOPLE_DIR, name), encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("# "):
                            people.append(line[2:].strip())
                            break
            except OSError:
                continue
        for c in read_json(WA_CONTACTS, {}).get("contacts") or []:
            if isinstance(c, dict) and c.get("name"):
                people.append(str(c["name"]).strip())
        return [p for p in people if 3 <= len(p) <= 60]

    def _signature(self):
        sig = []
        for path in (CONFIG, WA_CONTACTS, PEOPLE_DIR):
            try:
                sig.append(os.stat(path).st_mtime_ns)
            except OSError:
                sig.append(0)
        return tuple(sig)

    def names(self):
        sig = self._signature()
        if sig == self.names_sig:
            return self.name_re
        self.names_sig = sig
        self.name_key = {}    # what was written (lowercase) -> the person (full name, lowercase)
        self.name_show = {}   # the person -> how to write them back
        firsts = {}
        for full in self._people():
            canon = full.lower()
            if canon in COMMON:
                continue
            self.name_key[canon] = canon
            self.name_show.setdefault(canon, full)
            parts = full.split()
            if len(parts) > 1:
                firsts.setdefault(parts[0].lower(), set()).add(canon)
        for first, owners in firsts.items():
            # A first name stands for a person only if one contact has it.
            if len(owners) == 1 and len(first) >= 3 and first not in COMMON and first not in self.name_key:
                self.name_key[first] = next(iter(owners))
        if not self.name_key:
            self.name_re = None
        else:
            alts = sorted(self.name_key, key=len, reverse=True)
            # Not inside a path, file name or address: rosa.md stays rosa.md.
            self.name_re = re.compile(r"(?<![\w@/\\.-])(" + "|".join(re.escape(a) for a in alts)
                                      + r")(?![\w@/\\-]|\.\w)", re.IGNORECASE)
        return self.name_re

    # --- Out: hide --------------------------------------------------------------
    def hide(self, text):
        if len(text) < 3 or text.startswith("data:") or (len(text) > 4000 and " " not in text[:4000]):
            return text

        def email(m):
            v = m.group(0)
            return self.token("EMAIL", v.lower(), v)

        def card(m):
            v = m.group(0)
            digits = re.sub(r"\D", "", v)
            if digits == v and not (len(digits) in (15, 16) and digits[0] in "3456"):
                return v      # ids and timestamps are bare digits too
            if 13 <= len(digits) <= 19 and luhn(digits):
                return self.token("CARD", digits, v)
            return v

        def iban(m):
            v = m.group(0)
            return self.token("IBAN", v.replace(" ", ""), v) if iban_ok(v) else v

        def phone(m):
            v = m.group(0)
            digits = re.sub(r"\D", "", v)
            if not 8 <= len(digits) <= 15 or DATE_RE.search(v):
                return v
            key = ("+" if v.startswith("+") else "") + digits
            return self.token("PHONE", key, v)

        text = EMAIL_RE.sub(email, text)
        text = IBAN_RE.sub(iban, text)
        text = CARD_RE.sub(card, text)
        text = PHONE_RE.sub(phone, text)
        name_re = self.names()
        if name_re:
            def person(m):
                key = self.name_key.get(m.group(0).lower(), m.group(0).lower())
                return self.token("PERSON", key, m.group(0))
            text = name_re.sub(person, text)
        return text

    # --- In: put back -------------------------------------------------------------
    def show(self, text, json_text=False):
        if "_" not in text:
            return text

        def back(m):
            v = self.reverse.get((m.group(1), int(m.group(2)), m.group(3) or ""))
            if v is None:
                return m.group(0)
            return json.dumps(v, ensure_ascii=False)[1:-1] if json_text else v

        text = TOKEN_RE.sub(back, text)
        return LOOSE_RE.sub(back, text)


BOOK = None


def book():
    global BOOK
    if BOOK is None:
        BOOK = Book()
    return BOOK


def walk(obj, fn, path=()):
    if isinstance(obj, dict):
        return {k: walk(v, fn, path + (k,)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v, fn, path + (i,)) for i, v in enumerate(obj)]
    if isinstance(obj, str):
        return fn(obj, path)
    return obj


def last_key(path):
    for p in reversed(path):
        if isinstance(p, str):
            return p
    return ""


def add_note(body):
    """NOTE at the end of the first system message (chat format)."""
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return body
    first = msgs[0]
    if isinstance(first, dict) and first.get("role") in ("system", "developer"):
        content = first.get("content")
        if isinstance(content, str):
            first = {**first, "content": content + "\n\n" + NOTE}
        elif isinstance(content, list):
            first = {**first, "content": content + [{"type": "text", "text": NOTE}]}
        else:
            return body
        return {**body, "messages": [first] + msgs[1:]}
    return {**body, "messages": [{"role": "system", "content": NOTE}] + msgs}


def hide_request(body):
    with _lock:
        b = book()
        hide = lambda s, p: s if last_key(p) in NOT_TEXT_KEYS else b.hide(s)
        out = {k: (walk(v, hide, (k,)) if k in SCRUB_KEYS else v) for k, v in body.items()}
        b.save()
    return add_note(out)


def show_value(obj):
    with _lock:
        b = book()
        return walk(obj, lambda s, p: b.show(s, last_key(p) in JSON_TEXT_KEYS))


class StreamFixer:
    """Puts real values back into a streamed reply. A stand-in can arrive
    split over two pieces ("[PER" then "SON_3]"), so the end of a piece that
    could be the start of one is held back until the next piece."""

    def __init__(self):
        self.carry = {}      # path -> held text
        self.template = {}   # path -> last event that had text at that path

    @staticmethod
    def split_tail(text):
        i = text.rfind("[")
        if i == -1 or "]" in text[i:] or len(text) - i > 20:
            return text, ""
        return text[:i], text[i:]

    def event(self, obj):
        """The event with real values in; held text is carried over."""
        b = book()

        def fix(s, path):
            key = last_key(path)
            if key not in STREAM_KEYS:
                return b.show(s)
            held = self.carry.pop(path, "") + s
            ready, tail = self.split_tail(held)
            if tail:
                self.carry[path] = tail
            self.template[path] = obj
            return b.show(ready, key in JSON_TEXT_KEYS)

        with _lock:
            return walk(obj, fix)

    def flush(self):
        """Events carrying whatever is still held, before the stream ends."""
        out = []
        b = book()
        for path, text in list(self.carry.items()):
            tmpl = self.template.get(path)
            if tmpl is None or not text:
                continue
            ev = copy.deepcopy(tmpl)

            def put(node, keys):
                for k in keys[:-1]:
                    node = node[k]
                node[keys[-1]] = b.show(text, last_key(path) in JSON_TEXT_KEYS)

            def blank(s, p):
                return "" if (last_key(p) in STREAM_KEYS and p != path) else s

            ev = walk(ev, blank)
            try:
                put(ev, list(path))
                out.append(ev)
            except (KeyError, IndexError, TypeError):
                continue
        self.carry.clear()
        return out


def ends_stream(obj):
    if not isinstance(obj, dict):
        return False
    if obj.get("type") in ("message_stop", "message_delta", "content_block_stop", "response.completed",
                           "response.output_text.done"):
        return True
    for ch in obj.get("choices") or []:
        if isinstance(ch, dict) and ch.get("finish_reason"):
            return True
    return False


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Relay(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.relay()

    def do_POST(self):
        self.relay()

    def relay(self):
        parts = self.path.split("/", 2)
        host = UPSTREAMS.get(parts[1] if len(parts) > 1 else "")
        if not host:
            self.send_error(404)
            return
        upstream_path = "/" + (parts[2] if len(parts) > 2 else "")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else None
        scrub = enabled()
        if raw and scrub and "json" in (self.headers.get("Content-Type") or ""):
            try:
                body = json.loads(raw)
                if isinstance(body, dict):
                    raw = json.dumps(hide_request(body), ensure_ascii=False).encode("utf-8")
            except ValueError:
                pass
            except Exception as exc:     # noqa: BLE001 -- never block a request on a scrub bug
                log(f"scrub failed, sending as is: {exc}")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_REQUEST}
        headers["Accept-Encoding"] = "identity"
        if raw is not None:
            headers["Content-Length"] = str(len(raw))
        conn = (http.client.HTTPConnection(TEST_UPSTREAM, timeout=600) if TEST_UPSTREAM
                else http.client.HTTPSConnection(host, timeout=600))
        try:
            conn.request(self.command, upstream_path, body=raw, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            log(f"{host} unreachable: {exc}")
            self.send_error(502, "Cloud model unreachable")
            return
        try:
            ctype = resp.getheader("Content-Type") or ""
            if "event-stream" in ctype:
                self.stream(resp, scrub)
            elif "json" in ctype and scrub:
                data = resp.read()
                try:
                    data = json.dumps(show_value(json.loads(data)), ensure_ascii=False).encode("utf-8")
                except ValueError:
                    pass
                self.send_response(resp.status)
                self.copy_headers(resp)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                data = resp.read()
                self.send_response(resp.status)
                self.copy_headers(resp)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()

    def copy_headers(self, resp):
        for name, value in resp.getheaders():
            if name.lower() not in DROP_RESPONSE:
                self.send_header(name, value)

    def chunk(self, data):
        if data:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

    def stream(self, resp, scrub):
        self.send_response(resp.status)
        self.copy_headers(resp)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        fixer = StreamFixer()
        pending = [None]     # an "event:" line waits for its data line
        named = [False]      # this stream names its events (Anthropic style)

        def emit_obj(obj, event_line=None):
            if event_line is not None:
                self.chunk(event_line)
            elif named[0] and isinstance(obj, dict) and obj.get("type"):
                self.chunk(b"event: " + str(obj["type"]).encode("utf-8") + b"\n")
            self.chunk(b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n")

        while True:
            line = resp.readline()
            if not line:
                break
            if not scrub:
                self.chunk(line)
                continue
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text.startswith("event:"):
                pending[0] = text.encode("utf-8") + b"\n"
                named[0] = True
                continue
            if not text.startswith("data:"):
                # SSE comments go as they are; the blank line after each
                # event is written with the event.
                if text.startswith(":"):
                    self.chunk(text.encode("utf-8") + b"\n\n")
                continue
            payload = text[5:].strip()
            event_line, pending[0] = pending[0], None
            if payload == "[DONE]":
                for ev in fixer.flush():
                    emit_obj(ev)
                if event_line:
                    self.chunk(event_line)
                self.chunk(b"data: [DONE]\n\n")
                continue
            try:
                obj = json.loads(payload)
            except ValueError:
                if event_line:
                    self.chunk(event_line)
                self.chunk(text.encode("utf-8") + b"\n\n")
                continue
            if ends_stream(obj):
                for ev in fixer.flush():
                    emit_obj(ev)
            emit_obj(fixer.event(obj), event_line)
        if scrub:
            for ev in fixer.flush():
                emit_obj(ev)
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main():
    server = ThreadingServer(LISTEN, Relay)
    log(f"privacy relay on {LISTEN[0]}:{LISTEN[1]}")
    server.serve_forever()


if __name__ == "__main__":
    main()
