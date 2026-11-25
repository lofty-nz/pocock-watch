"""Works out where each Claude Code agent is in Matt Pocock's /implement flow.

Reads the session logs in ~/.claude/projects (read-only, only the new lines each time)
and turns tool calls into steps: read ticket → write test → test fails → write code →
tests pass → review → commit → ticket done → merged.
"""
import glob
import json
import os
import re
import time

STAGES = [
    ("read", "Read ticket"),
    ("test", "Write test"),
    ("red", "Test fails"),
    ("code", "Write code"),
    ("green", "Tests pass"),
    ("review", "Review"),
    ("commit", "Commit"),
    ("done", "Ticket done"),
    ("merge", "Merged"),
]
ORDER = {k: i for i, (k, _) in enumerate(STAGES)}
LABEL = dict(STAGES)

REVIEW_SKILLS = {"matt-code-review", "code-review", "review", "security-review"}
TEST_RUN = re.compile(r"\b(pytest|unittest|npm (run )?test|node --test|vitest|jest|go test|cargo test)\b|tests?/\S*\.(py|js|sh)\b")
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|[._-](test|spec)\.[a-z]+$")
CODE_FILE = re.compile(r"\.(py|js|mjs|ts|tsx|jsx|html|css|sh|go|rs|ino|c|cpp|h)$")
FAILED = re.compile(r"\bFAIL(ED|:)|\bERROR:|Traceback \(most recent|\b\d+ failed\b|AssertionError")
PASSED = re.compile(r"(^|\n)OK\b|\b\d+ passed\b|\ball tests pass", re.I)
TICKET = re.compile(r"ticket[ _-]?#?(\d+)|-t(\d+)\b", re.I)
COMMAND = re.compile(r"<command-name>/?([\w:-]+)</command-name>")

ACTIVE_S = 90          # log changed this recently → working
SHOW_FOR_S = 45 * 60   # hide agents quiet for longer than this
FINISHED_SHOW_S = 20 * 60


def ticket_of(text):
    m = TICKET.search(text or "")
    return (m.group(1) or m.group(2)) if m else None


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


class Agent:
    """Folds transcript lines into a step, the latest skill and a 'now' line."""

    def __init__(self):
        self.stage = None
        self.skill = None
        self.now = "starting"
        self.note = ""
        self.ticket = None
        self.cwd = None
        self.waiting_on_user = False
        self.title = None
        self._pending = {}   # tool_use_id → kind of Bash run ("test" / "commit")

    def _reach(self, stage):
        if self.stage is None or ORDER[stage] > ORDER[self.stage]:
            self.stage = stage

    def add(self, d):
        if d.get("cwd"):
            self.cwd = d["cwd"]
            self.ticket = self.ticket or ticket_of(d["cwd"])
        if d.get("type") == "ai-title" and d.get("aiTitle"):
            self.title = d["aiTitle"]
        msg = d.get("message")
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        if d.get("type") == "assistant":
            self.waiting_on_user = msg.get("stop_reason") == "end_turn"
            for c in content if isinstance(content, list) else []:
                if c.get("type") == "tool_use":
                    self.waiting_on_user = False
                    self._tool(c.get("id"), c.get("name", ""), c.get("input") or {})
                elif c.get("type") == "text" and c.get("text", "").strip():
                    self.now = "writing a reply"
        elif d.get("type") == "user":
            if isinstance(content, str):
                m = COMMAND.search(content)
                if m:
                    self.skill = m.group(1)
                self.waiting_on_user = False
                return
            for c in content if isinstance(content, list) else []:
                if c.get("type") == "tool_result":
                    self._result(c.get("tool_use_id"), _text(c.get("content")), c.get("is_error"))
                elif c.get("type") == "text":
                    m = COMMAND.search(c.get("text", ""))
                    if m:
                        self.skill = m.group(1)

    def _tool(self, tid, name, inp):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        base = os.path.basename(path)
        if name == "Bash":
            cmd = inp.get("command", "")
            self.now = inp.get("description") or cmd.splitlines()[0][:60] if cmd else "running a command"
            self.ticket = self.ticket or ticket_of(cmd)
            if re.search(r"\bgit (-C \S+ )?merge\b(?! --abort)", cmd) and "merge-base" not in cmd:
                self._reach("merge")
            if "Status: done" in cmd:
                self._reach("done")
            if re.search(r"\bgit (-C \S+ )?commit\b", cmd):
                self._pending[tid] = "commit"
            elif TEST_RUN.search(cmd):
                self._pending[tid] = "test"
                self.now = inp.get("description") or "running tests"
            if re.search(r"\b(cat|head|less|sed -n)\b.*tickets/", cmd):
                self._reach("read")
        elif name == "Read":
            self.now = f"reading {base}"
            if "tickets/" in path or "/issues/" in path:
                self._reach("read")
                self.ticket = self.ticket or ticket_of(base) or (re.match(r"(\d+)-", base) or [None, None])[1]
        elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            self.now = f"editing {base}"
            new = (inp.get("new_string") or "") + (inp.get("content") or "")
            if "tickets/" in path or "/issues/" in path:
                if re.search(r"Status:\s*done", new):
                    self._reach("done")
            elif TEST_FILE.search(path):
                self._reach("test")
            elif CODE_FILE.search(path):
                self._reach("code")
        elif name in ("Agent", "Task"):
            desc = inp.get("description", "")
            self.now = f"agent: {desc}"[:60]
            if "review" in desc.lower():
                self._reach("review")
        elif name == "Skill":
            s = inp.get("skill", "")
            self.skill = s.split(":")[-1]
            self.now = f"/{self.skill}"
            if self.skill in REVIEW_SKILLS:
                self._reach("review")
            if self.skill == "tdd":
                self.now = "/tdd — test first"
        elif name == "SubagentHandback":
            self.now = "handed back"
        elif name in ("Grep", "Glob"):
            self.now = "searching the code"
        else:
            self.now = name

    def _result(self, tid, text, is_error):
        kind = self._pending.pop(tid, None)
        if kind == "commit" and not is_error and re.search(r"^\[\S+ [0-9a-f]{6,}\]", text, re.M):
            self._reach("commit")
        elif kind == "test":
            if is_error or FAILED.search(text):
                self.note = "tests failing"
                if self.stage in (None, "read", "test"):
                    self._reach("red")
            elif PASSED.search(text):
                self.note = ""
                self._reach("green")


class Row:
    def __init__(self, kind, path):
        self.kind = kind          # "session" or "agent"
        self.path = path
        self.agent = Agent()
        self.offset = 0
        self.title = ""
        self.parent = None
        self.depth = 0
        self.finished = False
        self.mtime = 0
        self.first_seen = time.time()
        self.flagged_done = False
        self.children = []
        self.id = os.path.basename(path)[:-len(".jsonl")]
        if kind == "agent":
            self.id = self.id[len("agent-"):]
            self.session = os.path.basename(os.path.dirname(os.path.dirname(path)))

    @property
    def label(self):
        return self.agent.title or self.title

    def __getattr__(self, k):   # row.stage, row.now, row.ticket … come from its Agent
        return getattr(self.__dict__["agent"], k)

    @property
    def age(self):
        return time.time() - self.mtime

    @property
    def state(self):
        if self.finished:
            return "finished"
        if self.kind == "session" and self.agent.waiting_on_user and self.age > 5:
            return "your turn"
        return "working" if self.age < ACTIVE_S else "quiet"


class Watcher:
    def __init__(self, root=os.path.expanduser("~/.claude/projects")):
        self.root = root
        self.rows = {}

    def _read_new(self, row):
        try:
            size = os.path.getsize(row.path)
        except OSError:
            return
        if size < row.offset:          # file replaced → start again
            row.agent, row.offset = Agent(), 0
        if size == row.offset:
            return
        with open(row.path, "rb") as f:
            f.seek(row.offset)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        for line in chunk[:end].splitlines():
            try:
                row.agent.add(json.loads(line))
            except (ValueError, AttributeError):
                pass
        row.offset += end + 1

    def scan(self):
        now = time.time()
        found = []
        for p in glob.glob(os.path.join(self.root, "*", "*.jsonl")):
            found.append(("session", p))
        for p in glob.glob(os.path.join(self.root, "*", "*", "subagents", "agent-*.jsonl")):
            found.append(("agent", p))
        out = []
        for kind, p in found:
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            meta = {}
            if kind == "agent":
                try:
                    with open(p[:-len(".jsonl")] + ".meta.json") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    pass
            finished = bool(meta.get("finished"))
            limit = FINISHED_SHOW_S if finished else SHOW_FOR_S
            if now - mtime > limit:
                self.rows.pop(p, None)
                continue
            row = self.rows.get(p)
            if row is None:
                row = self.rows[p] = Row(kind, p)
            row.mtime, row.finished = mtime, finished
            if kind == "agent":
                row.title = meta.get("description") or os.path.basename(p)
                row.depth = meta.get("spawnDepth", 1)
                row.parent = meta.get("parentAgentId")
                row.agent.ticket = row.agent.ticket or ticket_of(row.title)
            else:
                row.title = row.agent.title or "Session " + row.id[:8]
            self._read_new(row)
            if kind == "agent" and row.agent.ticket is None:
                row.agent.ticket = ticket_of(row.agent.now)
            if kind == "session" and row.agent.title:
                row.title = row.agent.title
            out.append(row)
        # nest: reviewers under their ticket agent, ticket agents under their session
        by_id = {r.id: r for r in out}
        for r in out:
            r.children = []
        for r in sorted(out, key=lambda r: r.mtime, reverse=True):
            if r.kind == "agent":
                parent = by_id.get(r.parent) if r.parent else by_id.get(r.session)
                if parent is not None:
                    parent.children.append(r)
        out.sort(key=lambda r: (r.kind != "session", r.state == "finished", -r.mtime))
        return out


def ticket_counts(repo):
    """Status counts from every ticket file under <repo>/tickets."""
    counts = {}
    for p in glob.glob(os.path.join(repo, "tickets", "**", "*.md"), recursive=True):
        try:
            with open(p, errors="replace") as f:
                m = re.search(r"^Status:\s*([\w-]+)", f.read(4000), re.M)
        except OSError:
            continue
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


def repo_of(cwd):
    """Main checkout for a cwd (a ticket worktree 'repo-t13' maps back to 'repo')."""
    if not cwd:
        return None
    path = cwd
    while path and path != "/":
        if os.path.exists(os.path.join(path, ".git")):
            main = re.sub(r"-t\d+$", "", path)
            return main if os.path.isdir(os.path.join(main, "tickets")) else (path if os.path.isdir(os.path.join(path, "tickets")) else None)
        path = os.path.dirname(path)
    return None
