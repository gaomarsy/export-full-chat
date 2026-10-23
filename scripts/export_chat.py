#!/usr/bin/env python3
"""把当前 Claude Code 会话的原始记录 (jsonl) 导出成 /export 样式的 txt，从会话第一句到现在，跨过 compact。

两种模式：
  chatonly   只有对话：❯ 用户说的话、⏺ Claude 的回复
  wholechat  所有内容：对话之外，还有每次工具调用的完整输入和输出、思考、系统注入、子 agent 记录

用法：
  python3 export_chat.py chatonly            # 当前会话，存到当前目录
  python3 export_chat.py wholechat --tz +8   # 时间换成东八区
  python3 export_chat.py both --out-dir <目录>
"""
import argparse, datetime, glob, json, os, re, sys, unicodedata

W = 80

# ---------------- 找会话记录 ----------------
def find_session():
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid: sys.exit("读不到 CLAUDE_CODE_SESSION_ID，请在 Claude Code 会话里运行")
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
    if not hits: sys.exit(f"找不到会话 {sid} 的记录")
    return hits[0]

# ---------------- 隐去密钥 ----------------
KEY_PAT = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|sk_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
                     r"|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_\-]{30,}|xox[abpr]-[A-Za-z0-9\-]{10,}"
                     r"|tvly-[A-Za-z0-9_\-]{16,}|ark-[A-Za-z0-9_\-]{16,}|[0-9a-f]{32}\.[A-Za-z0-9]{16}"  # Tavily、火山方舟、智谱
                     r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})")  # 最后一种是 JWT
# 名字带 KEY / TOKEN / SECRET / PASSWORD 的赋值，等号或冒号后面的值隐去
ASSIGN_PAT = re.compile(r"(?i)([A-Za-z0-9_\-]*(?:api[_\-]?key|access[_\-]?key|token|secret|password|passwd)[A-Za-z0-9_\-]*[\"']?\s*[=:]\s*[\"']?)"
                        r"(?!\[密钥已隐去\])(?=[A-Za-z_\-.]*\d)([A-Za-z0-9_\-.+/=]{16,})(?![A-Za-z0-9_(])")  # 值要像密钥：16 位以上、带数字，不是函数调用
def collect_secrets(roots, extra):
    vals = set()
    files = list(extra) + glob.glob(os.path.expanduser("~/.config/*/env")) + glob.glob(os.path.expanduser("~/.config/*/*.env"))
    for root in roots:
        d = root
        while True:  # 从这个目录一直往上找 .env
            files += glob.glob(os.path.join(d, ".env*"))
            if os.path.dirname(d) == d: break
            d = os.path.dirname(d)
    for p in files:
        try:
            for l in open(p, errors="ignore"):
                if "=" in l and not l.lstrip().startswith("#"):
                    v = l.split("=", 1)[1].strip().strip("'\"")
                    if len(v) >= 12: vals.add(v)
        except OSError: pass
    for p in [c for root in roots for c in glob.glob(os.path.join(root, ".claude/skills/*/config.local.json"))]:
        try:
            def walk(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if isinstance(v, str) and re.search(r"key|token|secret", k, re.I) and len(v) >= 12: vals.add(v)
                        walk(v)
                elif isinstance(o, list):
                    for v in o: walk(v)
            walk(json.load(open(p)))
        except (OSError, ValueError): pass
    return vals
def make_cleaner(vals):
    def clean(s):
        for v in vals:  # 整串，以及被截断后只剩前半段的片段
            s = s.replace(v, "[密钥已隐去]")
            if len(v) >= 16: s = re.sub(re.escape(v[:16]) + r"[A-Za-z0-9_\-]*", "[密钥已隐去]", s)
        s = KEY_PAT.sub("[密钥已隐去]", s)
        return ASSIGN_PAT.sub(lambda m: m.group(1) + "[密钥已隐去]", s)
    return clean

# ---------------- 折行（中文算 2 列）----------------
def cw(ch): return 2 if unicodedata.east_asian_width(ch) in "WF" else 1
def dwidth(x): return sum(cw(c) for c in x)
def wrap(line, width):
    width = max(width, 10)
    res, cur, n = [], "", 0
    for tok in re.findall(r"[A-Za-z0-9_\-./:@%#&=?~`'\"()\[\]{}<>*+,;!|\\^$]+ ?|.", line):
        w = dwidth(tok)
        if n + w > width and cur:
            res.append(cur.rstrip()); cur, n = "", 0
            tok = tok.lstrip(); w = dwidth(tok)
        while w > width:  # 超长单词硬切
            i, acc = 0, 0
            while i < len(tok) and acc + cw(tok[i]) <= width: acc += cw(tok[i]); i += 1
            res.append(tok[:i]); tok = tok[i:]; w = dwidth(tok)
        cur += tok; n += w
    res.append(cur.rstrip())
    return res
def block(first, text, indent="  "):
    """first 是第一行前缀，其余行用 indent；代码块和目录树原样输出"""
    lines, incode = [], False
    for i, raw in enumerate(text.split("\n")):
        pre = first if i == 0 else indent
        if raw.strip().startswith("```"):
            incode = not incode; lines.append((pre + raw).rstrip()); continue
        if incode or re.search(r"[├└│┌┐┘┬┴┼]", raw) or raw.lstrip().startswith("|") or not raw.strip():
            lines.append((pre + raw).rstrip()); continue
        lead = re.match(r"\s*", raw).group(0)
        bullet = re.match(r"(- |\* |\d+\. )", raw.strip())
        hang = " " * len(bullet.group(1)) if bullet else ""
        parts = wrap(raw.strip(), W - dwidth(pre) - len(lead))
        if len(parts) > 1:  # 列表项折行后对齐到文字开头
            parts = [parts[0]] + wrap(raw.strip()[len(parts[0]):].strip(), W - len(indent) - len(lead) - len(hang))
        for j, p in enumerate(parts):
            lines.append(((pre if j == 0 else indent) + lead + (hang if j > 0 else "") + p).rstrip())
    return "\n".join(lines)
def verbatim(first, text, indent="     "):
    """工具输入输出：不折行，只加前缀"""
    return "\n".join((first if i == 0 else indent) + l for i, l in enumerate(text.split("\n"))).rstrip()

# ---------------- 渲染 ----------------
def text_of(c):
    if isinstance(c, str): return c
    out = []
    for y in c or []:
        if not isinstance(y, dict): continue
        if y.get("type") == "text": out.append(y.get("text", ""))
        elif y.get("type") == "image": out.append("[图片]")
        elif y.get("type") == "tool_reference": out.append(f"[工具 {y.get('tool_name', '')}]")
    return "\n".join(out)
def strip_reminders(s): return re.sub(r"<system-reminder>.*?</system-reminder>", "", s, flags=re.S).strip()

class Renderer:
    def __init__(self, mode, tz, max_out):
        self.mode, self.tz, self.max_out = mode, tz, max_out
        self.o, self.names = [], {}
    def ts(self, s):
        t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(self.tz)
        return t.strftime("%A, %b %-d at %-I:%M %p")
    def cut(self, s):
        if self.max_out and len(s) > self.max_out:
            return s[:self.max_out] + f"\n…（截断，原文共 {len(s)} 字符）"
        return s
    def add(self, s): self.o.append(s)

    def tool_call(self, x):
        name, inp = x["name"], x.get("input") or {}
        self.names[x["id"]] = name
        if self.mode == "chatonly": return
        # wholechat：展开
        if name == "Bash":
            head = f"⏺ Bash({inp.get('description', '')})" if inp.get("description") else "⏺ Bash"
            self.add(f"\n{head}\n" + verbatim("  $ ", inp.get("command", ""), "    ") + "\n")
        elif name == "Read":
            self.add(f"\n⏺ Read({inp.get('file_path')})" + "".join(f" {k}={v}" for k, v in inp.items() if k != "file_path") + "\n")
        elif name == "Write":
            self.add(f"\n⏺ Write({inp.get('file_path')})\n" + verbatim("  │ ", self.cut(inp.get("content", "")), "  │ ") + "\n")
        elif name == "Edit":
            body = "\n".join(["- " + l for l in inp.get("old_string", "").split("\n")] + ["+ " + l for l in inp.get("new_string", "").split("\n")])
            self.add(f"\n⏺ Edit({inp.get('file_path')}){' replace_all' if inp.get('replace_all') else ''}\n" + verbatim("  │ ", self.cut(body), "  │ ") + "\n")
        else:
            self.add(f"\n⏺ {name}\n" + verbatim("  │ ", self.cut(json.dumps(inp, ensure_ascii=False, indent=1)), "  │ ") + "\n")

    def tool_result(self, x):
        name = self.names.get(x.get("tool_use_id"))
        r = text_of(x.get("content"))
        err = x.get("is_error")
        if self.mode == "chatonly":
            if name == "AskUserQuestion" and not err and r.strip(): self.add("\n" + block("❯ ", r.strip()) + "\n")  # 用户对提问的回答
            return
        self.add(verbatim("  ⎿  " + ("[出错] " if err else ""), self.cut(r.strip() or "(无输出)")) + "\n")

    def entry(self, d):
        t = d.get("type")
        if t == "system":
            st = d.get("subtype")
            if self.mode == "chatonly" and st != "compact_boundary": return
            if st == "turn_duration":
                s = int(d["durationMs"] // 1000)
                dur = f"{s}s" if s < 60 else f"{s//60}m {s%60}s" if s < 3600 else f"{s//3600}h {s%3600//60}m {s%60}s"
                self.add(f"\n✻ Worked for {dur} · done {self.ts(d['timestamp'])}\n")
            elif st == "compact_boundary":
                self.add("\n✻ Conversation compacted\n")
            elif st == "away_summary":
                self.add("\n" + block("※ recap: ", d.get("content", "")) + "\n")
            return
        if t == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") == "queued_command":  # 用户在 Claude 忙的时候排队发的消息
                q = strip_reminders(text_of(a.get("prompt")))
                if q and not q.startswith("<task-notification>"): self.add("\n" + block("❯ ", q) + "\n")
            elif self.mode == "wholechat":
                r = d.get("rendered")
                s = "\n".join(x.get("content", "") for x in r if isinstance(x, dict)) if isinstance(r, list) else ""
                s = s.strip() or json.dumps(a, ensure_ascii=False)
                self.add("\n" + verbatim(f"  ⎿  [系统注入 {a.get('type')}] ", self.cut(s)) + "\n")
            return
        if t not in ("user", "assistant"): return
        c = d.get("message", {}).get("content")
        if t == "user":
            if d.get("isCompactSummary"):
                if self.mode == "chatonly": return
                s = text_of(c)
                self.add("\n" + block("  ⎿  ", "Summary:\n" + s.split("Summary:", 1)[-1].strip(), "     ") + "\n")
                return
            if d.get("isMeta"):
                if self.mode == "wholechat":
                    s = text_of(c).strip()
                    if s: self.add("\n" + verbatim("  ⎿  [系统注入] ", self.cut(s)) + "\n")
                return
            if isinstance(c, str): c = [{"type": "text", "text": c}]
            for x in c or []:
                if not isinstance(x, dict): continue
                k = x.get("type")
                if k == "tool_result": self.tool_result(x); continue
                if k == "image": self.add("\n❯ [图片]\n"); continue
                if k != "text": continue
                s = strip_reminders(x["text"])
                if not s: continue
                if s.startswith("<task-notification>"):
                    if self.mode == "chatonly": continue
                    self.add("\n" + verbatim("  ⎿  [系统注入] ", self.cut(s)) + "\n"); continue
                m = re.search(r"<command-name>(.*?)</command-name>", s)
                if m:
                    a = re.search(r"<command-args>(.*?)</command-args>", s, re.S)
                    self.add("\n❯ " + m.group(1) + (" " + a.group(1).strip() if a and a.group(1).strip() else "") + "\n"); continue
                m = re.search(r"<local-command-stdout>(.*?)</local-command-stdout>", s, re.S)
                if m:
                    if m.group(1).strip() and self.mode == "wholechat": self.add(block("  ⎿  ", m.group(1).strip(), "     ") + "\n")
                    continue
                self.add("\n" + block("❯ ", s) + "\n")
        else:
            if isinstance(c, str): c = [{"type": "text", "text": c}]
            for x in c or []:
                if not isinstance(x, dict): continue
                k = x.get("type")
                if k == "text" and x.get("text", "").strip(): self.add("\n" + block("⏺ ", x["text"].strip()) + "\n")
                elif k == "thinking" and self.mode == "wholechat" and x.get("thinking", "").strip():
                    self.add("\n" + block("✻ Thinking… ", x["thinking"].strip(), "  ┊ ") + "\n")
                elif k == "tool_use": self.tool_call(x)

    def run(self, path):
        for line in open(path):
            try:
                d = json.loads(line)
                if isinstance(d, dict): self.entry(d)
            except (ValueError, KeyError, AttributeError, TypeError): continue

def first_prompt(path):
    for line in open(path):
        try: d = json.loads(line)
        except ValueError: continue
        if not isinstance(d, dict): continue
        if d.get("type") == "user" and not d.get("isMeta") and not d.get("isCompactSummary"):
            s = strip_reminders(text_of(d.get("message", {}).get("content")))
            if s and not s.startswith("<"): return d.get("timestamp"), s
    return None, "session"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["chatonly", "wholechat", "both"])
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--out", help="输出文件名（只在单一模式下有效）")
    ap.add_argument("--tz", default=None, help="时区偏移，如 +8；默认本机时区")
    ap.add_argument("--max-tool-output", type=int, default=0, help="wholechat 里单条工具输入/输出最多保留多少字符，0 = 不截断")
    ap.add_argument("--no-subagents", action="store_true", help="wholechat 不附子 agent 记录")
    ap.add_argument("--redact-file", action="append", default=[], help="额外的 KEY=VALUE 文件，其中的值会被隐去")
    a = ap.parse_args()

    path = find_session()
    if a.tz:
        m = re.fullmatch(r"([+-]?)(\d{1,2})(?::?(\d{2}))?", a.tz.strip())
        if not m: sys.exit("--tz 写成 +8、-5、+5:30 这样")
        off = datetime.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        tz = datetime.timezone(-off if m.group(1) == "-" else off)
    else:
        tz = datetime.datetime.now().astimezone().tzinfo
    cwds = {os.getcwd()}
    for line in open(path):
        m = re.search(r'"cwd":"([^"]+)"', line)
        if m: cwds.add(m.group(1))
    clean = make_cleaner(collect_secrets(sorted(cwds), a.redact_file))
    os.makedirs(a.out_dir, exist_ok=True)
    if a.out and a.mode == "both": print("提示：--out 只在单独导出一种时有效，both 模式用默认文件名")
    ts, fp = first_prompt(path)
    start = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz) if ts else datetime.datetime.now(tz)
    slug = re.sub(r"[^0-9A-Za-z一-鿿\-]", "", fp.replace(" ", "-"))[:24] or "session"

    for mode in (["chatonly", "wholechat"] if a.mode == "both" else [a.mode]):
        r = Renderer(mode, tz, a.max_tool_output)
        r.run(path)
        if mode == "wholechat" and not a.no_subagents:
            sub = sorted(glob.glob(os.path.join(path[:-6], "subagents", "*.jsonl")))
            for sp in sub:
                desc = ""
                meta = sp[:-6] + ".meta.json"
                if os.path.exists(meta):
                    try: desc = json.load(open(meta)).get("description", "")
                    except ValueError: pass
                r.o.append("\n" + "─" * W + f"\n子 agent 记录：{os.path.basename(sp)} {desc}\n" + "─" * W + "\n")
                r.names = {}; r.run(sp)
        name = a.out if (a.out and a.mode != "both") else f"{start:%Y-%m-%d-%H%M%S}-{slug}-{mode}.txt"
        out = os.path.join(a.out_dir, name)
        open(out, "w").write(clean("".join(r.o)).rstrip() + "\n")
        n_user = sum(1 for l in open(out) if l.startswith("❯ "))
        print(f"{mode}: {out}  ({os.path.getsize(out)//1024} KB, {n_user} 条用户消息)")
    print(f"来源：{path}")

if __name__ == "__main__":
    main()
