# Stahovač

Jednoduchý desktopový správce Instagram profilů. Program si v SQLite pamatuje, které příspěvky už byly jednou viděné nebo zpracované, takže není potřeba porovnávat staré screenshoty feedu.

## Verze 0.2.0

- PySide6 GUI v jednoduchém stylu podobném Latflixu
- seznam Instagram profilů
- první průchod profilu uloží současné příspěvky jako známé
- další průchody označí pouze nově nalezené příspěvky
- přehled příspěvků vybraného profilu
- filtry: nové, stažené, známé, ignorované a chyby
- otevření konkrétního Instagram postu v prohlížeči
- označení vybraných postů jako známé nebo ignorované
- cookies.txt pro přihlášený Instagram
- stažení nových příspěvků přes gallery-dl na pozadí
- smazání souboru z disku nemaže záznam z databáze

## Linux

```bash
./start_app.sh
```

Při prvním spuštění se vytvoří `.venv` a nainstaluje PySide6 + gallery-dl.

V **Nastavení** vyber `cookies.txt` a cílovou složku pro stahování.

## Jak program chápe první synchronizaci

První kontrola profilu je výchozí bod: všechny aktuálně nalezené posty se uloží jako `known`. Až další nově objevené posty dostanou stav `new`.

To znamená, že můžeš dnes přidat starý profil s tisícem příspěvků, program si je zapamatuje, ale nebude je považovat za tisíc novinek.

## Data

Databáze se ukládá do:

```text
data/stahovac.sqlite3
```

Databáze ani stažená média se do GitHubu necommitují.
