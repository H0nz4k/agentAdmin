# Changelog

Všechny důležité změny v projektu HanzHub Audit jsou zaznamenány v tomto souboru.

## [1.1.0] - 2026-06-19
### Přidáno
- **Moderní GUI:** Kompletní přepis UI do `ttkbootstrap` (moderní vzhled, barvy, padding).
- **Podpora témat:** Možnost změnit vzhled (světlý/tmavý) v `config/agent.yaml` pomocí `ui.theme`.
- **Progress bar:** Animovaný ukazatel průběhu v horní liště během auditu a komunikace s AI.
- **Zobrazení verze:** Verze aplikace se zobrazuje v záhlaví okna.
- **Changelog:** Zaveden soubor `CHANGELOG.md` pro sledování změn.

### Opraveno
- Opravena barva textu v tmavých tématech (nyní je text vždy čitelný na tmavém pozadí).

## [1.0.0] - 2026-06-19
### Přidáno
- **Základní aplikace:** MVP verze s připojením přes SSH.
- **Read-only audit:** Sběr dat o disku, RAM, Dockeru, systemd a portech.
- **AI Analýza:** Automatické vyhodnocení auditu a návrhy řešení.
- **Chat:** Možnost doptávat se AI na detaily z auditu.
- **Akční tlačítka:** Tlačítka "Zkusit vyřešit" a "Vyřešit" pro každé doporučení.
- **Inventura:** Automatické generování `config/services.yaml`.
