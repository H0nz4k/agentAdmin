from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

import ttkbootstrap as tb
from ttkbootstrap.constants import *

from hanz_audit.actions import build_fix_prompt, build_try_prompt
from hanz_audit.agent_config import load_agent_config
from hanz_audit.audit import run_audit
from hanz_audit.analysis import AnalysisResult, Recommendation, analyze
from hanz_audit.chat import ChatSession, load_info_context
from hanz_audit.config import load_config
from hanz_audit.inventory import (
    build_inventory,
    load_existing_inventory,
    merge_inventory,
    save_inventory,
)
from hanz_audit.overview import format_overview
from hanz_audit.report import audit_to_markdown, recommendations_brief, save_audit_markdown
from hanz_audit.ssh_client import SSHClient, SSHConfig, format_ssh_error
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
        
        self.title(f"HanzHub Audit — {agent_name}")
        self.geometry("1120x860")
        self.minsize(880, 640)

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
        self._busy = False
        self._connecting = False

        self._build_ui()
        self._init_chat()
        self._update_connection_ui(connected=False)

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
            text=f"{self.agent_config.get('agent', {}).get('name', 'HanzAgent')} v{__version__}  ·  {target}",
            font=("Segoe UI", 11),
            bootstyle=(INVERSE, PRIMARY)
        ).pack(anchor=W, pady=(4, 0))

        head_right = tb.Frame(header, bootstyle=PRIMARY)
        head_right.pack(side=RIGHT)
        self.status_dot = tb.Label(
            head_right,
            text="●",
            font=("Segoe UI", 14),
            bootstyle=(INVERSE, PRIMARY)
        )
        self.status_dot.pack(side=LEFT, padx=(0, 8))
        self.status_var = tk.StringVar(value="Nepřipojeno")

        # Toolbar
        toolbar = tb.Frame(self, padding=(16, 12))
        toolbar.pack(fill=X)

        self.btn_connect = tb.Button(
            toolbar, text="Připojit SSH", bootstyle=PRIMARY, command=self._on_connect
        )
        self.btn_connect.pack(side=LEFT, padx=(0, 10))

        self.btn_audit = tb.Button(
            toolbar,
            text="Spustit audit",
            bootstyle=SUCCESS,
            command=self._on_run_audit,
            state=DISABLED,
        )
        self.btn_audit.pack(side=LEFT, padx=(0, 10))

        self.btn_export = tb.Button(
            toolbar, text="Exportovat MD…", bootstyle=OUTLINE, command=self._on_export, state=DISABLED
        )
        self.btn_export.pack(side=LEFT, padx=(0, 10))

        self.btn_reset_chat = tb.Button(
            toolbar, text="Nový chat", bootstyle=LINK, command=self._on_reset_chat
        )
        self.btn_reset_chat.pack(side=LEFT)

        self.progress = tb.Progressbar(toolbar, mode="indeterminate", bootstyle=SUCCESS, length=200)

        # Main PanedWindow
        paned = tb.Panedwindow(self, orient=VERTICAL, bootstyle=INFO)
        paned.pack(fill=BOTH, expand=True, padx=14, pady=(4, 14))

        report_notebook = tb.Notebook(paned, bootstyle=INFO)
        chat_outer = tb.Labelframe(paned, text=" Chat s HanzAgent ", padding=8, bootstyle=INFO)
        paned.add(report_notebook, weight=3)
        paned.add(chat_outer, weight=2)

        overview_tab = tb.Frame(report_notebook, padding=6)
        detail_tab = tb.Frame(report_notebook, padding=6)
        report_notebook.add(overview_tab, text="  Přehled auditu  ")
        report_notebook.add(detail_tab, text="  Technický report  ")

        # Overview Tab
        overview_paned = tb.Panedwindow(overview_tab, orient=VERTICAL, bootstyle=INFO)
        overview_paned.pack(fill=BOTH, expand=True)

        overview_text_frame = tb.Frame(overview_paned)
        actions_outer = tb.Labelframe(
            overview_paned, text=" Akce — co dělat dál ", padding=8, bootstyle=PRIMARY
        )
        overview_paned.add(overview_text_frame, weight=3)
        overview_paned.add(actions_outer, weight=2)

        is_dark = "dark" in self.agent_config.get("ui", {}).get("theme", "flatly").lower() or self.agent_config.get("ui", {}).get("theme", "flatly").lower() in ["cyborg", "superhero", "solar", "vapor"]
        bg_color = "#222222" if is_dark else "#ffffff"
        fg_color = "#f8fafc" if is_dark else "#212529"

        self.overview_text = scrolledtext.ScrolledText(
            overview_text_frame,
            wrap=WORD,
            font=("Segoe UI", 11),
            state=DISABLED,
            spacing1=4,
            spacing3=6,
            relief=FLAT,
            padx=10,
            pady=10,
            bg=bg_color,
            fg=fg_color
        )
        self.overview_text.pack(fill=BOTH, expand=True, padx=2, pady=2)

        actions_canvas = tk.Canvas(actions_outer, highlightthickness=0)
        actions_scroll = tb.Scrollbar(
            actions_outer, orient=VERTICAL, command=actions_canvas.yview, bootstyle=ROUND
        )
        self.actions_inner = tb.Frame(actions_canvas)
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

        self._show_actions_placeholder()

        # Detail Tab
        self.report_text = scrolledtext.ScrolledText(
            detail_tab, wrap=WORD, font=("Consolas", 10), state=DISABLED, relief=FLAT, padx=10, pady=10,
            bg=bg_color, fg=fg_color
        )
        self.report_text.pack(fill=BOTH, expand=True, padx=2, pady=2)

        self._show_overview_placeholder()

        # Chat
        self.chat_text = scrolledtext.ScrolledText(
            chat_outer, wrap=WORD, font=("Segoe UI", 11), state=DISABLED, height=8, relief=FLAT, padx=10, pady=10,
            bg=bg_color, fg=fg_color
        )
        self.chat_text.tag_configure("user", foreground="#3b82f6", font=("Segoe UI", 11, "bold"))
        self.chat_text.tag_configure("assistant", foreground=fg_color, font=("Segoe UI", 11))
        self.chat_text.tag_configure("system", foreground="#94a3b8", font=("Segoe UI", 10, "italic"))
        self.chat_text.tag_configure("error", foreground="#ef4444", font=("Segoe UI", 11, "bold"))
        self.chat_text.pack(fill=BOTH, expand=True, pady=(0, 10))

        input_row = tb.Frame(chat_outer)
        input_row.pack(fill=X)
        self.chat_input = tb.Entry(input_row, font=("Segoe UI", 11))
        self.chat_input.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        self.chat_input.bind("<Return>", lambda _e: self._on_send_chat())
        self.btn_send = tb.Button(
            input_row, text="Odeslat", bootstyle=PRIMARY, command=self._on_send_chat
        )
        self.btn_send.pack(side=RIGHT)

    def _init_chat(self) -> None:
        root = self.config_data["_root"]
        info = load_info_context(root, self.config_data["paths"]["info_file"])
        try:
            self.chat = ChatSession(
                api_key=self.config_data["openai"]["api_key"],
                model=self.config_data["openai"]["model"],
                info_context=info,
                agent_config=self.agent_config,
            )
            self._append_chat(
                "system",
                "HanzAgent připraven. Klikni „Připojit SSH“, pak „Spustit audit“.\n",
            )
        except ValueError as exc:
            self._append_chat("error", f"{exc}\n")
            self.btn_send.config(state=DISABLED)

    def _set_status_indicator(self, state: str) -> None:
        colors = {
            "ok": SUCCESS,
            "busy": WARNING,
            "error": DANGER,
            "idle": SECONDARY,
        }
        self.status_dot.configure(bootstyle=(INVERSE, colors.get(state, SECONDARY)))

    def _update_connection_ui(
        self, connected: bool, status: str = "", *, indicator: str | None = None
    ) -> None:
        if connected:
            self.btn_connect.config(text="Odpojit", bootstyle=OUTLINE)
            self.btn_audit.config(state=NORMAL if not self._busy else DISABLED)
        else:
            self.btn_connect.config(text="Připojit SSH", bootstyle=PRIMARY)
            self.btn_audit.config(state=DISABLED)

        if indicator:
            self._set_status_indicator(indicator)
        elif connected:
            self._set_status_indicator("ok")
        elif not self._busy:
            self._set_status_indicator("idle")

        if status:
            self.status_var.set(status)
        elif not connected:
            self.status_var.set("Nepřipojeno")

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        if busy:
            self._set_status_indicator("busy")
            self.progress.pack(side=LEFT, padx=(20, 0))
            self.progress.start(10)
        elif self.ssh and self.ssh.connected:
            self._set_status_indicator("ok")
            self.progress.stop()
            self.progress.pack_forget()
        else:
            self._set_status_indicator("idle")
            self.progress.stop()
            self.progress.pack_forget()
        if not self._connecting:
            self.btn_connect.config(
                state=DISABLED if busy else NORMAL
            )
        self.btn_audit.config(
            state=NORMAL if not busy and self.ssh and self.ssh.connected else DISABLED
        )
        self.btn_send.config(state=DISABLED if busy or not self.chat else NORMAL)
        if status:
            self.status_var.set(status)

    def _on_connect(self) -> None:
        if self._busy or self._connecting:
            return

        if self.ssh and self.ssh.connected:
            self.ssh.close()
            self.ssh = None
            self._update_connection_ui(False, "Odpojeno")
            return

        self._connecting = True
        self.btn_connect.config(state=DISABLED)
        self.btn_audit.config(state=DISABLED)
        self.status_var.set(
            f"Připojuji {self.ssh_config.user}@{self.ssh_config.host}…"
        )

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
                    self.btn_connect.config(state=NORMAL)

                self._run_on_ui(on_ok)
            except Exception as exc:
                err = format_ssh_error(exc, self.ssh_config)

                def on_err() -> None:
                    self._connecting = False
                    self.ssh = None
                    self._update_connection_ui(
                        False, "SSH — nepřipojeno", indicator="error"
                    )
                    self.btn_connect.config(state=NORMAL)
                    messagebox.showerror("SSH připojení", err)

                self._run_on_ui(on_err)

        threading.Thread(target=work, daemon=True).start()

    def _on_run_audit(self) -> None:
        if self._busy:
            return
        if not self.ssh or not self.ssh.connected:
            messagebox.showwarning(
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
                    self.btn_export.config(state=NORMAL)
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
                    messagebox.showerror("Audit", err)

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
        self.overview_text.config(state=NORMAL)
        self.overview_text.delete("1.0", END)
        self.overview_text.insert(END, text)
        self.overview_text.config(state=DISABLED)

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
        self._send_chat_message(build_try_prompt(rec), status="Diagnostika…")

    def _on_apply_fix(self, rec: Recommendation) -> None:
        if self._busy or not self.chat:
            return
        if not messagebox.askyesno(
            "Vyřešit problém",
            f"{rec.problem}\n\n"
            "HanzAgent připraví plán opravy v chatu.\n"
            "Příkazy na Pi zatím neprovádí automaticky — "
            "každý krok musíš schválit.\n\n"
            "Pokračovat?",
        ):
            return
        self._send_chat_message(build_fix_prompt(rec), status="Příprava opravy…")

    def _send_chat_message(self, msg: str, status: str = "AI odpovídá…") -> None:
        if self._busy or not self.chat:
            return
        self.chat_input.delete(0, END)
        self._append_chat("user", f"{msg}\n")
        self._set_busy(True, status)

        def work() -> None:
            try:
                reply = self.chat.ask(msg)

                def on_ok() -> None:
                    self._append_chat("assistant", f"{reply}\n")
                    self._set_busy(False, "Připraveno")

                self._run_on_ui(on_ok)
            except Exception as exc:
                err = str(exc)

                def on_err() -> None:
                    self._append_chat("error", f"{err}\n")
                    self._set_busy(False, "Chyba AI")

                self._run_on_ui(on_err)

        threading.Thread(target=work, daemon=True).start()

    def _show_report(self, md: str) -> None:
        self.report_text.config(state=NORMAL)
        self.report_text.delete("1.0", END)
        self.report_text.insert(END, md)
        self.report_text.config(state=DISABLED)

    def _on_export(self) -> None:
        if not self.current_report_md:
            messagebox.showinfo("Export", "Nejdřív spusť audit.")
            return
        default_name = f"hanzhub_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Vše", "*.*")],
            initialfile=default_name,
        )
        if path:
            Path(path).write_text(self.current_report_md, encoding="utf-8")
            messagebox.showinfo("Export", f"Uloženo:\n{path}")

    def _append_chat(self, role: str, text: str) -> None:
        self.chat_text.config(state=NORMAL)
        prefixes = {
            "user": "Ty: ",
            "assistant": "HanzAgent: ",
            "system": "",
            "error": "Chyba: ",
        }
        prefix = prefixes.get(role, "")
        if prefix:
            self.chat_text.insert(END, prefix, role)
        self.chat_text.insert(END, text, role)
        self.chat_text.see(END)
        self.chat_text.config(state=DISABLED)

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
        self.chat_text.config(state=NORMAL)
        self.chat_text.delete("1.0", END)
        self.chat_text.config(state=DISABLED)
        self._append_chat("system", "Nová konverzace.\n")

    def destroy(self) -> None:
        if self.ssh:
            self.ssh.close()
        super().destroy()


def main() -> None:
    app = HanzAuditApp()
    app.mainloop()
