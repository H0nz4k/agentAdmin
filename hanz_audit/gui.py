from __future__ import annotations

import json
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.scrolled import ScrolledText

import ttkbootstrap as tb
from ttkbootstrap.constants import *

from hanz_audit.actions import build_fix_prompt, build_try_prompt
from hanz_audit.agent_config import load_agent_config
from hanz_audit.audit import run_audit
from hanz_audit.analysis import AnalysisResult, Recommendation, analyze
from hanz_audit.chat import ChatSession, load_info_context
from hanz_audit.custom_tools import default_tools_path
from hanz_audit.tools_ui import ToolsManagerWindow
from hanz_audit.config import load_config
from hanz_audit.diagnostics import (
    detect_diagnostic_topic,
    format_diagnostics,
    run_diagnostics,
)
from hanz_audit.executor import AgentExecutor
from hanz_audit.permissions import OperationLevel
from hanz_audit.inventory import (
    build_inventory,
    load_existing_inventory,
    merge_inventory,
    save_inventory,
)
from hanz_audit.memory import (
    add_facts,
    extract_facts_from_conversation,
    load_knowledge,
    save_knowledge,
)
from hanz_audit.overview import format_overview
from hanz_audit.report import audit_to_markdown, recommendations_brief, save_audit_markdown
from hanz_audit.ssh_client import SSHClient, SSHConfig, format_ssh_error
from hanz_audit.terminal_launcher import find_unix_paths, open_ssh_terminal, strip_path_punctuation
from hanz_audit.version import __version__


class HanzAuditApp(tb.Window):
    def __init__(self) -> None:
        self.config_data = load_config()
        root = self.config_data["_root"]
        agent_path = root / self.config_data["paths"].get("agent_file", "config/agent.yaml")
        self.agent_config = load_agent_config(agent_path)
        agent_name = self.agent_config.get("agent", {}).get("name", "HanzAgent")
        
        # Načtení zvoleného tématu z configu (nebo default "flatly")
        ui_theme = self.agent_config.get("ui", {}).get("theme", "flatly")
        super().__init__(themename=ui_theme)
        self.window_title_base = f"HanzHub Audit — {agent_name}"
        self.title(self.window_title_base)
        self.minsize(880, 640)
        
        self.ui_state_file = root / "ui_state.json"
        self.custom_tools_path = root / self.config_data["paths"].get(
            "custom_tools_file", "config/custom_tools.yaml"
        )
        self.v1_pack_path = root / self.config_data["paths"].get(
            "v1_tools_pack", "hanz-agent-tools-v1"
        )
        self.remote_v1_tools = self.config_data["paths"].get(
            "remote_v1_tools", "/opt/agentAdmin/tools"
        )
        self._tools_window: ToolsManagerWindow | None = None
        self._ui_state_cache = self._read_ui_state()
        self._load_window_state()

        ssh_cfg = self.config_data["ssh"]
        self.ssh_config = SSHConfig(
            host=ssh_cfg["host"],
            user=ssh_cfg["user"],
            port=ssh_cfg.get("port", 22),
            key_path=ssh_cfg.get("key_path", ""),
            connect_timeout=ssh_cfg.get("connect_timeout", 20),
            banner_timeout=ssh_cfg.get("banner_timeout", 30),
        )
        self.ssh: SSHClient | None = None
        self.current_report_md = ""
        self.current_overview = ""
        self.audit_result = None
        self.current_analysis: AnalysisResult | None = None
        self.chat: ChatSession | None = None
        self._executor: AgentExecutor | None = None
        self._busy = False
        self._connecting = False
        self._path_link_counter = 0
        self._save_ui_timer: str | None = None
        self._pane_restore_attempts = 0
        self._chat_status_base = "Pracuji"
        self._chat_status_phase = 0
        self._chat_status_timer: str | None = None
        self._agent_console_visible = False
        self._agent_console_loaded = False

        self._build_ui()
        self._restore_pane_state()
        self._init_chat()
        self._update_connection_ui(connected=False)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_window_configure, add="+")

    def _read_ui_state(self) -> dict:
        try:
            if self.ui_state_file.exists():
                return json.loads(self.ui_state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _load_window_state(self) -> None:
        state = self._ui_state_cache
        try:
            if "geometry" in state:
                self.geometry(state["geometry"])
            else:
                self.geometry("1120x860")
            if state.get("zoomed", False):
                self.state("zoomed")
        except Exception:
            self.geometry("1120x860")

    def _restore_pane_state(self) -> None:
        self._pane_restore_attempts = 0
        self._try_restore_pane()

    def _try_restore_pane(self) -> None:
        if not hasattr(self, "main_paned"):
            return
        state = self._ui_state_cache
        sash = state.get("main_paned_sash")
        ratio = float(state.get("main_paned_ratio", 0.75))
        self._pane_restore_attempts += 1
        try:
            self.main_paned.update_idletasks()
            self.update_idletasks()
            width = self.main_paned.winfo_width()
            if width <= 100:
                if self._pane_restore_attempts < 40:
                    self.after(100, self._try_restore_pane)
                return
            if sash is not None:
                pos = min(max(int(sash), 220), width - 220)
            else:
                pos = int(width * ratio)
            self.main_paned.sashpos(0, pos)
        except Exception:
            if self._pane_restore_attempts < 40:
                self.after(100, self._try_restore_pane)

    def _schedule_save_ui_state(self) -> None:
        if self._save_ui_timer:
            self.after_cancel(self._save_ui_timer)
        self._save_ui_timer = self.after(350, self._save_window_state)

    def _on_window_configure(self, event) -> None:
        if event.widget is self:
            self._schedule_save_ui_state()

    def _on_close(self) -> None:
        self._stop_chat_status_animation()
        self._save_window_state()
        if self.ssh:
            self.ssh.close()
        super().destroy()

    def _save_window_state(self) -> None:
        try:
            state = {
                "geometry": self.geometry(),
                "zoomed": self.state() == "zoomed",
            }
            if hasattr(self, "main_paned"):
                try:
                    self.main_paned.update_idletasks()
                    sash = self.main_paned.sashpos(0)
                    width = self.main_paned.winfo_width()
                    state["main_paned_sash"] = sash
                    if width > 0:
                        state["main_paned_ratio"] = round(sash / width, 4)
                except Exception:
                    pass
            self.ui_state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _add_toolbar_button(
        self,
        parent: tb.Frame,
        text: str,
        command,
        *,
        state=NORMAL,
    ) -> tb.Button:
        enabled = state != DISABLED
        btn = tb.Button(
            parent,
            text=text,
            bootstyle="primary" if enabled else "secondary",
            command=command,
            state=state,
            width=16,
        )
        btn.pack(side=LEFT, padx=(0, 8))
        return btn

    def _set_toolbar_button(self, btn: tb.Button, *, enabled: bool, text: str | None = None) -> None:
        if text is not None:
            btn.config(text=text)
        btn.config(
            state=NORMAL if enabled else DISABLED,
            bootstyle="primary" if enabled else "secondary",
        )

    def _refresh_toolbar(self) -> None:
        connected = bool(self.ssh and self.ssh.connected)
        busy = self._busy
        can_use = not busy and not self._connecting

        self._set_toolbar_button(
            self.btn_connect,
            enabled=can_use,
            text="Odpojit" if connected else "Připojit SSH",
        )
        self._set_toolbar_button(self.btn_audit, enabled=can_use and connected)
        self._set_toolbar_button(
            self.btn_export,
            enabled=can_use and bool(self.current_report_md),
        )
        self._set_toolbar_button(self.btn_reset_chat, enabled=can_use)
        self._set_toolbar_button(self.btn_terminal, enabled=can_use)
        self._set_toolbar_button(self.btn_memory, enabled=can_use and bool(self.chat))
        self._set_toolbar_button(self.btn_tools, enabled=can_use)

    def _ask_yes_no(self, title: str, message: str, *, alert: bool = False) -> bool:
        result = Messagebox.yesno(
            message,
            title=title,
            parent=self,
            alert=alert,
            buttons=["Ne:secondary", "Ano:primary"],
            default="Ano",
            localize=False,
            width=58,
        )
        return result == "Ano"

    def _show_info(self, title: str, message: str) -> None:
        Messagebox.ok(message, title=title, parent=self, localize=False, width=58)

    def _show_warning(self, title: str, message: str) -> None:
        Messagebox.show_warning(message, title=title, parent=self, localize=False, width=58)

    def _show_error(self, title: str, message: str) -> None:
        Messagebox.show_error(message, title=title, parent=self, localize=False, width=58)

    def _on_toggle_agent_console(self) -> None:
        if self._agent_console_visible:
            self._hide_agent_console()
        else:
            self._show_agent_console()

    def _show_agent_console(self) -> None:
        if not self._agent_console_visible:
            self.body_paned.add(self.agent_console_frame, weight=1)
            self._agent_console_visible = True
        self.btn_agent.config(bootstyle="primary")
        if not self._agent_console_loaded:
            self._load_agent_console_history()
            self._agent_console_loaded = True
        self.agent_console_text.text.see(END)

    def _hide_agent_console(self) -> None:
        if self._agent_console_visible:
            self.body_paned.forget(self.agent_console_frame)
            self._agent_console_visible = False
        self.btn_agent.config(bootstyle="secondary")

    def _clear_agent_console(self) -> None:
        widget = self.agent_console_text.text
        widget.config(state=NORMAL)
        widget.delete("1.0", END)
        widget.config(state=DISABLED)
        self._append_agent_console_line(
            "info", "", {}, "Log vymazán.\n", True
        )

    def _load_agent_console_history(self) -> None:
        root = self.config_data["_root"]
        log_path = root / "data" / "agent_actions.jsonl"
        if not log_path.is_file():
            return
        try:
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return
        self._append_agent_console_line(
            "info", "", {}, f"--- Historie ({min(len(lines), 40)} posledních akcí) ---\n", True
        )
        for raw in lines[-40:]:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            tool = entry.get("tool", "?")
            args = entry.get("arguments") or {}
            ok = bool(entry.get("ok"))
            preview = (entry.get("output_preview") or "").strip()
            ts = (entry.get("ts") or "")[:19].replace("T", " ")
            self._append_agent_console_line(
                "done" if ok else "err",
                tool,
                args,
                preview or ("OK" if ok else "Chyba"),
                ok,
                ts=ts,
            )

    def _on_agent_console(
        self, event: str, tool_name: str, arguments: dict, detail: str = "", ok: bool = True
    ) -> None:
        def ui() -> None:
            if event in ("start", "cmd", "confirm") and not self._agent_console_visible:
                self._show_agent_console()
            self._append_agent_console_line(event, tool_name, arguments, detail, ok)
            self.agent_console_text.text.see(END)

        self._run_on_ui(ui)

    def _append_agent_console_line(
        self,
        event: str,
        tool_name: str,
        arguments: dict,
        detail: str,
        ok: bool,
        *,
        ts: str | None = None,
    ) -> None:
        widget = self.agent_console_text.text
        widget.config(state=NORMAL)
        stamp = ts or datetime.now().astimezone().strftime("%H:%M:%S")
        widget.insert(END, f"[{stamp}] ", "ts")

        if event == "info":
            widget.insert(END, detail, "ok")
            if not detail.endswith("\n"):
                widget.insert(END, "\n")
            widget.config(state=DISABLED)
            return

        args_text = json.dumps(arguments, ensure_ascii=False) if arguments else ""
        if event == "start":
            widget.insert(END, f"▶ {tool_name}", "tool")
            if args_text and args_text != "{}":
                widget.insert(END, f"  {args_text}\n", "ok")
            else:
                widget.insert(END, "\n")
        elif event == "cmd":
            widget.insert(END, f"$ {detail.rstrip()}\n", "cmd")
        elif event == "confirm":
            widget.insert(END, f"? Potvrzení: {tool_name} — {detail}\n", "warn")
        elif event == "cancel":
            widget.insert(END, f"✕ Zrušeno: {tool_name}\n", "warn")
        elif event == "denied":
            widget.insert(END, f"✕ Zamítnuto: {tool_name} — {detail}\n", "err")
        elif event == "done":
            tag = "ok" if ok else "err"
            mark = "✓" if ok else "✗"
            widget.insert(END, f"{mark} {tool_name}", tag)
            if detail.strip():
                widget.insert(END, "\n", tag)
                for line in detail.strip().splitlines()[:30]:
                    widget.insert(END, f"  {line}\n", tag)
            else:
                widget.insert(END, "\n", tag)
        else:
            widget.insert(END, f"{event}: {tool_name} {detail}\n", "ok")

        widget.config(state=DISABLED)

    def _run_on_ui(self, callback) -> None:
        self.after(0, callback)

    def _build_ui(self) -> None:
        target = f"{self.ssh_config.user}@{self.ssh_config.host}:{self.ssh_config.port}"

        # Header
        header = tb.Frame(self, bootstyle=PRIMARY, padding=(20, 14))
        header.pack(fill=X)

        head_left = tb.Frame(header, bootstyle=PRIMARY)
        head_left.pack(side=LEFT, fill=X, expand=True)
        tb.Label(
            head_left, text="HanzHub Audit", font=("Segoe UI", 18, "bold"), bootstyle=(INVERSE, PRIMARY)
        ).pack(anchor=W)
        tb.Label(
            head_left,
            text=f"{self.agent_config.get('agent', {}).get('name', 'HanzAgent')} v{__version__}",
            font=("Segoe UI", 11),
            bootstyle=(INVERSE, PRIMARY)
        ).pack(anchor=W, pady=(4, 0))

        head_right = tb.Frame(header, bootstyle=PRIMARY)
        head_right.pack(side=RIGHT)
        self.btn_agent = tb.Button(
            head_right,
            text="Agent",
            bootstyle="secondary",
            command=self._on_toggle_agent_console,
            width=10,
        )
        self.btn_agent.pack(side=LEFT, padx=(0, 12))
        self.host_label = tb.Label(
            head_right,
            text=target,
            font=("Segoe UI", 11),
            bootstyle=(INVERSE, PRIMARY),
        )
        self.host_label.pack(side=LEFT, padx=(0, 12))
        self.status_dot = tb.Label(
            head_right,
            text="●",
            font=("Segoe UI", 18),
            bootstyle="danger",
        )
        self.status_dot.pack(side=LEFT)

        # Toolbar
        toolbar = tb.Frame(self, padding=(16, 12))
        toolbar.pack(fill=X)

        self.btn_connect = self._add_toolbar_button(
            toolbar, "Připojit SSH", self._on_connect
        )

        self.btn_audit = self._add_toolbar_button(
            toolbar,
            "Spustit audit",
            self._on_run_audit,
            state=DISABLED,
        )

        self.btn_export = self._add_toolbar_button(
            toolbar,
            "Exportovat MD…",
            self._on_export,
            state=DISABLED,
        )

        self.btn_reset_chat = self._add_toolbar_button(
            toolbar, "Reset chatu", self._on_reset_chat
        )

        self.btn_terminal = self._add_toolbar_button(
            toolbar, "Terminál", self._on_open_terminal
        )

        self.btn_memory = self._add_toolbar_button(
            toolbar, "Do paměti", self._on_save_to_memory
        )

        self.btn_tools = self._add_toolbar_button(
            toolbar, "Nástroje", self._on_open_tools_manager
        )

        self.progress = tb.Progressbar(toolbar, mode="indeterminate", bootstyle=SUCCESS, length=200)

        # Main layout: report + chat nahoře, volitelně agent konzole dole
        self.body_paned = tb.Panedwindow(self, orient=VERTICAL)
        self.body_paned.pack(fill=BOTH, expand=True, padx=14, pady=(4, 14))

        self.main_paned = tb.Panedwindow(self.body_paned, orient=HORIZONTAL)
        self.body_paned.add(self.main_paned, weight=4)
        self.main_paned.bind("<ButtonRelease-1>", lambda _e: self._schedule_save_ui_state())

        report_notebook = tb.Notebook(self.main_paned, bootstyle=SECONDARY)
        chat_outer = tb.Labelframe(self.main_paned, text=" Chat s HanzAgent ", padding=8, bootstyle=INFO)
        self.main_paned.add(report_notebook, weight=3)
        self.main_paned.add(chat_outer, weight=1)

        self.agent_console_frame = tb.Labelframe(
            self.body_paned,
            text=" Agent — příkazy na Pi ",
            padding=8,
            bootstyle=INFO,
        )
        console_toolbar = tb.Frame(self.agent_console_frame)
        console_toolbar.pack(fill=X, pady=(0, 6))
        tb.Label(
            console_toolbar,
            text="Live log nástrojů a SSH příkazů (jako terminál v Cursoru)",
            font=("Segoe UI", 9),
            bootstyle=SECONDARY,
        ).pack(side=LEFT)
        tb.Button(
            console_toolbar,
            text="Vymazat",
            bootstyle="secondary",
            command=self._clear_agent_console,
            width=10,
        ).pack(side=RIGHT)

        self.agent_console_text = ScrolledText(
            self.agent_console_frame,
            wrap=WORD,
            font=("Consolas", 10),
            state=DISABLED,
            height=10,
            relief=FLAT,
            bootstyle="round",
        )
        self.agent_console_text.pack(fill=BOTH, expand=True)
        widget = self.agent_console_text.text
        widget.tag_configure("ts", foreground="#64748b")
        widget.tag_configure("tool", foreground="#38bdf8")
        widget.tag_configure("cmd", foreground="#4ade80")
        widget.tag_configure("ok", foreground="#94a3b8")
        widget.tag_configure("err", foreground="#f87171")
        widget.tag_configure("warn", foreground="#fbbf24")
        self._append_agent_console_line(
            "info", "", {}, "Agent konzole připravena. Spusť nástroj v chatu nebo přes Vyřešit.\n", True
        )

        overview_tab = tb.Frame(report_notebook, padding=6)
        detail_tab = tb.Frame(report_notebook, padding=6)
        actions_tab = tb.Frame(report_notebook, padding=6)
        report_notebook.add(overview_tab, text="  Přehled auditu  ")
        report_notebook.add(detail_tab, text="  Technický report  ")
        report_notebook.add(actions_tab, text="  Akce  ")

        # Overview Tab
        overview_text_frame = tb.Frame(overview_tab)
        overview_text_frame.pack(fill=BOTH, expand=True)

        is_dark = "dark" in self.agent_config.get("ui", {}).get("theme", "flatly").lower() or self.agent_config.get("ui", {}).get("theme", "flatly").lower() in ["cyborg", "superhero", "solar", "vapor"]
        bg_color = "#222222" if is_dark else "#ffffff"
        fg_color = "#f8fafc" if is_dark else "#212529"

        self.overview_text = ScrolledText(
            overview_text_frame,
            wrap=WORD,
            font=("Segoe UI", 11),
            state=DISABLED,
            spacing1=4,
            spacing3=6,
            relief=FLAT,
            bootstyle="round"
        )
        self.overview_text.pack(fill=BOTH, expand=True, padx=2, pady=2)

        # Actions tab
        actions_outer = tb.Labelframe(
            actions_tab, text=" Co dělat dál ", padding=8, bootstyle=PRIMARY
        )
        actions_outer.pack(fill=BOTH, expand=True)
        actions_canvas = tk.Canvas(actions_outer, highlightthickness=0, bg=bg_color)
        actions_scroll = tb.Scrollbar(
            actions_outer, orient=VERTICAL, command=actions_canvas.yview, bootstyle=ROUND
        )
        self.actions_inner = tb.Frame(actions_canvas, bootstyle=PRIMARY)
        self.actions_inner.bind(
            "<Configure>",
            lambda e: actions_canvas.configure(scrollregion=actions_canvas.bbox("all")),
        )
        actions_canvas.create_window((0, 0), window=self.actions_inner, anchor="nw")
        actions_canvas.configure(yscrollcommand=actions_scroll.set)
        actions_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        actions_scroll.pack(side=RIGHT, fill=Y)

        def _on_actions_wheel(event):
            actions_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        actions_canvas.bind(
            "<Enter>", lambda _: actions_canvas.bind_all("<MouseWheel>", _on_actions_wheel)
        )
        actions_canvas.bind("<Leave>", lambda _: actions_canvas.unbind_all("<MouseWheel>"))

        # Detail Tab
        self.report_text = ScrolledText(
            detail_tab, wrap=WORD, font=("Consolas", 10), state=DISABLED, relief=FLAT,
            bootstyle="round"
        )
        self.report_text.pack(fill=BOTH, expand=True, padx=2, pady=2)

        self._show_overview_placeholder()
        self._show_actions_placeholder()

        # Chat
        self.chat_text = ScrolledText(
            chat_outer, wrap=WORD, font=("Segoe UI", 11), state=DISABLED, height=8, relief=FLAT,
            bootstyle="round"
        )
        self.chat_text.text.tag_configure("user", foreground="#3b82f6", font=("Segoe UI", 11, "bold"))
        self.chat_text.text.tag_configure("assistant", foreground=fg_color, font=("Segoe UI", 11))
        self.chat_text.text.tag_configure("system", foreground="#94a3b8", font=("Segoe UI", 10, "italic"))
        self.chat_text.text.tag_configure("error", foreground="#ef4444", font=("Segoe UI", 11, "bold"))
        self.chat_text.pack(fill=BOTH, expand=True, pady=(0, 10))

        self.chat_activity = tb.Frame(chat_outer)
        self.chat_status_label = tb.Label(
            self.chat_activity,
            text="Pracuji…",
            font=("Segoe UI", 10),
            bootstyle=INFO,
        )
        self.chat_status_label.pack(anchor=W, fill=X, pady=(0, 4))
        self.chat_progress = tb.Progressbar(
            self.chat_activity, mode="indeterminate", bootstyle=INFO
        )
        self.chat_progress.pack(fill=X)

        self.chat_input_row = tb.Frame(chat_outer)
        self.chat_input_row.pack(fill=X)
        self.chat_input = tb.Entry(self.chat_input_row, font=("Segoe UI", 11))
        self.chat_input.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        self.chat_input.bind("<Return>", lambda _e: self._on_send_chat())
        self.btn_send = tb.Button(
            self.chat_input_row, text="Odeslat", bootstyle=PRIMARY, command=self._on_send_chat
        )
        self.btn_send.pack(side=RIGHT)

    def _init_chat(self) -> None:
        root = self.config_data["_root"]
        info = load_info_context(root, self.config_data["paths"]["info_file"])
        knowledge_path = root / self.config_data["paths"].get(
            "knowledge_file", "config/knowledge.yaml"
        )
        try:
            self.chat = ChatSession(
                api_key=self.config_data["openai"]["api_key"],
                model=self.config_data["openai"]["model"],
                info_context=info,
                agent_config=self.agent_config,
                tools_enabled=True,
                knowledge_path=knowledge_path,
                custom_tools_path=self.custom_tools_path,
                v1_pack_path=self.v1_pack_path,
                remote_tools_root=self.remote_v1_tools,
            )
            self._append_chat(
                "system",
                "HanzAgent připraven. Klikni „Připojit SSH“, pak „Spustit audit“.\n"
                "Nástroje: tlačítko „Nástroje“ — vlastní YAML + balíček hanz-agent-tools-v1.\n"
                "Skripty v1 musí být na Pi v /opt/agentAdmin/tools (viz README balíčku).\n"
                "Soubory: read_file / write_file / delete_file jen na whitelistu — zápis a mazání potvrzuješ v dialogu.\n"
                "Tlačítko „Vyřešit“ spustí plný servisní režim.\n",
            )
        except ValueError as exc:
            self._append_chat("error", f"{exc}\n")
            self.btn_send.config(state=DISABLED)

    def _ensure_executor(self) -> AgentExecutor | None:
        if not self.ssh or not self.ssh.connected:
            return None
        root = self.config_data["_root"]
        log_path = root / "data" / "agent_actions.jsonl"
        self._executor = AgentExecutor(
            self.ssh,
            log_path,
            approval=self._request_tool_approval,
            on_console=self._on_agent_console,
            tools_path=self.custom_tools_path,
            on_tools_reload=self._reload_agent_tools,
            v1_pack_path=self.v1_pack_path,
            remote_tools_root=self.remote_v1_tools,
        )
        return self._executor

    def _reload_agent_tools(self) -> None:
        if self.chat:
            self.chat.reload_custom_tools()

    def _on_open_tools_manager(self) -> None:
        if self._tools_window and self._tools_window.winfo_exists():
            self._tools_window.lift()
            self._tools_window.focus_force()
            return
        self._tools_window = ToolsManagerWindow(
            self,
            self.custom_tools_path,
            v1_pack_path=self.v1_pack_path,
            remote_tools_root=self.remote_v1_tools,
            on_saved=self._reload_agent_tools,
        )

    def _request_tool_approval(
        self, tool_name: str, arguments: dict, level: OperationLevel, description: str
    ) -> bool:
        result: list[bool | None] = [None]
        event = threading.Event()

        def ask() -> None:
            if level == OperationLevel.DESTRUCTIVE:
                msg = (
                    f"DESTRUKTIVNÍ OPERACE\n\n{description}\n\n"
                    f"Nástroj: {tool_name}\nParametry: {arguments}\n\n"
                    "Potvrď kliknutím Ano pouze pokud souhlasíš."
                )
            else:
                msg = (
                    f"Citlivá operace (úroveň 2)\n\n{description}\n\n"
                    f"Nástroj: {tool_name}\nParametry: {arguments}\n\n"
                    "Povolit provedení na Pi?"
                )
            if self._busy:
                self._set_chat_status(f"Čekám na potvrzení: {tool_name}")
            result[0] = self._ask_yes_no("HanzAgent — potvrzení", msg, alert=True)
            if self._busy:
                self._set_chat_status("Servis + AI")
            event.set()

        self.after(0, ask)
        event.wait(timeout=300)
        return bool(result[0])

    def _tool_handler(self, tool_name: str, arguments: dict) -> str:
        executor = self._ensure_executor()
        if not executor:
            return '{"ok": false, "output": "SSH není připojeno — nástroj nelze spustit."}'

        def on_ui_notify() -> None:
            self._append_chat("system", f"Spouštím nástroj: {tool_name}…\n")
            self._set_chat_status(f"Nástroj: {tool_name}")

        self._run_on_ui(on_ui_notify)
        return executor.run_tool(tool_name, arguments)

    def _set_status_indicator(self, state: str) -> None:
        states = {
            "ok": "success",
            "busy": "warning",
            "error": "danger",
            "idle": "danger",
        }
        color_bootstyle = states.get(state, "danger")
        self.status_dot.configure(bootstyle=color_bootstyle)

    def _update_connection_ui(
        self, connected: bool, status: str = "", *, indicator: str | None = None
    ) -> None:
        if indicator:
            self._set_status_indicator(indicator)
        elif connected:
            self._set_status_indicator("ok")
        elif not self._busy:
            self._set_status_indicator("idle")

        _ = status
        self._refresh_toolbar()

    def _set_chat_status(self, status: str) -> None:
        self._chat_status_base = status.rstrip(".… ")
        if self._busy:
            self._chat_status_phase = 0
            self.chat_status_label.config(text=f"{self._chat_status_base}…")

    def _start_chat_status_animation(self) -> None:
        self._stop_chat_status_animation()
        self._tick_chat_status()

    def _stop_chat_status_animation(self) -> None:
        if self._chat_status_timer:
            self.after_cancel(self._chat_status_timer)
            self._chat_status_timer = None

    def _tick_chat_status(self) -> None:
        if not self._busy:
            return
        dots = ("", ".", "..", "...")
        self._chat_status_phase = (self._chat_status_phase + 1) % len(dots)
        self.chat_status_label.config(text=f"{self._chat_status_base}{dots[self._chat_status_phase]}")
        self._chat_status_timer = self.after(450, self._tick_chat_status)

    def _show_chat_activity(self, status: str = "") -> None:
        label = status or "Pracuji"
        self._set_chat_status(label)
        if not self.chat_activity.winfo_ismapped():
            self.chat_activity.pack(fill=X, pady=(0, 8), before=self.chat_input_row)
        self.chat_progress.start(12)
        self._start_chat_status_animation()

    def _hide_chat_activity(self) -> None:
        self._stop_chat_status_animation()
        self.chat_progress.stop()
        self.chat_activity.pack_forget()

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        if busy:
            self._set_status_indicator("busy")
            self.progress.pack(side=LEFT, padx=(20, 0))
            self.progress.start(10)
            self._show_chat_activity(status)
            self.btn_send.config(text="Pracuji…", bootstyle=SECONDARY)
        elif self.ssh and self.ssh.connected:
            self._set_status_indicator("ok")
            self.progress.stop()
            self.progress.pack_forget()
            self._hide_chat_activity()
            self.btn_send.config(text="Odeslat", bootstyle=PRIMARY)
        else:
            self._set_status_indicator("idle")
            self.progress.stop()
            self.progress.pack_forget()
            self._hide_chat_activity()
            self.btn_send.config(text="Odeslat", bootstyle=PRIMARY)
        input_state = DISABLED if busy or not self.chat else NORMAL
        self.chat_input.config(state=input_state)
        self.btn_send.config(state=input_state)
        self._refresh_toolbar()

    def _on_connect(self) -> None:
        if self._busy or self._connecting:
            return

        if self.ssh and self.ssh.connected:
            self.ssh.close()
            self.ssh = None
            self._update_connection_ui(False, "Odpojeno")
            return

        self._connecting = True
        self._refresh_toolbar()

        def work() -> None:
            try:
                client = SSHClient(self.ssh_config)
                client.connect()
                info = client.test_connection()
                first_line = info.splitlines()[0] if info else "ok"

                def on_ok() -> None:
                    self.ssh = client
                    self._connecting = False
                    self._update_connection_ui(
                        True,
                        f"Připojeno — {self.ssh_config.host} ({first_line})",
                    )

                self._run_on_ui(on_ok)
            except Exception as exc:
                err = format_ssh_error(exc, self.ssh_config)

                def on_err() -> None:
                    self._connecting = False
                    self.ssh = None
                    self._update_connection_ui(
                        False, "SSH — nepřipojeno", indicator="error"
                    )
                    self._show_error("SSH připojení", err)

                self._run_on_ui(on_err)

        threading.Thread(target=work, daemon=True).start()

    def _on_run_audit(self) -> None:
        if self._busy:
            return
        if not self.ssh or not self.ssh.connected:
            self._show_warning(
                "SSH",
                "Nejprve klikni „Připojit SSH“ a počkej na zelený stav v liště.",
            )
            return

        self._set_busy(True, "Probíhá audit a analýza…")

        def work() -> None:
            try:
                result = run_audit(self.ssh, self.ssh_config.host)
                analysis = analyze(result)
                overview = format_overview(
                    result, analysis, self.agent_config, actions_in_panel=True
                )
                md = audit_to_markdown(result, analysis, overview)
                root = self.config_data["_root"]
                audits_dir = root / self.config_data["paths"]["audits_dir"]
                ts = result.timestamp.astimezone().strftime("%Y%m%d_%H%M%S")
                auto_path = audits_dir / f"audit_{ts}.md"
                save_audit_markdown(result, auto_path, analysis, overview)

                services_path = root / self.config_data["paths"]["services_file"]
                inv = build_inventory(result)
                existing = load_existing_inventory(services_path)
                inv = merge_inventory(inv, existing)
                save_inventory(inv, services_path)

                def done() -> None:
                    self.audit_result = result
                    self.current_analysis = analysis
                    self.current_report_md = md
                    self.current_overview = overview
                    self._show_overview(overview)
                    self._rebuild_action_buttons(analysis)
                    self._show_report(md)
                    if self.chat:
                        self.chat.set_audit_context(md)
                    n_rec = len(analysis.recommendations)
                    self._set_busy(
                        False,
                        f"Audit hotov — {n_rec} návrhů, services.yaml aktualizován",
                    )
                    self._append_chat(
                        "system",
                        f"Audit dokončen ({len(result.sections)} sekcí).\n"
                        f"Uloženo: data/audits/{auto_path.name}\n"
                        f"Inventura: config/services.yaml\n\n"
                        f"{recommendations_brief(analysis)}\n",
                    )

                self._run_on_ui(done)
            except Exception as exc:
                err = str(exc)

                def on_fail() -> None:
                    self._set_busy(False, "Audit selhal")
                    self._show_error("Audit", err)

                self._run_on_ui(on_fail)

        threading.Thread(target=work, daemon=True).start()

    def _show_overview_placeholder(self) -> None:
        intro = self.agent_config.get("overview", {}).get("intro", "")
        text = (
            "Zatím žádný audit.\n\n"
            "1. Připoj SSH\n"
            "2. Spusť audit\n\n"
            "Zde uvidíš srozumitelný přehled — stejný styl jako v config/agent.yaml.\n"
        )
        if intro:
            text += f"\n{intro.strip()}"
        self._show_overview(text)

    def _show_overview(self, text: str) -> None:
        self.overview_text.text.config(state=NORMAL)
        self.overview_text.text.delete("1.0", END)
        self.overview_text.text.insert(END, text)
        self.overview_text.text.config(state=DISABLED)

    def _clear_frame(self, frame: tb.Frame) -> None:
        for child in frame.winfo_children():
            child.destroy()

    def _show_actions_placeholder(self) -> None:
        self._clear_frame(self.actions_inner)
        tb.Label(
            self.actions_inner,
            text="Po auditu zde u každého doporučení budou tlačítka\n"
            "„Zkusit vyřešit“ a „Vyřešit“.",
            font=("Segoe UI", 11),
            bootstyle=SECONDARY,
            justify=LEFT,
        ).pack(anchor=W, padx=8, pady=12)

    def _rebuild_action_buttons(self, analysis: AnalysisResult) -> None:
        self._clear_frame(self.actions_inner)
        if not analysis.recommendations:
            tb.Label(self.actions_inner, text="Žádná doporučení.", font=("Segoe UI", 11)).pack(
                anchor=W, padx=4, pady=8
            )
            return

        btn_cfg = self.agent_config.get("action_buttons", {})
        try_label = btn_cfg.get("try_label", "Zkusit vyřešit")
        fix_label = btn_cfg.get("fix_label", "Vyřešit")

        for rec in analysis.recommendations:
            card = tb.Frame(self.actions_inner, padding=14)
            card.pack(fill=X, pady=6, padx=4)

            title = f"{rec.priority}. {rec.problem}"
            if rec.requires_confirmation:
                title += "  ⚠"
            tb.Label(
                card, text=title, font=("Segoe UI", 12, "bold"), wraplength=900
            ).pack(anchor=W)
            tb.Label(
                card,
                text=rec.solution,
                font=("Segoe UI", 11),
                wraplength=900,
                bootstyle=SECONDARY
            ).pack(anchor=W, pady=(6, 12))

            btn_row = tb.Frame(card)
            btn_row.pack(anchor=W)
            tb.Button(
                btn_row,
                text=try_label,
                bootstyle=OUTLINE,
                command=lambda r=rec: self._on_try_fix(r),
            ).pack(side=LEFT, padx=(0, 10))
            tb.Button(
                btn_row,
                text=fix_label,
                bootstyle=PRIMARY,
                command=lambda r=rec: self._on_apply_fix(r),
            ).pack(side=LEFT)

            tb.Separator(self.actions_inner, orient=HORIZONTAL).pack(fill=X, pady=6)

    def _on_try_fix(self, rec: Recommendation) -> None:
        if self._busy or not self.chat:
            return
        self._send_chat_with_diagnostics(rec, status="Live diagnostika + AI…")

    def _send_chat_with_diagnostics(self, rec: Recommendation, status: str = "Diagnostika…") -> None:
        if self._busy or not self.chat:
            return
        topic = detect_diagnostic_topic(rec)
        self.chat_input.delete(0, END)
        self._append_chat("user", f"Zkusit vyřešit: {rec.problem}\n")
        self._set_busy(True, status)

        def work() -> None:
            live_data = ""
            if self.ssh and self.ssh.connected:
                try:
                    sections = run_diagnostics(self.ssh, topic)
                    live_data = format_diagnostics(sections, topic)

                    def on_diag_ok() -> None:
                        self._append_chat(
                            "system",
                            f"Live diagnostika dokončena ({len(sections)} příkazů, téma: {topic}).\n",
                        )

                    self._run_on_ui(on_diag_ok)
                except Exception as exc:
                    live_data = ""

                    def on_diag_err() -> None:
                        self._append_chat(
                            "system",
                            f"Live diagnostika selhala: {exc}. Použiji data z auditu.\n",
                        )

                    self._run_on_ui(on_diag_err)
            else:

                def on_no_ssh() -> None:
                    self._append_chat(
                        "system",
                        "SSH není připojeno — agent pracuje jen s daty z auditu. "
                        "Pro live diagnostiku klikni „Připojit SSH“.\n",
                    )

                self._run_on_ui(on_no_ssh)

            prompt = build_try_prompt(rec, live_data)
            try:
                self._run_on_ui(lambda: self._set_chat_status("OpenAI odpovídá"))
                reply, _ = self.chat.ask(prompt)

                def on_ok() -> None:
                    self._append_chat("assistant", f"{reply}\n")
                    self._set_busy(False, "Připraveno")

                self._run_on_ui(on_ok)
            except Exception as exc:
                err_str = str(exc)
                if "insufficient_quota" in err_str or "429" in err_str:

                    def on_err() -> None:
                        self._append_chat(
                            "error",
                            "Došel kredit na OpenAI API (insufficient_quota). "
                            "Zkontroluj prosím fakturaci zde: ",
                        )
                        self._append_chat_link(
                            "platform.openai.com/account/billing",
                            "https://platform.openai.com/account/billing",
                        )
                        self._set_busy(False, "Chyba AI")

                else:

                    def on_err() -> None:
                        self._append_chat("error", f"{err_str}\n")
                        self._set_busy(False, "Chyba AI")

                self._run_on_ui(on_err)

        threading.Thread(target=work, daemon=True).start()

    def _on_apply_fix(self, rec: Recommendation) -> None:
        if self._busy or not self.chat:
            return
        if not self.ssh or not self.ssh.connected:
            self._show_warning(
                "SSH",
                "Pro režim Vyřešit musí být připojeno SSH — agent potřebuje spouštět nástroje na Pi.",
            )
            return
        if not self._ask_yes_no(
            "Vyřešit problém",
            f"{rec.problem}\n\n"
            "HanzAgent provede diagnostiku a může spouštět servisní nástroje "
            "(restart služeb, prune cache, docker cleanup…).\n"
            "Citlivé operace vždy potvrdíš v dialogu.\n\n"
            "Pokračovat?",
        ):
            return
        self._send_fix_with_tools(rec)

    def _send_fix_with_tools(self, rec: Recommendation) -> None:
        if self._busy or not self.chat:
            return
        topic = detect_diagnostic_topic(rec)
        self.chat_input.delete(0, END)
        self._append_chat("user", f"Vyřešit: {rec.problem}\n")
        self._set_busy(True, "Servis + AI…")

        def work() -> None:
            live_data = ""
            if self.ssh and self.ssh.connected:
                try:
                    sections = run_diagnostics(self.ssh, topic)
                    live_data = format_diagnostics(sections, topic)

                    def on_diag_ok() -> None:
                        self._append_chat(
                            "system",
                            f"Live diagnostika dokončena ({len(sections)} příkazů).\n",
                        )

                    self._run_on_ui(on_diag_ok)
                except Exception as exc:

                    def on_diag_err() -> None:
                        self._append_chat("system", f"Diagnostika selhala: {exc}\n")

                    self._run_on_ui(on_diag_err)

            prompt = build_fix_prompt(rec, live_data)
            try:
                self._run_on_ui(lambda: self._set_chat_status("Agent pracuje (AI + nástroje)"))
                reply, tools_used = self.chat.ask(prompt, tool_handler=self._tool_handler)

                def on_ok() -> None:
                    self._append_chat("assistant", f"{reply}\n")
                    self._warn_if_no_tools_used(tools_used, True)
                    self._set_busy(False, "Připraveno")

                self._run_on_ui(on_ok)
            except Exception as exc:
                err_str = str(exc)
                if "insufficient_quota" in err_str or "429" in err_str:

                    def on_err() -> None:
                        self._append_chat(
                            "error",
                            "Došel kredit na OpenAI API. Zkontroluj fakturaci zde: ",
                        )
                        self._append_chat_link(
                            "platform.openai.com/account/billing",
                            "https://platform.openai.com/account/billing",
                        )
                        self._set_busy(False, "Chyba AI")

                else:

                    def on_err() -> None:
                        self._append_chat("error", f"{err_str}\n")
                        self._set_busy(False, "Chyba AI")

                self._run_on_ui(on_err)

        threading.Thread(target=work, daemon=True).start()

    def _warn_if_no_tools_used(self, tools_used: bool, had_handler: bool) -> None:
        if had_handler and not tools_used:
            self._append_chat(
                "system",
                "⚠ Žádný nástroj nebyl spuštěn — agent odpověděl jen textem, na Pi se nic neprovedlo. "
                "Otevři panel Agent (vpravo nahoře) pro ověření, nebo napiš konkrétněji "
                "(např. „stop_service predicapp“).\n",
            )
            self._append_agent_console_line(
                "info",
                "",
                {},
                "⚠ Textová odpověď bez spuštění nástroje na Pi.\n",
                False,
            )

    def _chat_tool_handler(self):
        if self.ssh and self.ssh.connected and self.chat and self.chat.tools_enabled:
            return self._tool_handler
        return None

    def _send_chat_message(self, msg: str, status: str = "AI odpovídá…") -> None:
        if self._busy or not self.chat:
            return
        self.chat_input.delete(0, END)
        self._append_chat("user", f"{msg}\n")
        tool_handler = self._chat_tool_handler()
        if tool_handler:
            self._set_busy(True, "Agent pracuje…")
        else:
            self._set_busy(True, status)

        def work() -> None:
            try:
                if tool_handler:
                    self._run_on_ui(lambda: self._set_chat_status("Agent pracuje (AI + nástroje)"))
                    reply, tools_used = self.chat.ask(msg, tool_handler=tool_handler)
                else:
                    self._run_on_ui(lambda: self._set_chat_status("OpenAI odpovídá"))
                    reply, tools_used = self.chat.ask(msg)

                def on_ok() -> None:
                    self._append_chat("assistant", f"{reply}\n")
                    self._warn_if_no_tools_used(tools_used, bool(tool_handler))
                    self._set_busy(False, "Připraveno")

                self._run_on_ui(on_ok)
            except Exception as exc:
                err_str = str(exc)
                if "insufficient_quota" in err_str or "429" in err_str:
                    def on_err() -> None:
                        self._append_chat("error", "Došel kredit na OpenAI API (insufficient_quota). Zkontroluj prosím fakturaci zde: ")
                        self._append_chat_link("platform.openai.com/account/billing", "https://platform.openai.com/account/billing")
                        self._set_busy(False, "Chyba AI")
                else:
                    def on_err() -> None:
                        self._append_chat("error", f"{err_str}\n")
                        self._set_busy(False, "Chyba AI")

                self._run_on_ui(on_err)

        threading.Thread(target=work, daemon=True).start()

    def _show_report(self, md: str) -> None:
        self.report_text.text.config(state=NORMAL)
        self.report_text.text.delete("1.0", END)
        self.report_text.text.insert(END, md)
        self.report_text.text.config(state=DISABLED)

    def _on_export(self) -> None:
        if not self.current_report_md:
            self._show_info("Export", "Nejdřív spusť audit.")
            return
        default_name = f"hanzhub_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Vše", "*.*")],
            initialfile=default_name,
        )
        if path:
            Path(path).write_text(self.current_report_md, encoding="utf-8")
            self._show_info("Export", f"Uloženo:\n{path}")

    def _on_save_to_memory(self) -> None:
        if self._busy or not self.chat:
            return
        conv = self.chat.recent_conversation_text()
        if not conv.strip():
            self._show_info("Paměť", "Nejdřív proběhni konverzaci s agentem.")
            return

        self._set_busy(True, "Extrahuji poznatky…")

        def work() -> None:
            try:
                facts = extract_facts_from_conversation(
                    self.chat.client,
                    self.chat.model,
                    conv,
                )

                def on_done() -> None:
                    self._set_busy(False)
                    if not facts:
                        self._show_info(
                            "Paměť",
                            "Z poslední konverzace nebyly nalezeny nové poznatky k uložení.",
                        )
                        return
                    preview = "\n\n".join(
                        f"• [{f.get('category', '?')}] {f.get('subject', '')}: "
                        f"{(f.get('text') or '')[:200]}"
                        for f in facts
                    )
                    if not self._ask_yes_no(
                        "Uložit do paměti?",
                        f"Agent navrhuje {len(facts)} poznatků:\n\n{preview}\n\nUložit?",
                    ):
                        return
                    data = load_knowledge(self.chat.knowledge_path)
                    n = add_facts(data, facts)
                    if n == 0:
                        self._show_info("Paměť", "Poznatky už v paměti jsou — nic nového.")
                        return
                    save_knowledge(data, self.chat.knowledge_path)
                    self.chat.reload_memory()
                    self._append_chat(
                        "system",
                        f"Uloženo {n} poznatků do config/knowledge.yaml.\n",
                    )
                    self._show_info(
                        "Paměť",
                        f"Uloženo {n} poznatků.\nSoubor: config/knowledge.yaml",
                    )

                self._run_on_ui(on_done)
            except Exception as exc:

                def on_err() -> None:
                    self._set_busy(False)
                    self._show_error("Paměť", str(exc))

                self._run_on_ui(on_err)

        threading.Thread(target=work, daemon=True).start()

    def _on_open_terminal(self, remote_path: str | None = None) -> None:
        try:
            open_ssh_terminal(self.ssh_config, remote_path)
        except Exception as exc:
            self._show_error("Terminál", f"Nepodařilo se otevřít SSH terminál:\n{exc}")

    def _open_terminal_at_path(self, path: str) -> None:
        clean = strip_path_punctuation(path)
        self._on_open_terminal(clean)

    def _register_path_link_tag(self, tag_name: str, path: str) -> None:
        widget = self.chat_text.text
        widget.tag_configure(tag_name, foreground="#22c55e", underline=True)
        widget.tag_bind(
            tag_name,
            "<Button-1>",
            lambda _e, p=path: self._open_terminal_at_path(p),
        )
        widget.tag_bind(tag_name, "<Enter>", lambda _e: widget.config(cursor="hand2"))
        widget.tag_bind(tag_name, "<Leave>", lambda _e: widget.config(cursor=""))

    def _insert_text_with_path_links(self, text: str, base_tag: str) -> None:
        widget = self.chat_text.text
        paths = find_unix_paths(text)
        if not paths:
            widget.insert(END, text, base_tag)
            return

        pos = 0
        for start, end, path in paths:
            if start > pos:
                widget.insert(END, text[pos:start], base_tag)
            display = text[start:end]
            display = strip_path_punctuation(display)
            if display:
                self._path_link_counter += 1
                tag_name = f"path_link_{self._path_link_counter}"
                self._register_path_link_tag(tag_name, path)
                widget.insert(END, display, (base_tag, tag_name))
            pos = end
        if pos < len(text):
            widget.insert(END, text[pos:], base_tag)

    def _append_chat(self, role: str, text: str) -> None:
        self.chat_text.text.config(state=NORMAL)
        prefixes = {
            "user": "Ty: ",
            "assistant": "HanzAgent: ",
            "system": "",
            "error": "Chyba: ",
        }
        prefix = prefixes.get(role, "")
        if prefix:
            self.chat_text.text.insert(END, prefix, role)
        self._insert_text_with_path_links(text, role)
        self.chat_text.text.see(END)
        self.chat_text.text.config(state=DISABLED)

    def _append_chat_link(self, text: str, url: str) -> None:
        self.chat_text.text.config(state=NORMAL)
        tag_name = f"link_{hash(url)}"
        self.chat_text.text.tag_configure(tag_name, foreground="#3b82f6", underline=True)
        self.chat_text.text.tag_bind(tag_name, "<Button-1>", lambda e, u=url: webbrowser.open(u))
        self.chat_text.text.tag_bind(tag_name, "<Enter>", lambda e: self.chat_text.text.config(cursor="hand2"))
        self.chat_text.text.tag_bind(tag_name, "<Leave>", lambda e: self.chat_text.text.config(cursor=""))
        
        self.chat_text.text.insert(END, text, tag_name)
        self.chat_text.text.insert(END, "\n")
        self.chat_text.text.see(END)
        self.chat_text.text.config(state=DISABLED)

    def _on_send_chat(self) -> None:
        if self._busy or not self.chat:
            return
        msg = self.chat_input.get().strip()
        if not msg:
            return
        self._send_chat_message(msg)

    def _on_reset_chat(self) -> None:
        if self.chat:
            self.chat.reset_conversation()
            if self.current_report_md:
                self.chat.set_audit_context(self.current_report_md)
        self.chat_text.text.config(state=NORMAL)
        self.chat_text.text.delete("1.0", END)
        self.chat_text.text.config(state=DISABLED)
        self._append_chat("system", "Nová konverzace.\n")

    def destroy(self) -> None:
        self._on_close()


def main() -> None:
    app = HanzAuditApp()
    app.mainloop()
