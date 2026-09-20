# REDGIF downloader

Jednoduchý GUI downloader pro RedGIFs postavený na `gallery-dl`.

## Co umí

- přidat jeden nebo více RedGIFs profilů,
- projít profil a zobrazit nalezená RedGIF ID,
- stáhnout pouze položky bez markeru,
- po úspěšném stažení vytvořit prázdný marker `data/markers/<profil>/<ID>.done`,
- marker zůstává i po smazání staženého videa, takže položka se znovu nestáhne,
- každému profilu vytváří vlastní složku ve zvoleném adresáři.

## Spuštění

```bash
chmod +x start_app.sh
./start_app.sh
```

První spuštění vytvoří virtuální prostředí a nainstaluje PySide6 a gallery-dl.
