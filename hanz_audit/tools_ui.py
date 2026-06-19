from __future__ import annotations

from pathlib import Path

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from ttkbootstrap.dialogs import Messagebox

from hanz_audit.custom_tools import (
    CustomTool,
    ToolParameter,
    builtin_tool_catalog,
    load_all_tools,
    load_custom_tools,
    save_custom_tools,
)
from hanz_audit.v1_tools import v1_internal_tools, v1_tool_catalog


class ToolsManagerWindow(tb.Toplevel):
    def __init__(
        self,
        parent: tb.Window,
        tools_path: Path,
        *,
        v1_pack_path: Path | None = None,
        remote_tools_root: str | None = None,
        on_saved=None,
    ) -> None:
        super().__init__(parent)
        self.tools_path = tools_path
        self.v1_pack_path = v1_pack_path
        self.remote_tools_root = remote_tools_root
        self.on_saved = on_saved
        self._selected_id: str | None = None

        self.title("Knihovna nástrojů — HanzAgent")
        self.geometry("920x620")
        self.minsize(760, 520)
        self.transient(parent)

        intro = tb.Label(
            self,
            text=(
                "Vlastní nástroje (config/custom_tools.yaml) + balíček hanz-agent-tools-v1. "
                "Agent volá custom_<id>. Skripty v1 běží z /opt/agentAdmin/tools na Pi — "
                "nainstaluj podle hanz-agent-tools-v1/README.md nebo scripts/pi-install-agentAdmin.sh."
            ),
            wraplength=860,
            bootstyle=SECONDARY,
            padding=(12, 10),
        )
        intro.pack(fill=X)

        body = tb.Panedwindow(self, orient=HORIZONTAL)
        body.pack(fill=BOTH, expand=True, padx=12, pady=(0, 8))

        left = tb.Frame(body, padding=4)
        right = tb.Frame(body, padding=4)
        body.add(left, weight=2)
        body.add(right, weight=3)

        tb.Label(left, text="Seznam nástrojů", font=("Segoe UI", 11, "bold")).pack(anchor=W)
        list_frame = tb.Frame(left)
        list_frame.pack(fill=BOTH, expand=True, pady=(6, 0))

        cols = ("id", "name", "level", "source")
        self.tree = tb.Treeview(list_frame, columns=cols, show="headings", height=18, bootstyle=INFO)
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Název")
        self.tree.heading("level", text="Úroveň")
        self.tree.heading("source", text="Zdroj")
        self.tree.column("id", width=180)
        self.tree.column("name", width=160)
        self.tree.column("level", width=60, anchor=CENTER)
        self.tree.column("source", width=80)
        scroll = tb.Scrollbar(list_frame, orient=VERTICAL, command=self.tree.yview, bootstyle=ROUND)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        btn_row = tb.Frame(left)
        btn_row.pack(fill=X, pady=(8, 0))
        tb.Button(btn_row, text="Nový", bootstyle=PRIMARY, command=self._new_tool, width=10).pack(
            side=LEFT, padx=(0, 6)
        )
        tb.Button(btn_row, text="Smazat", bootstyle=SECONDARY, command=self._delete_tool, width=10).pack(
            side=LEFT
        )

        tb.Label(right, text="Detail / editor", font=("Segoe UI", 11, "bold")).pack(anchor=W)
        form = tb.Frame(right)
        form.pack(fill=X, pady=(6, 8))
        tb.Label(form, text="ID:").grid(row=0, column=0, sticky=W, pady=2)
        self.ent_id = tb.Entry(form, width=36)
        self.ent_id.grid(row=0, column=1, sticky=EW, pady=2, padx=(8, 0))
        tb.Label(form, text="Název:").grid(row=1, column=0, sticky=W, pady=2)
        self.ent_name = tb.Entry(form, width=36)
        self.ent_name.grid(row=1, column=1, sticky=EW, pady=2, padx=(8, 0))
        tb.Label(form, text="Úroveň:").grid(row=2, column=0, sticky=W, pady=2)
        self.spn_level = tb.Spinbox(form, from_=0, to=3, width=6)
        self.spn_level.grid(row=2, column=1, sticky=W, pady=2, padx=(8, 0))
        tb.Label(form, text="Popis:").grid(row=3, column=0, sticky=NW, pady=2)
        self.txt_desc = tb.Text(form, height=3, width=40, font=("Segoe UI", 10))
        self.txt_desc.grid(row=3, column=1, sticky=EW, pady=2, padx=(8, 0))
        tb.Label(form, text="Příkaz:").grid(row=4, column=0, sticky=NW, pady=2)
        self.txt_cmd = tb.Text(form, height=6, width=40, font=("Consolas", 10))
        self.txt_cmd.grid(row=4, column=1, sticky=EW, pady=2, padx=(8, 0))
        form.columnconfigure(1, weight=1)

        tb.Label(
            right,
            text="Parametry (volitelné): jeden řádek = name|typ|popis|required(0/1), např. unit|string|systemd unit|1",
            bootstyle=SECONDARY,
            font=("Segoe UI", 9),
        ).pack(anchor=W)
        self.txt_params = ScrolledText(right, height=4, font=("Consolas", 10), bootstyle="round")
        self.txt_params.pack(fill=X, pady=(4, 8))

        actions = tb.Frame(right)
        actions.pack(fill=X)
        tb.Button(actions, text="Použít do formuláře", bootstyle=SECONDARY, command=self._apply_form_to_tool).pack(
            side=LEFT, padx=(0, 8)
        )
        tb.Button(actions, text="Uložit YAML", bootstyle=PRIMARY, command=self._save_all).pack(side=LEFT)

        self._custom_tools: list[CustomTool] = []
        self._refresh_list()
        self.grab_set()

    def _refresh_list(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for b in builtin_tool_catalog():
            self.tree.insert("", END, iid=f"sys:{b['id']}", values=(b["id"], b["name"], b["level"], b["source"]))
        for b in v1_tool_catalog(self.v1_pack_path):
            self.tree.insert(
                "",
                END,
                iid=f"v1:{b['id']}",
                values=(b["openai_name"], b["name"], b["level"], b["source"]),
            )
        self._custom_tools = load_custom_tools(self.tools_path)
        for t in self._custom_tools:
            self.tree.insert(
                "",
                END,
                iid=f"custom:{t.id}",
                values=(t.openai_name, t.name, t.level, "vlastní"),
            )

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if not iid.startswith("custom:"):
            self._selected_id = None
            if iid.startswith("v1:"):
                tool_id = iid.split(":", 1)[1]
                all_tools = load_all_tools(
                    self.tools_path,
                    v1_pack_path=self.v1_pack_path,
                    remote_tools_root=self.remote_tools_root,
                )
                tool = next((t for t in all_tools if t.id == tool_id), None)
                if tool:
                    self._fill_form(tool, readonly=True)
            return
        tool_id = iid.split(":", 1)[1]
        tool = next((t for t in self._custom_tools if t.id == tool_id), None)
        if not tool:
            return
        self._selected_id = tool_id
        self._fill_form(tool)

    def _fill_form(self, tool: CustomTool, *, readonly: bool = False) -> None:
        self.ent_id.config(state=NORMAL)
        self.ent_name.config(state=NORMAL)
        self.spn_level.config(state=NORMAL)
        self.ent_id.delete(0, END)
        self.ent_id.insert(0, tool.id)
        self.ent_name.delete(0, END)
        self.ent_name.insert(0, tool.name)
        self.spn_level.delete(0, END)
        self.spn_level.insert(0, str(tool.level))
        self.txt_desc.delete("1.0", END)
        self.txt_desc.insert("1.0", tool.description)
        self.txt_cmd.delete("1.0", END)
        self.txt_cmd.insert("1.0", tool.command)
        self.txt_params.text.delete("1.0", END)
        lines = []
        for p in tool.parameters:
            req = "1" if p.required else "0"
            lines.append(f"{p.name}|{p.type}|{p.description}|{req}")
        self.txt_params.text.insert("1.0", "\n".join(lines))
        if readonly:
            self.ent_id.config(state=DISABLED)
            self.ent_name.config(state=DISABLED)
            self.spn_level.config(state=DISABLED)
            self.txt_desc.config(state=DISABLED)
            self.txt_cmd.config(state=DISABLED)
            self.txt_params.text.config(state=DISABLED)
        else:
            self.txt_desc.config(state=NORMAL)
            self.txt_cmd.config(state=NORMAL)
            self.txt_params.text.config(state=NORMAL)

    def _parse_params(self) -> list[ToolParameter]:
        raw = self.txt_params.text.get("1.0", END).strip()
        if not raw:
            return []
        params: list[ToolParameter] = []
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2 or not parts[0]:
                continue
            params.append(
                ToolParameter(
                    name=parts[0],
                    type=parts[1] if len(parts) > 1 else "string",
                    description=parts[2] if len(parts) > 2 else "",
                    required=(parts[3] == "1" if len(parts) > 3 else False),
                )
            )
        return params

    def _form_to_tool(self) -> CustomTool | None:
        tool_id = self.ent_id.get().strip().lower().replace("-", "_")
        if not tool_id:
            Messagebox.show_error("Vyplň ID nástroje.", title="Nástroje", parent=self)
            return None
        try:
            level = int(self.spn_level.get())
        except ValueError:
            level = 0
        return CustomTool(
            id=tool_id,
            name=self.ent_name.get().strip() or tool_id,
            description=self.txt_desc.get("1.0", END).strip(),
            level=max(0, min(3, level)),
            enabled=True,
            command=self.txt_cmd.get("1.0", END).strip(),
            parameters=self._parse_params(),
        )

    def _apply_form_to_tool(self) -> None:
        tool = self._form_to_tool()
        if not tool:
            return
        replaced = False
        for i, existing in enumerate(self._custom_tools):
            if existing.id == tool.id:
                self._custom_tools[i] = tool
                replaced = True
                break
        if not replaced:
            self._custom_tools.append(tool)
        self._selected_id = tool.id
        self._refresh_list()
        self.tree.selection_set(f"custom:{tool.id}")

    def _new_tool(self) -> None:
        self._selected_id = None
        self.ent_id.delete(0, END)
        self.ent_name.delete(0, END)
        self.spn_level.delete(0, END)
        self.spn_level.insert(0, "0")
        self.txt_desc.delete("1.0", END)
        self.txt_cmd.delete("1.0", END)
        self.txt_params.text.delete("1.0", END)

    def _delete_tool(self) -> None:
        if not self._selected_id:
            Messagebox.show_warning("Vyber vlastní nástroj ze seznamu.", title="Nástroje", parent=self)
            return
        if not Messagebox.yesno(
            f"Smazat nástroj custom_{self._selected_id}?",
            title="Nástroje",
            parent=self,
            buttons=["Ne:secondary", "Ano:primary"],
            localize=False,
        ) == "Ano":
            return
        self._custom_tools = [t for t in self._custom_tools if t.id != self._selected_id]
        self._selected_id = None
        self._save_all(silent=True)

    def _save_all(self, silent: bool = False) -> None:
        if self.ent_id.get().strip():
            self._apply_form_to_tool()
        try:
            save_custom_tools(self._custom_tools, self.tools_path)
        except OSError as exc:
            Messagebox.show_error(str(exc), title="Uložení", parent=self)
            return
        self._refresh_list()
        if self.on_saved:
            self.on_saved()
        if not silent:
            Messagebox.ok(
                f"Uloženo do:\n{self.tools_path}\n\nAgent načte nástroje při příštím volání.",
                title="Nástroje",
                parent=self,
            )
