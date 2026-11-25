"""Step detection from made-up Claude Code transcript lines (no real logs)."""
import json, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import watcher

n = 0
def tool(name, **inp):
    global n; n += 1
    return {"type": "assistant", "timestamp": "2026-11-25T03:00:00Z",
            "message": {"content": [{"type": "tool_use", "id": f"t{n}", "name": name, "input": inp}]}}, f"t{n}"
def result(tid, text, err=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]}}

def feed(agent, *pairs):
    for p in pairs:
        agent.add(p[0] if isinstance(p, tuple) else p)

class StageTests(unittest.TestCase):
    def test_reading_the_ticket_is_the_first_step(self):
        a = watcher.Agent()
        feed(a, tool("Read", file_path="/r/tickets/upgrade/issues/13-x.md"))
        self.assertEqual(a.stage, "read")

    def test_test_first_then_red_then_code_then_green(self):
        a = watcher.Agent()
        feed(a, tool("Write", file_path="/r/tests/test_x.py", content="..."))
        self.assertEqual(a.stage, "test")
        e, tid = tool("Bash", command="python3 -m unittest tests/test_x.py", description="Run the new test")
        a.add(e); a.add(result(tid, "FAIL: test_x\nFAILED (failures=1)", err=True))
        self.assertEqual(a.stage, "red")
        feed(a, tool("Edit", file_path="/r/server.py", old_string="a", new_string="b"))
        self.assertEqual(a.stage, "code")
        e, tid = tool("Bash", command="python3 -m unittest discover tests")
        a.add(e); a.add(result(tid, "Ran 40 tests in 3.1s\n\nOK"))
        self.assertEqual(a.stage, "green")

    def test_a_failing_run_after_green_does_not_go_backwards(self):
        a = watcher.Agent()
        e, tid = tool("Bash", command="pytest -q"); a.add(e); a.add(result(tid, "5 passed"))
        e, tid = tool("Bash", command="pytest -q"); a.add(e); a.add(result(tid, "1 failed, 4 passed", err=True))
        self.assertEqual(a.stage, "green")
        self.assertIn("failing", a.note)

    def test_review_commit_done_merge(self):
        a = watcher.Agent()
        feed(a, tool("Agent", description="Standards review ticket 13", prompt="..."))
        self.assertEqual(a.stage, "review")
        e, tid = tool("Bash", command="git add -A && git commit -m 'ticket 13'"); a.add(e); a.add(result(tid, "[ticket-13 abc123] ticket 13"))
        self.assertEqual(a.stage, "commit")
        feed(a, tool("Edit", file_path="/r/tickets/upgrade/issues/13-x.md", old_string="Status: claimed", new_string="Status: done"))
        self.assertEqual(a.stage, "done")
        feed(a, tool("Bash", command="cd /r && git merge ticket-13"))
        self.assertEqual(a.stage, "merge")

    def test_skill_calls_are_remembered(self):
        a = watcher.Agent()
        feed(a, tool("Skill", skill="matt-code-review"))
        self.assertEqual(a.skill, "matt-code-review")
        self.assertEqual(a.stage, "review")
        a.add({"type": "user", "message": {"content": "<command-name>/implement</command-name> tickets/x.md"}})
        self.assertEqual(a.skill, "implement")

    def test_now_line_uses_bash_description_and_file_names(self):
        a = watcher.Agent()
        feed(a, tool("Bash", command="ls", description="List the worktree"))
        self.assertEqual(a.now, "List the worktree")
        feed(a, tool("Edit", file_path="/r/library.py", old_string="a", new_string="b"))
        self.assertEqual(a.now, "editing library.py")

    def test_ticket_number_from_description_or_worktree(self):
        self.assertEqual(watcher.ticket_of("Implement ticket 13 supporting documents"), "13")
        self.assertEqual(watcher.ticket_of("work in /home/x/statement-teacher-t07 please"), "07")
        self.assertIsNone(watcher.ticket_of("Spec review"))

    def test_session_title_and_handback(self):
        a = watcher.Agent()
        a.add({"type": "ai-title", "aiTitle": "Statement teacher tickets"})
        self.assertEqual(a.title, "Statement teacher tickets")
        feed(a, tool("SubagentHandback", result="done"))
        self.assertEqual(a.now, "handed back")

class ScanTests(unittest.TestCase):
    def test_reviewers_nest_under_their_ticket_agent_and_agents_under_their_session(self):
        root = tempfile.mkdtemp()
        sub = os.path.join(root, "proj", "s9", "subagents"); os.makedirs(sub)
        with open(os.path.join(root, "proj", "s9.jsonl"), "w") as f:
            f.write(json.dumps({"type": "ai-title", "aiTitle": "Tickets run"}) + "\n")
        for aid, meta in [("p1", {"description": "Implement ticket 13", "spawnDepth": 1}),
                          ("c1", {"description": "Spec review ticket 13", "spawnDepth": 2, "parentAgentId": "p1"})]:
            with open(os.path.join(sub, f"agent-{aid}.meta.json"), "w") as f:
                json.dump(meta, f)
            with open(os.path.join(sub, f"agent-{aid}.jsonl"), "w") as f:
                f.write(json.dumps(tool("Bash", command="ls", description="Look around")[0]) + "\n")
        rows = watcher.Watcher(root).scan()
        sess = [r for r in rows if r.kind == "session"][0]
        self.assertEqual(sess.title, "Tickets run")
        self.assertEqual([c.title for c in sess.children], ["Implement ticket 13"])
        self.assertEqual([c.title for c in sess.children[0].children], ["Spec review ticket 13"])

    def test_scan_finds_subagents_reads_incrementally_and_ignores_half_lines(self):
        root = tempfile.mkdtemp()
        sub = os.path.join(root, "proj", "sess1", "subagents"); os.makedirs(sub)
        with open(os.path.join(root, "proj", "sess1.jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "cwd": "/r", "message": {"content": "hi"}}) + "\n")
        with open(os.path.join(sub, "agent-a1.meta.json"), "w") as f:
            json.dump({"description": "Implement ticket 05 digits", "spawnDepth": 1}, f)
        p = os.path.join(sub, "agent-a1.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps(tool("Read", file_path="/r/tickets/05.md")[0]) + "\n")
            f.write('{"type": "assist')   # half-written line
        w = watcher.Watcher(root)
        rows = w.scan()
        ag = [r for r in rows if r.kind == "agent"][0]
        self.assertEqual((ag.ticket, ag.stage, ag.title), ("05", "read", "Implement ticket 05 digits"))
        with open(p, "a") as f:
            f.write('ant"}\n' + json.dumps(tool("Write", file_path="/r/tests/test_d.py", content="")[0]) + "\n")
        ag = [r for r in w.scan() if r.kind == "agent"][0]
        self.assertEqual(ag.stage, "test")

    def test_old_finished_agents_drop_off(self):
        root = tempfile.mkdtemp()
        sub = os.path.join(root, "proj", "s", "subagents"); os.makedirs(sub)
        with open(os.path.join(sub, "agent-b.meta.json"), "w") as f:
            json.dump({"description": "Implement ticket 02", "spawnDepth": 1, "finished": True}, f)
        p = os.path.join(sub, "agent-b.jsonl"); open(p, "w").write("")
        old = time.time() - 3 * 3600; os.utime(p, (old, old))
        self.assertEqual([r for r in watcher.Watcher(root).scan() if r.kind == "agent"], [])

    def test_ticket_counts_from_status_lines(self):
        root = tempfile.mkdtemp(); d = os.path.join(root, "tickets", "upgrade", "issues"); os.makedirs(d)
        for i, s in enumerate(["done", "done", "claimed", "ready-for-agent"]):
            open(os.path.join(d, f"{i}.md"), "w").write(f"# T\n\nStatus: {s}\n")
        self.assertEqual(watcher.ticket_counts(root), {"done": 2, "claimed": 1, "ready-for-agent": 1})

if __name__ == "__main__":
    unittest.main()
