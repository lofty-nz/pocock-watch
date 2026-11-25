"""Made-up Claude Code logs for screenshots and trying the badge: python3 pocock_watch.py --demo"""
import json
import os
import tempfile
import time

N = [0]


def tool(name, **inp):
    N[0] += 1
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"d{N[0]}", "name": name, "input": inp}]}}, f"d{N[0]}"


def res(tid, text, err=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]}}


def ran(cmd, out, desc, err=False):
    e, t = tool("Bash", command=cmd, description=desc)
    return [e, res(t, out, err)]


READ = [tool("Read", file_path="/demo/shop/tickets/issues/x.md")[0]]
TEST = READ + [tool("Write", file_path="/demo/shop/tests/test_x.py", content="")[0]]
RED = TEST + ran("python3 -m unittest tests/test_x.py", "FAILED (failures=1)", "Watch the new test fail", True)
CODE = RED + [tool("Edit", file_path="/demo/shop/cart.py", old_string="a", new_string="b")[0]]
GREEN = CODE + ran("python3 -m unittest discover tests", "Ran 48 tests\n\nOK", "Run the whole suite")
REVIEW = GREEN + [tool("Agent", description="Standards review ticket", prompt="")[0]]
COMMIT = REVIEW + ran("git commit -am x", "[ticket-07 1a2b3c4] x", "Commit the ticket")
DONE = COMMIT + [tool("Edit", file_path="/demo/shop/tickets/issues/x.md", old_string="", new_string="Status: done")[0]]


def build():
    root = tempfile.mkdtemp(prefix="pocock-demo-")
    repo = os.path.join(root, "shop")
    os.makedirs(os.path.join(repo, ".git"))
    os.makedirs(os.path.join(repo, "tickets", "issues"))
    for i, s in enumerate(["done"] * 9 + ["claimed"] * 3 + ["ready-for-agent"] * 2):
        with open(os.path.join(repo, "tickets", "issues", f"{i:02d}.md"), "w") as f:
            f.write(f"# Ticket {i}\n\nStatus: {s}\n")
    proj = os.path.join(root, "projects", "demo")
    now = time.time()

    def write(path, lines, age, meta=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for d in lines:
                d = dict(d, cwd=repo)
                f.write(json.dumps(d) + "\n")
        if meta is not None:
            with open(path[:-6] + ".meta.json", "w") as f:
                json.dump(meta, f)
        os.utime(path, (now - age, now - age))

    sess = os.path.join(proj, "s1.jsonl")
    write(sess, [{"type": "ai-title", "aiTitle": "Online shop: checkout tickets"},
                 {"type": "user", "message": {"content": "<command-name>/implement</command-name>"}},
                 tool("Agent", description="Implement ticket 07 discount codes", prompt="")[0]], 3)
    sub = os.path.join(proj, "s1", "subagents")
    agents = [
        ("a7", "Implement ticket 07 discount codes", REVIEW, 2, None, False),
        ("a8", "Implement ticket 08 gift cards", CODE + ran("python3 -m unittest tests/test_gift.py", "Ran 6 tests\n\nOK", "Run the gift card tests"), 6, None, False),
        ("a9", "Implement ticket 09 order emails", RED, 4, None, False),
        ("r1", "Spec review ticket 07", [tool("Read", file_path="/demo/shop/cart.py")[0]], 1, "a7", False),
        ("r2", "Standards review ticket 07", [tool("Grep", pattern="discount")[0]], 2, "a7", False),
        ("a5", "Implement ticket 05 shipping rates", DONE, 240, None, True),
        ("a6", "Implement ticket 06 tax lines", DONE, 520, None, True),
    ]
    for aid, desc, lines, age, parent, fin in agents:
        meta = {"description": desc, "spawnDepth": 2 if parent else 1, "finished": fin}
        if parent:
            meta["parentAgentId"] = parent
        write(os.path.join(sub, f"agent-{aid}.jsonl"), lines, age, meta)
    write(os.path.join(proj, "s2.jsonl"), [{"type": "ai-title", "aiTitle": "Grill the refunds spec"},
          {"type": "user", "message": {"content": "<command-name>/grill-with-docs</command-name>"}},
          {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Which refunds count?"}]}}], 95)
    return os.path.join(root, "projects")
