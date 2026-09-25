# Pornhub Stahovač – požadavky

Tento soubor je průběžný zdroj požadavků pro Pornhub část aplikace. Každý nový explicitní požadavek uživatele se sem má průběžně doplnit, aby se při dalších úpravách neztratil nebo omylem nevrátil zpět.

## Základní principy

- Pornhub je samostatný modul aplikace Stahovač.
- Stabilita má přednost před agresivními experimenty, které by mohly rozbít funkční stahování.
- Identita videa je založená na Pornhub video ID, ne na názvu souboru nebo titulku.
- Identita profilu se opírá o interní Pornhub user/profile ID, pokud ho lze bezpečně získat.
- Stažená videa a pouze „známá“/baseline videa musí zůstat interně rozlišená.
- Jedno nedostupné, smazané nebo soukromé video nesmí shodit celý profil, pokud ostatní položky fungují.

## Profily a odkazy

- Dialog `+ Odkazy` používá samostatné řádky místo jednoho velkého textového pole.
- Ve výchozím stavu má 5 řádků.
- Dole má tlačítka pro přidání dalších `+1`, `+5` a `+10` řádků.
- Každý řádek má vlevo profil/seznam a vpravo volitelné „Poslední známé video“.
- Poslední známé video se ukládá ke konkrétnímu profilu a přežije restart aplikace.
- Po kontrole profilu se uložené referenční video a všechna starší videa označí jako známá; ke stažení zůstanou pouze novější položky.
- U již existujícího profilu lze stejným dialogem doplnit nebo změnit jeho uložené poslední známé video.
- Tlačítko `+ Odkazy` přidává Pornhub profily, seznamy nebo odkazy.
- Nové profily se mají přidávat i během probíhajícího stahování.
- Nové profily se mají přidávat i během `Projít vybrané`.
- Přidání profilu nesmí spouštět stahování samo od sebe.
- Nově přidaný profil se vloží na konec aktuálního pořadí tabulky.
- U profilu se má při prvním úspěšném scanu uložit interní Pornhub ID a záložní videa pro případ přejmenování profilu.
- Při změně názvu/URL profilu se smí URL automaticky opravit jen po ověření stejného interního ID.

## Ruční stahování jednotlivých videí

- Samostatné tlačítko `+ Videa`.
- Otevře dialog podobný `+ Odkazy`.
- Uživatel může vložit 1 až 5 konkrétních Pornhub video URL, každé na vlastní řádek.
- Tato videa se stáhnou rovnou a nepřidávají se jako samostatné profily do hlavní tabulky.
- Platnost URL se před spuštěním zkontroluje.
- Již fyzicky stažená video ID se přeskočí.
- Úspěšně stažené video se zapíše do databáze stažených videí, aby ho profil později rozpoznal jako stažené.
- Až 5 ručně vložených videí se může stahovat současně.

## Paralelní stahování

- Program průběžně hlídá volné místo na cílovém disku, kam se videa stahují.
- Pokud volné místo klesne pod 2 GB, všechna právě běžící stahování se automaticky pozastaví.
- Při automatickém pozastavení kvůli místu se v aplikaci zobrazí stav `Pozastaveno` a varovná hláška s informací o nedostatku místa.
- Tlačítko se přepne na `Pokračovat`; pokračování je dovoleno až ve chvíli, kdy je na cílovém disku opět alespoň 2 GB volného místa.
- Automatické hlídání platí pro celou společnou frontu a všechny právě běžící download procesy.
- Varovná hláška při nedostatku místa nabízí odsouhlasit pokračování pod limitem 2 GB.
- Pokračování pod 2 GB vyžaduje výslovné potvrzení uživatele a vypne ochranu pouze pro aktuální běh stahování; při příštím spuštění stahování je ochrana znovu aktivní.
- Pokud uživatel nejdřív nechá stahování pozastavené, může výjimku později potvrdit přes tlačítko `Pokračovat`.

- Fronta se týká i více vybraných profilů, nejen více videí uvnitř jednoho profilu.
- Existuje jedna společná fronta videí napříč všemi vybranými profily a celkový limit je nejvýše 5 současně stahovaných videí.
- Fronta se nesmí zbytečně zastavit na hranici profilu. Jakmile se uvolní slot, vezme se další video v pořadí fronty, i kdyby už patřilo následujícímu profilu.
- Příklad: u profilu A zbývají poslední 2 videa a ve frontě čeká profil B. Současně mohou běžet 2 videa z A a 3 videa z B, aby všech 5 slotů zůstalo využitých.
- Pořadí fronty zůstává po profilech: nejdřív se do fronty vloží všechna videa profilu A, pak profilu B atd.; překryv mezi profily vznikne přirozeně při uvolňování pěti pracovních slotů.
- Toto chování má platit pro `Stáhnout nové` i `Stáhnout novější…`.
- U profilu se může stahovat až 5 videí současně.
- Paralelní režim se používá jen tehdy, když aplikace zná konkrétní video ID z posledního scanu.
- Pokud bezpečný seznam konkrétních ID není k dispozici, použije se starý sekvenční fallback.
- Pauza musí pozastavit všechny právě běžící download procesy.
- Pokračování musí obnovit všechny právě běžící download procesy.
- Zrušení musí ukončit všechny právě běžící download procesy.
- GUI má u paralelního režimu zobrazovat celkovou rychlost.
- U profilu se nemají paralelní procesy současně přetahovat o jeden yt-dlp archive soubor.
- HTTP chunking, který způsobil stav „zjišťuji rychlost“ bez reálného stahování, se nepoužívá.

## Kontrola a baseline

- V nastavení je volba `Automaticky označit videa kratší než 60 sekund jako známá`; ve výchozím stavu je zapnutá.
- Při kontrole profilu se video se známou délkou kratší než 60 sekund automaticky uloží jako známé/baseline a nebude nabízeno ke stažení.
- Přesně 60sekundové video se nepřeskakuje; pravidlo platí jen pro délku `< 60 s`.
- Pokud Pornhub/yt-dlp při plochém scanu délku videa neposkytne, video se automaticky nepřeskakuje jen na základě odhadu.

- `Projít vybrané` pouze zjistí aktuální obsah a počty, nic nestahuje.
- `Nastavit jako aktuální` označí současný obsah jako známý bez fyzického stahování.
- Známé/baseline položky se v GUI započítávají do zobrazeného „Staženo“, ale interně nejsou totéž co fyzicky stažené soubory.
- U režimu `Stáhnout novější…` se referenční a starší položky označí jako známé.
- Jednotlivá nedostupná videa mají zůstat k případnému pozdějšímu opakování, ne shodit celý profil.

## Tabulka a stav řádků

- Každý řádek má vlevo vlastní viditelný informativní checkbox s fajfkou.
- Checkbox není výběrový: neoznačuje ani neodznačuje řádek a nijak neurčuje, nad kterými profily se provedou akce.
- Checkbox je pouze uživatelský marker a jeho stav se ukládá k profilu, aby přežil refresh i restart aplikace.
- Změna checkboxu nesmí nikdy změnit pořadí řádků ani vyvolat automatické řazení.
- Sloupec checkboxů se nikdy neřadí ani po kliknutí na jeho hlavičku.
- Buňka s checkboxem musí barevně souhlasit se zbytkem řádku, včetně zeleného stavu `Aktuální` a modrého označení výběru řádku.
- Fajfka v označeném checkboxu musí být jasně viditelná.
- Sloupce: Název, Kategorie, Odkaz, Poslední kontrola, Nové, Staženo, Celkem, Stav.
- Stav `Aktuální` má mít celý řádek světle zelené pozadí.
- Označený/vybraný řádek má mít standardní modré označení výběru.
- Řádek pod myší se nesmí podbarvovat. Hover se zobrazuje pouze jemným rámečkem kolem celého řádku.
- Kliknutí do prázdné šedé plochy programu mimo tabulku má spolehlivě zrušit výběr jednoho i více řádků, i když uživatel klikne na pasivní text/štítek v šedé ploše.
- Kliknutí do prázdné plochy uvnitř samotné tabulky (např. pod posledním řádkem, kde není žádná buňka) má také vždy zrušit výběr jednoho i více řádků.
- Kliknutí do prázdné části pásu kategorií mezi poslední záložkou kategorie a tlačítkem `+ Kategorie` má také zrušit výběr řádků; kliknutí na skutečnou záložku kategorie se chová normálně jako přepnutí filtru.
- Požadavky musí být zobrazitelné přímo v aplikaci; kvůli úspoře místa jsou `Požadavky` i `Nastavení` schované v kompaktní nabídce `⋮` v horní části okna. Dialog požadavků čte průběžný soubor `PORNHUB/POZADAVKY.md`.
- Dole pod tabulkou se zobrazuje počet profilů a počet označených profilů.
- Počet označených profilů se aktualizuje při změně výběru.
- Počet profilů respektuje aktuální filtr/kategorii.
- Samostatná jednotlivá videa se do počtu profilů nepočítají.

## Řazení

- Pořadí řádků se nesmí samo měnit při změně kategorie, data poslední kontroly, stavu, počtu nových nebo stažených položek.
- Pořadí řádků se změní pouze po explicitním kliknutí uživatele na název sloupce.
- Opakované kliknutí na stejný sloupec obrátí směr řazení.
- Po ručním seřazení se má toto aktuální vizuální pořadí zachovat i při následném refreshi dat.
- Filtr nebo přepnutí kategorie řádky pouze skrývá/zobrazuje, nesmí je samovolně přerovnávat.

## Kategorie

- Každý profil může mít jednu kategorii.
- Výchozí kategorie zahrnují Ruined, Femdom a Latex; uživatel může přidávat další.
- Změna kategorie nesmí změnit pozici řádku.
- Filtry kategorií mají pouze omezit zobrazené řádky.

## Stahování nových a novějších

- `Stáhnout nové` stahuje jen položky, které aplikace ještě nezná.
- `Stáhnout novější…` používá konkrétní referenční video.
- Pokud je referenční video v posledním scanu, má se pro výběr novějších položek použít přesné pořadí známých ID.
- Pokud referenční video v posledním scanu není, použije se bezpečnější timestamp/date fallback.
- Změna názvu videa nesmí způsobit opětovné stažení, pokud zůstalo stejné video ID.
- Nový upload stejného obsahu s novým Pornhub video ID se považuje za nové video.

## Ovládání během běžících akcí

- `+ Odkazy` zůstává dostupné během stahování i kontroly profilů.
- Další scan, mazání, změna nastavení nebo druhý hlavní download worker mohou zůstat během konfliktní operace zamčené.
- Ruční `+ Videa` nespouštět souběžně s již běžícím download/scan workerem, dokud nebude výslovně navržena bezpečná více-frontová architektura.

## Databáze a deduplikace

- `downloads` = skutečně fyzicky stažená videa.
- `known_items` = známé/baseline/přeskočené položky.
- `source_items` = poslední známý obsah zdroje/profilu.
- Primární deduplikace je podle video ID.
- yt-dlp archive je druhá ochranná vrstva tam, kde se používá bezpečně.
- Zápis do databáze musí zachovat rozdíl mezi fyzicky staženým a pouze známým obsahem.

## UI

- Hlavní tlačítka zahrnují `+ Odkazy`, `+ Videa`, `Projít vybrané`, `Stáhnout nové`, `Stáhnout novější…`, `Odstranit`.
- Během stahování jsou dostupná tlačítka Pozastavit a Zrušit stahování.
- U paralelního stahování má být vidět agregovaná rychlost a průběh fronty.
- Po dokončení má aplikace jasně uvést počet skutečně stažených videí a relevantní počet přeskočených/známých položek.
