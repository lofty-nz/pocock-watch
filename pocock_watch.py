#!/usr/bin/env python3
"""Pocock Watch — always-on-top badge showing where each Claude Code agent is in
Matt Pocock's /implement flow (read ticket → test → red → code → green → review →
commit → done → merged).

Click the badge to open or close the list. Drag to move. Right-click for options.
Reads ~/.claude/projects logs only; never changes anything.
"""
import json
import math
import os
import subprocess
import time

import cairo
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

import watcher  # noqa: E402

CONFIG = os.path.expanduser("~/.config/pocock-watch/settings.json")
DEFAULTS = {"x": 40, "y": 80, "open": True, "notify": True, "show_finished": True, "refresh_s": 2}

W_PILL, H_PILL = 330, 34
W_OPEN = 470
BG = (0.09, 0.10, 0.13, 0.92)
FG = (0.92, 0.94, 0.97)
DIM = (0.55, 0.59, 0.66)
GREEN = (0.30, 0.82, 0.48)
AMBER = (0.98, 0.70, 0.20)
RED = (0.95, 0.36, 0.33)
BLUE = (0.40, 0.66, 1.00)
GREY = (0.27, 0.29, 0.34)
STATE_COL = {"working": GREEN, "your turn": BLUE, "quiet": AMBER, "finished": DIM}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(CONFIG) as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(s, f, indent=1)


def ago(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h{sec % 3600 // 60:02d}"


def rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def notify(title, body):
    try:
        subprocess.Popen(["notify-send", "-a", "Pocock Watch", "-i", "system-run", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class Badge(Gtk.Window):
    def __init__(self):
        super().__init__(title="Pocock Watch")
        self.s = load_settings()
        self.w = watcher.Watcher()
        self.rows = []
        self.counts = {}
        self.repo = None
        self.seen = {}           # row path → (state, stage) for notifications
        self.first_scan = True
        self.drag = None
        self.moved = False

        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.move(self.s["x"], self.s["y"])
        self.area = Gtk.DrawingArea()
        self.add(self.area)
        self.area.connect("draw", self.on_draw)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("destroy", Gtk.main_quit)

        self.refresh()
        GLib.timeout_add(int(self.s["refresh_s"] * 1000), self.refresh)
        GLib.timeout_add(120, self.tick)    # gentle pulse on the current step
        self.show_all()

    # ---------- data ----------
    def refresh(self):
        try:
            self.rows = self.w.scan()
        except Exception as e:  # keep the badge alive whatever the logs look like
            print("scan failed:", e)
        repo = next((watcher.repo_of(r.cwd) for r in self.rows if watcher.repo_of(r.cwd)), None)
        self.repo = repo
        self.counts = watcher.ticket_counts(repo) if repo else {}
        self.check_notifications()
        self.resize_to_fit()
        self.area.queue_draw()
        return True

    def tick(self):
        if any(r.state == "working" for r in self.rows):
            self.area.queue_draw()
        return True

    def check_notifications(self):
        for r in self.rows:
            key = (r.state, r.stage)
            old = self.seen.get(r.path)
            self.seen[r.path] = key
            if self.first_scan or old is None or old == key or not self.s["notify"]:
                continue
            name = (f"Ticket {r.ticket}: " if r.ticket else "") + r.label
            if r.kind == "agent" and r.depth == 1:
                if r.state == "finished" and old[0] != "finished":
                    notify("Agent finished", f"{name}\nreached: {watcher.LABEL.get(r.stage, '—')}")
                elif r.stage != old[1] and r.stage in ("done", "merge", "commit"):
                    notify(watcher.LABEL[r.stage], name)
            elif r.kind == "session" and r.state == "your turn" and old[0] != "your turn":
                notify("Your turn", r.label)
        self.first_scan = False

    # ---------- what to show ----------
    def visible(self):
        """(session, [ticket agents]) pairs worth showing."""
        out = []
        for r in self.rows:
            if r.kind != "session":
                continue
            kids = [c for c in r.children if self.s["show_finished"] or c.state != "finished"]
            if kids or r.state in ("working", "your turn"):
                out.append((r, kids))
        return out

    def layout_height(self):
        if not self.s["open"]:
            return H_PILL
        h = 44
        vis = self.visible()
        if not vis:
            return h + 30
        for _, kids in vis:
            h += 26
            for k in kids:
                h += 22 if k.state == "finished" else 58 + 15 * min(len(k.children), 3)
        return h + 8

    def resize_to_fit(self):
        w = W_OPEN if self.s["open"] else W_PILL
        h = self.layout_height()
        if (w, h) != self.get_size():
            self.area.set_size_request(w, h)
            self.resize(w, h)

    # ---------- drawing ----------
    def text(self, cr, x, y, s, size=12, col=FG, bold=False, maxw=None):
        """Draw text with its baseline at y; returns its width."""
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription(f"Sans {'Bold ' if bold else ''}{size * 0.75:.1f}")
        lay.set_font_description(fd)
        lay.set_text(s, -1)
        if maxw:
            lay.set_width(int(max(maxw, 1) * Pango.SCALE))
            lay.set_ellipsize(Pango.EllipsizeMode.END)
        cr.set_source_rgb(*col)
        cr.move_to(x, y - lay.get_baseline() / Pango.SCALE)
        PangoCairo.show_layout(cr, lay)
        return lay.get_pixel_extents()[1].width

    def width(self, cr, s, size, bold=False):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription(f"Sans {'Bold ' if bold else ''}{size * 0.75:.1f}"))
        lay.set_text(s, -1)
        return lay.get_pixel_extents()[1].width

    def dot(self, cr, x, y, r, col, pulse=False):
        if pulse:
            a = 0.35 + 0.35 * (1 + math.sin(time.time() * 4)) / 2
            cr.set_source_rgba(*col, a)
            cr.arc(x, y, r + 3, 0, 2 * math.pi)
            cr.fill()
        cr.set_source_rgb(*col)
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.fill()

    def stage_bar(self, cr, x, y, w, stage, live):
        n = len(watcher.STAGES)
        gap = 3
        seg = (w - gap * (n - 1)) / n
        cur = watcher.ORDER.get(stage, -1)
        for i, (key, _) in enumerate(watcher.STAGES):
            if i < cur:
                col, a = GREEN, 1
            elif i == cur:
                col = RED if key == "red" else AMBER if live else GREEN
                a = (0.55 + 0.45 * (1 + math.sin(time.time() * 4)) / 2) if live else 1
            else:
                col, a = GREY, 1
            cr.set_source_rgba(*col, a)
            rrect(cr, x + i * (seg + gap), y, seg, 7, 3)
            cr.fill()

    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        W = self.get_allocated_width()
        H = self.get_allocated_height()
        cr.set_source_rgba(*BG)
        rrect(cr, 0.5, 0.5, W - 1, H - 1, 12 if self.s["open"] else H / 2)
        cr.fill()
        if self.s["open"]:
            self.draw_open(cr, W)
        else:
            self.draw_pill(cr, W)

    def summary(self):
        agents = [r for r in self.rows if r.kind == "agent" and r.depth == 1]
        working = [a for a in agents if a.state == "working"]
        return agents, working

    def draw_pill(self, cr, W):
        agents, working = self.summary()
        turn = any(r.kind == "session" and r.state == "your turn" for r in self.rows)
        col = GREEN if working else BLUE if turn else DIM
        self.dot(cr, 17, H_PILL / 2, 5, col, pulse=bool(working))
        x = 30
        x += self.text(cr, x, 22, "Pocock", 12, FG, bold=True) + 8
        if working:
            lead = max(working, key=lambda a: a.mtime)
            msg = f"{len(working)} working · " + (f"T{lead.ticket} " if lead.ticket else "") \
                + watcher.LABEL.get(lead.stage, "starting")
        elif turn:
            msg = "your turn"
        elif agents:
            msg = f"{len(agents)} agents, all quiet"
        else:
            msg = "no agents running"
        self.text(cr, x, 22, msg, 12, FG if working else DIM, maxw=W - x - 12)

    def draw_open(self, cr, W):
        agents, working = self.summary()
        self.dot(cr, 18, 21, 5, GREEN if working else DIM, pulse=bool(working))
        self.text(cr, 30, 26, "POCOCK WATCH", 13, FG, bold=True)
        if self.counts:
            c = self.counts
            done = c.get("done", 0)
            total = sum(c.values())
            line = f"{os.path.basename(self.repo)}  tickets {done}/{total} done"
            busy = c.get("claimed", 0)
            if busy:
                line += f" · {busy} claimed"
            self.text(cr, 152, 26, line, 11, DIM, maxw=W - 166)
        y = 44
        vis = self.visible()
        if not vis:
            self.text(cr, 16, y + 18, "No Claude sessions active in the last 45 minutes.", 11, DIM)
            return
        for sess, kids in vis:
            st = sess.state
            self.dot(cr, 16, y + 12, 3.5, STATE_COL[st], pulse=(st == "working"))
            x = 26
            x += self.text(cr, x, y + 16, sess.label, 12, FG, bold=True, maxw=230) + 8
            right = st if st != "working" else (f"/{sess.skill}" if sess.skill else sess.now)
            if st == "working" and not kids:
                right = sess.now
            self.text(cr, x, y + 16, f"{right} · {ago(sess.age)}", 11, STATE_COL[st] if st != "working" else DIM,
                      maxw=W - x - 12)
            y += 26
            for k in kids:
                y = self.draw_agent(cr, k, y, W)

    def draw_agent(self, cr, k, y, W):
        st = k.state
        if st == "finished":
            return self.draw_finished(cr, k, y, W)
        live = st == "working"
        x0 = 26
        tag = f"T{k.ticket}" if k.ticket else "•"
        self.dot(cr, x0 + 3, y + 10, 3.5, STATE_COL[st], pulse=live)
        w = self.text(cr, x0 + 12, y + 14, tag, 12, FG, bold=True)
        self.text(cr, x0 + 18 + w, y + 14, k.label, 11, FG if st != "finished" else DIM, maxw=W - x0 - 110 - w)
        stage_name = watcher.LABEL.get(k.stage, "starting")
        right = ("✓ " if st == "finished" else "") + stage_name
        self.text(cr, W - 14 - self.width(cr, right, 11, True), y + 14, right, 11,
                  RED if k.stage == "red" and live else (AMBER if live else STATE_COL[st]), bold=True)
        self.stage_bar(cr, x0 + 12, y + 21, W - x0 - 26, k.stage, live)
        now = k.now if st != "finished" else "handed back"
        extra = f"  ({k.note})" if k.note and live else ""
        self.text(cr, x0 + 12, y + 44, f"{now}{extra} · {ago(k.age)}", 10.5, DIM, maxw=W - x0 - 26)
        y += 58
        for c in k.children[:3]:
            cst = c.state
            self.dot(cr, x0 + 18, y - 4, 2.5, STATE_COL[cst], pulse=(cst == "working"))
            self.text(cr, x0 + 26, y, f"↳ {c.label} — {'done' if cst == 'finished' else c.now}", 10, DIM,
                      maxw=W - x0 - 40)
            y += 15
        return y

    def draw_finished(self, cr, k, y, W):
        """One dim line: ✓ T13 title ……… Ticket done · 2m, with a mini step bar."""
        x0 = 26
        tag = f"T{k.ticket}" if k.ticket else "•"
        ok = k.stage in ("done", "merge")
        w = self.text(cr, x0 + 2, y + 14, "✓" if ok else "■", 11, GREEN if ok else AMBER, bold=True)
        w += self.text(cr, x0 + 8 + w, y + 14, tag, 11, DIM, bold=True)
        right = f"{watcher.LABEL.get(k.stage, '—')} · {ago(k.age)}"
        rw = self.width(cr, right, 10.5)
        bar_w = 60
        self.text(cr, x0 + 14 + w, y + 14, k.label, 10.5, DIM, maxw=W - x0 - 40 - w - rw - bar_w)
        self.stage_bar(cr, W - 22 - rw - bar_w, y + 8, bar_w, k.stage, False)
        self.text(cr, W - 14 - rw, y + 14, right, 10.5, DIM)
        return y + 22

    # ---------- mouse ----------
    def on_press(self, _w, ev):
        if ev.button == 1:
            self.drag = (ev.x_root, ev.y_root, *self.get_position())
            self.moved = False
        elif ev.button == 3:
            self.menu(ev)
        return True

    def on_motion(self, _w, ev):
        if self.drag:
            dx, dy = ev.x_root - self.drag[0], ev.y_root - self.drag[1]
            if abs(dx) + abs(dy) > 4:
                self.moved = True
            if self.moved:
                self.move(int(self.drag[2] + dx), int(self.drag[3] + dy))
        return True

    def on_release(self, _w, ev):
        if ev.button == 1 and self.drag:
            if self.moved:
                self.s["x"], self.s["y"] = self.get_position()
            else:
                self.s["open"] = not self.s["open"]
                self.resize_to_fit()
                self.area.queue_draw()
            save_settings(self.s)
            self.drag = None
        return True

    def menu(self, ev):
        m = Gtk.Menu()

        def toggle(label, key):
            it = Gtk.CheckMenuItem(label=label)
            it.set_active(self.s[key])

            def flip(_i):
                self.s[key] = not self.s[key]
                save_settings(self.s)
                self.refresh()
            it.connect("toggled", flip)
            m.append(it)
        toggle("Show finished agents", "show_finished")
        toggle("Desktop notifications", "notify")
        m.append(Gtk.SeparatorMenuItem())
        legend = Gtk.MenuItem(label="Steps: " + " → ".join(l for _, l in watcher.STAGES))
        legend.set_sensitive(False)
        m.append(legend)
        m.append(Gtk.SeparatorMenuItem())
        q = Gtk.MenuItem(label="Quit")
        q.connect("activate", lambda _i: Gtk.main_quit())
        m.append(q)
        m.show_all()
        m.popup_at_pointer(ev)


if __name__ == "__main__":
    Badge()
    Gtk.main()
