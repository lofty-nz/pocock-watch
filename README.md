# Pocock Watch

Always-on-top desktop badge that shows where each Claude Code agent is in Matt Pocock's
`/implement` flow:

Read ticket → Write test → Test fails → Write code → Tests pass → Review → Commit → Ticket done → Merged

- Reads `~/.claude/projects/**.jsonl` session logs (read-only, only new lines each refresh).
- Sessions show their title, latest skill (`/implement`, `/tdd`…) and what they're doing now, or **your turn**.
- Ticket agents (sub-agents like "Implement ticket 13…") show a 9-step bar, the current step and the last action;
  their review agents are listed underneath. Finished agents shrink to one line.
- The header counts ticket `Status:` lines in the repo's `tickets/` folder (worktrees `repo-tNN` map back to `repo`).
- Desktop notification when an agent finishes, commits, marks its ticket done, merges, or a session waits for you.

Click = open/close. Drag = move. Right-click = options/quit. Settings: `~/.config/pocock-watch/settings.json`.

Run: `setsid nohup python3 ~/Sandbox/pocock-watch/pocock_watch.py &`
Tests: `python3 -m unittest discover tests` (made-up transcripts only).

Steps are worked out from tool calls (test files edited, test runs failing/passing, review agents or
`/matt-code-review`, `git commit`, `Status: done`, `git merge`), so they're a good guide, not gospel.
