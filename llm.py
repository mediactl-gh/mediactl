"""
llm — model prompts, chat, and tool-assisted requests

Usage:
  llm ask <prompt...> [--model NAME] [--system TEXT] [--attach FILE ...]
  llm chat [--model NAME] [--system TEXT] [--no-save]
  llm assist <prompt...> [--model NAME] [--system TEXT]
  llm assist --voice [--wake WORD] [--device NAME] [--model NAME] [--system TEXT]

Repeat --attach to supply multiple files.

Help: help llm | llm --help
"""

import argparse
import os
import json
import base64
import mimetypes
import io
import contextlib
import sys
from datetime import datetime

# ── Constants ───────────────────────────────────────────────────────────────

HISTORY_DIR = ".out/llm"
CONFIG_FILE = ".out/llm.json"
_PREFS_FILE = os.path.join(HISTORY_DIR, "prefs.md")
_INTERACTIONS = 0

DEFAULT_CONFIG = {
    "provider": "",
    "model": "",
    "api_key": "",
    "api_url": "",
    "tools": [],
}
DEFAULT_MAX_TOKENS = 60
DEFAULT_WAKE_WORD = "hey computer"
DEFAULT_MODELS = {
    "anthropic": "claude-3-5-haiku-latest",
    "openai": "gpt-4o-mini",
    "openrouter": "openrouter/free",
    "ollama": "llama3.2",
}
DEFAULT_API_URLS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "ollama": "http://localhost:11434/api/chat",
}
_IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_AUDIO_TYPES = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm"}

PROMPT_ASSISTANT = (
    "You are a home assistant. Reply in ONE short sentence, max 15 words. "
    "Speak naturally like a voice assistant. No preamble, no filler, no follow-up questions. "
    "Never invent device state or facts. Use tools to check or control things. "
    "To call a tool, use this exact format: <tool_call>cmd action</tool_call>"
    "If unsure, say so briefly."
)
PROMPT_COMPACT = (
    "Extract important facts and user preferences from these transcripts. "
    "Output a short reference note (5-10 lines) for a home assistant. "
    "Only include new or specific facts: preferred devices, routines, locations, tone. "
    "Do NOT repeat or rephrase existing prefs — only add what's new.\n\n"
    "Existing preferences:\n{existing}\n\nNew transcripts:\n{transcripts}"
)

# ── Config ──────────────────────────────────────────────────────────────────

def create_config():
    data = json.loads(json.dumps(DEFAULT_CONFIG))
    filename = os.path.join(os.path.dirname(__file__), CONFIG_FILE)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def _file_cfg():
    try:
        if not os.path.exists(CONFIG_FILE):
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
                f.write("\n")
        os.makedirs(HISTORY_DIR, exist_ok=True)
        if not os.path.exists(_PREFS_FILE):
            with open(_PREFS_FILE, "w", encoding="utf-8") as f:
                pass
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_cfg(updates):
    cfg = _file_cfg()
    cfg.update(updates)
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    return cfg

def _resolve(key, default=None, required=True):
    val = _file_cfg().get(key)
    if val is not None and val != "":
        return val
    if required:
        raise Exception(f"missing LLM config key: {key} in {CONFIG_FILE}")
    return default

def _provider():
    val = _resolve("provider", required=False)
    return str(val).lower() if val else "openrouter"

def _model():
    return str(_resolve("model", DEFAULT_MODELS.get(_provider()), required=False))

def _system():
    return str(_resolve("system", "", required=False))

def _max_tokens():
    val = _resolve("max_tokens", None, required=False)
    return int(val) if val is not None else None

def _api_key():
    p = _provider()
    if p in ("ollama", "openrouter"):
        return _resolve("api_key", required=False) or ""
    return _resolve("api_key")

def _api_url():
    return _resolve("api_url", DEFAULT_API_URLS.get(_provider()), required=False)

def _assistant_system(system=None):
    return system or _system() or build_context()

# ── Ops ─────────────────────────────────────────────────────────────────────

def ask(prompt, attachments=None, system=None, model=None, on_reply=None):
    """Single-shot ask. attachments: list of file paths."""
    content = _build_content(prompt, attachments) if attachments else prompt
    reply, trace = _request([{"role": "user", "content": content}],
                     system=system, model=model)
    _save_transcript(prompt, reply, system=system, trace=trace)
    print(f"  {reply}")
    if on_reply: on_reply(reply)
    return reply

def assist(prompt, system=None, model=None):
    """Personal assistance — reply trimmed to first sentence."""
    from logui import logui
    reply = ask(prompt, system=system, model=model)
    logui("llm.assist", prompt=prompt[:80], response=reply)
    return _voice_text(reply)

def assist_voice(wake_word=None, system=None, model=None,
                 device=None, follow_up=8):
    """Hands-free assistant: wake word → listen → assist → speak. Loops until Ctrl+C."""
    import audio
    from logui import logui

    ww = _wake_word(wake_word)

    def _on_transcribe(text):
        text = (text or "").strip()
        if not text:
            return
        logui("llm.voice.transcribe")
        logui("llm.voice.think", heard=text[:80])
        try:
            reply = assist(text, system=system, model=model)
        except Exception as e:
            logui.err("llm.voice.error", stage="assist", msg=str(e)); return
        spoken = _voice_text(reply)
        if spoken:
            logui("llm.voice.speak", reply=spoken[:80])
            try:
                audio.speak(spoken)
            except Exception as e:
                logui.err("llm.voice.error", stage="speak", msg=str(e))

    logui("llm.voice", state="start", wake=ww or "(none)")
    try:
        audio._load_whisper()
        if ww:
            audio._ensure_wake_model()
            logui("llm.voice", state="wake", word=ww)
            audio.wake(word=ww, device=device)
        # One phrase per call keeps the microphone closed during our spoken reply.
        while True:
            audio.listen(on_transcribe=_on_transcribe, device=device,
                         timeout=follow_up, quiet=True)
    except KeyboardInterrupt:
        logui.print()
    finally:
        logui("llm.voice", state="stop")

def chat(system=None, model=None, save=True, on_input=None, on_reply=None):
    """Interactive multi-turn session. @path to attach files. type 'exit' to quit."""
    import re
    model    = model  or _model()
    system   = system or _system()
    history  = []
    lines    = []
    ts_start = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"  chat  model:{model}  type 'exit' to quit\n")

    try:
        while True:
            try:
                raw = input("  you: ").strip()
            except EOFError:
                break
            if not raw: continue
            if raw.lower() in ("exit", "quit", "bye"): break
            if on_input:
                raw = on_input(raw) or raw

            attach_paths = re.findall(r'@(\S+)', raw)
            prompt       = re.sub(r'@\S+\s*', '', raw).strip()
            if not prompt and attach_paths:
                prompt = "Describe the attached content."

            try:
                content = _build_content(prompt, attach_paths) if attach_paths else prompt
            except Exception as e:
                print(f"  ERROR: {e}"); continue

            history.append({"role": "user", "content": content})
            lines.append(f"you: {raw}")

            try:
                reply, _ = _request(history, system=system, model=model)
            except Exception as e:
                print(f"  ERROR: {e}")
                history.pop(); lines.pop()
                continue

            history.append({"role": "assistant", "content": reply})
            lines.append(f"llm: {reply}")
            print(f"  llm: {reply}\n")
            if on_reply: on_reply(reply)

    except KeyboardInterrupt:
        print()

    print("  session ended")
    if save and lines:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        path = os.path.join(HISTORY_DIR, f"chat_{ts_start}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"model: {model}\n\n")
            f.write("\n".join(lines))
        print(f"  saved: {path}")
    return history

# ── Context builder ─────────────────────────────────────────────────────────

def build_context():
    """Assemble system prompt from context: location, tools, memory."""
    parts = [PROMPT_ASSISTANT]
    try:
        w = json.loads(open(".out/weather.json", encoding="utf-8").read())
        loc = w.get("location", "")
        if loc: parts.append(f"Location: {loc}")
    except Exception: pass
    try:
        schema = _mediactl_schema()
        tools = []
        for cmd in sorted(schema):
            for action in sorted(schema[cmd].get("ops", {})):
                tools.append(f"{cmd} {action}")
        if tools: parts.append("Available commands:\n" + "\n".join(f"- {t}" for t in tools))
    except Exception: pass
    mem = _memory(3)
    if mem: parts.append(mem)
    return "\n\n".join(parts)

def _memory(n=3):
    """Last n chat transcripts + user preferences."""
    parts = []
    try:
        if os.path.exists(_PREFS_FILE):
            with open(_PREFS_FILE, encoding="utf-8") as f:
                p = f.read(500).strip()
            if p: parts.append(f"User preferences:\n{p}")
    except Exception: pass
    try:
        if not os.path.isdir(HISTORY_DIR): return "\n\n".join(parts)
        files = sorted(
            f for f in os.listdir(HISTORY_DIR)
            if f.startswith("chat_") and f.endswith(".txt")
        )[-n:]
        for fn in files:
            with open(os.path.join(HISTORY_DIR, fn), encoding="utf-8") as f:
                txt = f.read(600)
            parts.append(txt.strip())
    except Exception: pass
    return "\n\n".join(parts)

def compact_history():
    """Extract new facts from recent transcripts, append to prefs.md."""
    # read existing prefs
    existing = ""
    try:
        if os.path.exists(_PREFS_FILE):
            with open(_PREFS_FILE, encoding="utf-8") as f:
                existing = f.read(500).strip()
    except Exception: pass

    # gather recent transcripts
    transcripts = ""
    try:
        if not os.path.isdir(HISTORY_DIR): return
        files = sorted(
            f for f in os.listdir(HISTORY_DIR)
            if f.startswith("chat_") and f.endswith(".txt")
        )[-5:]
        for fn in files:
            with open(os.path.join(HISTORY_DIR, fn), encoding="utf-8") as f:
                transcripts += f.read(600) + "\n"
    except Exception: pass

    if not transcripts.strip(): return
    prompt = PROMPT_COMPACT.format(existing=existing or "(none)", transcripts=transcripts)

    try:
        reply = ask(prompt, system="Extract facts only. Output the updated preferences note.")
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(_PREFS_FILE, "w", encoding="utf-8") as f:
            f.write(reply)
    except Exception:
        pass

def _save_transcript(prompt, reply, system=None, trace=None):
    """Save exchange to chat log + debug log."""
    global _INTERACTIONS
    _INTERACTIONS += 1

    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        ts = datetime.now().strftime("%H:%M:%S")
        path = os.path.join(HISTORY_DIR, f"chat_{today}.txt")
        exists = os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if not exists:
                f.write(f"model: {_model()}\n\n")
            f.write(f"[{ts}] you: {prompt}\n")
            if trace:
                for t in trace:
                    f.write(f"[{ts}]   tool: {t.get('tool','')}  result: {t.get('result','')}\n")
            f.write(f"[{ts}] llm: {reply}\n\n")

        chats = sorted(f for f in os.listdir(HISTORY_DIR)
                       if f.startswith("chat_") and f.endswith(".txt"))
        while len(chats) > 3:
            os.remove(os.path.join(HISTORY_DIR, chats.pop(0)))
    except Exception: pass

    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        debug_path = os.path.join(HISTORY_DIR, "debug.txt")
        with open(debug_path, "a", encoding="utf-8") as f:
            f.write(f"{'='*60}\n")
            f.write(f"[{ts}] model: {_model()}\n")
            f.write(f"[{ts}] system:\n{system or _assistant_system()}\n")
            f.write(f"[{ts}] user: {prompt}\n")
            if trace:
                for t in trace:
                    f.write(f"[{ts}] tool: {t.get('tool','')}  result: {t.get('result','')}\n")
            f.write(f"[{ts}] response: {reply}\n")
            f.write(f"{'='*60}\n\n")
        with open(debug_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 1000:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.writelines(lines[-1000:])
    except Exception: pass

    if _INTERACTIONS == 1 or _INTERACTIONS % 3 == 0:
        try:
            compact_history()
        except Exception: pass

# ── Tool system ─────────────────────────────────────────────────────────────

def _mediactl():
    mod = sys.modules.get("__main__")
    if mod and all(hasattr(mod, n) for n in ("schema", "make_parser", "dispatch")):
        return mod
    mod = sys.modules.get("mediactl")
    if mod and all(hasattr(mod, n) for n in ("schema", "make_parser", "dispatch")):
        return mod
    import mediactl as mod
    return mod

def _mediactl_schema():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _mediactl().schema()
    return json.loads(buf.getvalue())

def _tool_name(op):
    return op.replace(".", "__").replace("-", "_")

def _tool_defs():
    whitelist = set(_file_cfg().get("tools", []))
    if not whitelist:
        return [], {}
    schema = _mediactl_schema()
    defs, mapping = [], {}
    for op in sorted(whitelist):
        if "." not in op: continue
        cmd, action = op.split(".", 1)
        info = schema.get(cmd, {}).get("ops", {}).get(action)
        if info is None: continue
        name = _tool_name(op)
        params = {
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": info.get("args", "positional arguments"),
            }
        }
        for flag in info.get("params", []):
            key = flag.lstrip("-").replace("-", "_")
            params[key] = {"type": "string", "description": flag}
        defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": f"Run mediactl {cmd} {action}.",
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "additionalProperties": False,
                },
            },
        })
        mapping[name] = (cmd, action, info.get("params", []))
    return defs, mapping

def _run_tool(name, raw_args, mapping):
    if name not in mapping:
        return f"ERROR: tool not allowed: {name}"
    cmd, action, flags = mapping[name]
    try:
        data = json.loads(raw_args or "{}")
    except Exception as e:
        return f"ERROR: invalid tool arguments: {e}"
    if not isinstance(data, dict):
        return "ERROR: tool arguments must be an object"
    argv = [cmd, action]
    argv.extend(str(x) for x in data.get("args", []) if x is not None)
    for flag in flags:
        key = flag.lstrip("-").replace("-", "_")
        val = data.get(key)
        if val is None or val is False: continue
        argv.append(flag)
        if val is not True: argv.append(str(val))
    buf = io.StringIO()
    try:
        parser = _mediactl().make_parser()
        parsed = parser.parse_args(argv)
        with contextlib.redirect_stdout(buf):
            _mediactl().dispatch(parsed)
    except (Exception, SystemExit) as e:
        return f"ERROR: {e}"
    return buf.getvalue().strip() or "OK"

def _parse_pseudo_tool_call(text):
    """Detect pseudo tool call formats, return (cmd, action, args) or None."""
    import re
    t = (text or "").strip()

    # XML-like tags
    m = re.search(
        r'(?:<tool_call>|<\|tool_call\|>)\s*(.*?)(?:</tool_call>|<\|tool_call_end\|>|<tool_call\|>)',
        t, re.S
    )
    if m:
        inner = re.sub(r'^call:', '', m.group(1).strip())
        args = {}
        brace = inner.find("{")
        if brace >= 0:
            depth = 0
            end = brace
            for i in range(brace, len(inner)):
                if inner[i] == "{": depth += 1
                elif inner[i] == "}": depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            try:
                args = json.loads(inner[brace:end])
                inner = inner[:brace].strip()
            except: pass
        parts = inner.split(None, 1)
        if len(parts) == 2: return (parts[0], parts[1], args)
        elif len(parts) == 1: return (parts[0], "", args)

    # JSON dict format
    try:
        data = json.loads(t)
        if isinstance(data, dict) and "command" in data:
            c = data["command"]
            name = c.get("name", "")
            args = c.get("arguments", {})
            parts = name.split(None, 1)
            if len(parts) == 2: return (parts[0], parts[1], args)
    except: pass

    return None

def _exec_pseudo_tool_call(text, tool_map):
    """Parse and execute a pseudo tool call. Returns (result, True) or (None, False)."""
    from logui import logui
    parsed = _parse_pseudo_tool_call(text)
    if not parsed:
        return None, False
    cmd, action, args = parsed
    op = f"{cmd}.{action}"
    logui("llm.tool", op=op, args=json.dumps(args) if args else "")
    name = _tool_name(op)
    if name in tool_map:
        return _run_tool(name, json.dumps(args), tool_map), True
    # fallback: execute directly
    try:
        argv = [cmd, action]
        for k, v in (args or {}).items():
            if k == "args":
                if isinstance(v, list): argv.extend(str(x) for x in v)
                else: argv.append(str(v))
            else:
                argv.append(f"--{k.replace('_','-')}")
                if v is not True and v is not False and v is not None:
                    argv.append(str(v))
        buf = io.StringIO()
        parser = _mediactl().make_parser()
        parsed_args = parser.parse_args(argv)
        with contextlib.redirect_stdout(buf):
            _mediactl().dispatch(parsed_args)
        return buf.getvalue().strip() or "OK", True
    except (Exception, SystemExit) as e:
        return f"ERROR: {e}", True

# ── Request layer ───────────────────────────────────────────────────────────

def _request(messages, system=None, model=None, max_tokens=None):
    """Send messages to provider. Returns (text, trace)."""
    provider   = _provider()
    model      = model      or _model()
    max_tokens = max_tokens or _max_tokens()
    system     = _assistant_system(system)
    api_key    = _api_key()

    if provider == "anthropic":
        if not api_key: raise Exception(f"API key not set - add api_key to {CONFIG_FILE}")
        return _request_anthropic(messages, system, model, max_tokens, api_key)
    elif provider in ("openai", "openrouter"):
        if not api_key: raise Exception(f"API key not set - add api_key to {CONFIG_FILE}")
        return _request_openai(messages, system, model, max_tokens, api_key, provider)
    elif provider == "ollama":
        return _request_ollama(messages, system, model, max_tokens)
    else:
        raise Exception(f"unknown provider: {provider}")

def _request_anthropic(messages, system, model, max_tokens, api_key):
    import requests as _req
    trace = []
    payload = {"model": model, "max_tokens": max_tokens or DEFAULT_MAX_TOKENS, "messages": messages}
    if system: payload["system"] = system
    resp = _req.post(_api_url(), headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }, json=payload, timeout=60)
    resp.raise_for_status()
    text = resp.json()["content"][0]["text"].strip()
    _, tool_map = _tool_defs()
    result, executed = _exec_pseudo_tool_call(text, tool_map)
    if executed:
        trace.append({"tool": text, "result": result})
        messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"Tool result:\n{result}"},
        ]
        payload["messages"] = messages
        resp = _req.post(_api_url(), headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, json=payload, timeout=60)
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
    return text, trace

def _request_openai(messages, system, model, max_tokens, api_key, provider="openai"):
    import requests as _req
    url = _api_url()
    trace = []

    def _convert(content):
        if isinstance(content, str): return content
        blocks = []
        for b in content:
            if b["type"] == "text":
                blocks.append({"type": "text", "text": b["text"]})
            elif b["type"] == "image":
                src = b["source"]
                blocks.append({"type": "image_url",
                    "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
            elif b["type"] == "document":
                blocks.append({"type": "text", "text": "[audio not supported]"})
        return blocks

    all_messages = []
    if system: all_messages.append({"role": "system", "content": system})
    for m in messages:
        all_messages.append({"role": m["role"], "content": _convert(m["content"])})

    payload = {"model": model, "messages": all_messages}
    if max_tokens is not None: payload["max_tokens"] = max_tokens
    tools, tool_map = _tool_defs()
    if tools: payload["tools"] = tools

    headers = {"content-type": "application/json"}
    if api_key: headers["Authorization"] = f"Bearer {api_key}"
    resp = _req.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]

    for _ in range(3):
        calls = msg.get("tool_calls") or []
        if not calls:
            content = (msg.get("content") or "").strip()
            result, executed = _exec_pseudo_tool_call(content, tool_map)
            if executed:
                trace.append({"tool": content, "result": result})
                all_messages.append({"role": "assistant", "content": content})
                all_messages.append({"role": "tool", "content": result})
                payload["messages"] = all_messages
                resp = _req.post(url, headers=headers, json=payload, timeout=60)
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                continue
            return content or "I did not get a usable response.", trace

        all_messages.append(msg)
        for call in calls:
            fn = call.get("function", {})
            result = _run_tool(fn.get("name", ""), fn.get("arguments", "{}"), tool_map)
            trace.append({"tool": fn.get("name", ""), "args": fn.get("arguments", "{}"), "result": result})
            all_messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": result})
        payload["messages"] = all_messages
        resp = _req.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]

    return (msg.get("content") or "").strip() or "I did not get a usable response.", trace

def _request_ollama(messages, system, model, max_tokens):
    import requests as _req
    url = _api_url()

    def _convert(content):
        if isinstance(content, str): return content, []
        text_parts, images = [], []
        for b in content:
            if b["type"] == "text": text_parts.append(b["text"])
            elif b["type"] == "image": images.append(b["source"]["data"])
            elif b["type"] == "document": text_parts.append("[audio not supported]")
        return " ".join(text_parts), images

    all_messages = []
    if system: all_messages.append({"role": "system", "content": system})
    for m in messages:
        text, images = _convert(m["content"])
        msg = {"role": m["role"], "content": text}
        if images: msg["images"] = images
        all_messages.append(msg)

    resp = _req.post(url, json={"model": model, "messages": all_messages, "stream": False}, timeout=120)
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip(), []

# ── Attachments ─────────────────────────────────────────────────────────────

def _encode_attachment(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMAGE_TYPES:
        mime = mimetypes.guess_type(path)[0] or f"image/{ext.lstrip('.')}"
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    if ext in _AUDIO_TYPES:
        mime = mimetypes.guess_type(path)[0] or f"audio/{ext.lstrip('.')}"
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        return {"type": "document", "source": {"type": "base64", "media_type": mime, "data": data}}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        contents = f.read()
    return {"type": "text", "text": f"[{os.path.basename(path)}]\n{contents}"}

def _build_content(prompt, attachments=None):
    blocks = []
    for path in (attachments or []):
        if not os.path.exists(path):
            raise Exception(f"attachment not found: {path}")
        blocks.append(_encode_attachment(path))
    blocks.append({"type": "text", "text": prompt})
    return blocks

# ── Voice helpers ───────────────────────────────────────────────────────────

def _voice_text(text):
    """Strip markup and trim to first sentence for TTS."""
    import re
    t = text or ""
    t = re.sub(r"<tool_call[\s\S]*?/>", " ", t)
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    m = re.match(r'^(.+?[.!?])(?:\s|$)', t)
    if m: t = m.group(1)
    return t

def _wake_word(param=None):
    if param is not None:
        return str(param).strip()
    saved = _file_cfg().get("wake_word")
    if saved: return str(saved).strip()
    _save_cfg({"wake_word": DEFAULT_WAKE_WORD})
    return DEFAULT_WAKE_WORD


def refresh():
    """Preload LLM: warm up the configured provider (best-effort)."""
    cfg = _file_cfg()
    if not cfg.get("provider"):
        return
    try:
        ask("", max_tokens=1)
    except Exception:
        pass




CLI_SCHEMA = {"ops": {
    "ask": {"args": "prompt", "params": ["--model", "--system", "--attach"]},
    "chat": {"params": ["--model", "--system", "--no-save"]},
    "assist": {"args": "prompt", "params": ["--model", "--system", "--voice", "--wake", "--device"]},
}}


def cli(argv):
    """Parse llm command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="llm", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"])
    p.add_argument("args", nargs="*")
    p.add_argument("--model",   default=None)
    p.add_argument("--system",  default=None)
    p.add_argument("--attach",  action="append", dest="attachments", default=[], metavar="FILE")
    p.add_argument("--no-save", action="store_true", dest="no_save")
    p.add_argument("--voice",   action="store_true", dest="voice")
    p.add_argument("--wake",    default=None, dest="wake")
    p.add_argument("--device",  default=None, dest="device")
    args = p.parse_args(argv)

    a = args.action
    try:
        if a == "ask":
            ask(" ".join(args.args),
                    attachments=args.attachments or None,
                    system=args.system, model=args.model)
        elif a == "chat":
            chat(system=args.system, model=args.model, save=not args.no_save)
        elif a == "assist":
            if args.voice:
                assist_voice(
                    wake_word=args.wake,
                    system=args.system, model=args.model,
                    device=args.device)
                return
            prompt = " ".join(args.args).strip()
            if not prompt:
                print("  ERROR: text required (or pass --voice)")
                return
            assist(prompt, system=args.system, model=args.model)
    except Exception as e:
        print(f"  ERROR: {e}")
