# Objednávání zboží, návrh uchopení

Datum: 2026-04-19

## Cíl
Navrhnout nový dashboard a analytický modul `Objednávání zboží`, který naváže na stávající dashboard `SKLAD` v `reporting-v2`, rozšíří pohled na 2 roky historie a bude sloužit jako rozhodovací podklad pro nákup a skladové řízení.

---

## 1. Konkrétní technická architektura

### Doporučený princip
Neřešit to jako chatového AI asistenta v první fázi, ale jako **výpočetní a rozhodovací vrstvu** nad existujícím reportingem. AI vrstvu je vhodné použít až jako komentář a vysvětlovač nad spočítanými variantami.

### Doporučené vrstvy

#### A. Ingest / zdroje dat
Stávající zdroje, na které navázat:
- WPJ GraphQL, produkty, objednávky, sklad
- 4PX / externí skladová vrstva tam, kde pomáhá zpřesnit dostupnost
- existující `refresh_data.py`
- existující výstupy:
  - `data/current/combined_inventory_overview.json`
  - `data/current/inventory_analytics_365d.json`
  - `data/current/wpj_products.json`

Nové nebo rozšířené vstupy:
- historie objednávek za 730 dní
- historie naskladnění, pokud je dostupná
- ceník nákupních cen
- tabulka akcí 2+1 / DeExpert, pokud není dostupná přes API, tak ruční import JSON/CSV
- logistická metadata: kusů na paletu, váha, objem, případně jednoduchý koeficient kapacity

#### B. Normalizační vrstva
Do `refresh_data.py` nebo do samostatných helperů přidat novou datovou větev:
- sjednocení produktového kódu
- sjednocení datumu objednávky
- rozpad historie prodejů po dnech / týdnech / měsících
- příprava časových řad po produktu
- označení 1 Kč dárků z objednávek
- příprava korelací produktů, které často chodí společně

Doporučení: oddělit tento kus do nových skriptů, aby `refresh_data.py` nezůstal jako jeden přerostlý soubor.

Například:
- `scripts/build_inventory_analytics_730d.py`
- `scripts/build_ordering_recommendations.py`
- `scripts/build_ordering_monitoring.py`

`refresh_data.py` by je pak jen orchestrace spouštěla.

#### C. Analytická vrstva
Nové výstupy do `data/current/`:
- `inventory_analytics_730d.json`
- `ordering_monitoring.json`
- `ordering_recommendations.json`
- `ordering_reference_data.json`

Obsah:
- **inventory_analytics_730d.json**
  - 30 / 90 / 180 / 365 / 730 denní metriky
  - sezonalita proti loňsku
  - trend obrátkovosti
  - pokrytí zásob
  - dead stock / low cover / overstock signály
- **ordering_monitoring.json**
  - alerty a prioritizace
  - změna zóny obrátkovosti
  - riziko vyprodání do další objednávky
  - nečekané odchylky od obvyklého prodeje
- **ordering_recommendations.json**
  - výstupy pro předobjednávkový režim
  - doporučené množství po produktu
  - varianty objednávky
  - odhad dopadů jednotlivých variant
- **ordering_reference_data.json**
  - pomocné mapy: palety, objem, balení, typ dodavatele, jen Praha / Riga / oba kanály

#### D. Rozhodovací engine
Doporučené moduly logiky:

1. **Forecast engine**
- základ: vážený průměr 90 dní
- stabilizace: kontrola proti 365 dní
- sezonalita: korekce proti stejnému období loni
- promo multiplier: jen tam, kde jsou spolehlivá historická data nebo ruční zadání

2. **Replenishment engine**
- potřeba do další objednávky
- safety stock podle variability prodeje
- risk score vyprodání
- Praha fallback flag

3. **Action evaluator**
- 2+1 simulace: bez akce / dárek prodat / dárek spotřebovat jako gift
- DeExpert simulace: jednotlivě vs. set
- porovnání úspory proti riziku ležáku a skladovacímu nákladu

4. **Truck composer**
- priorita: kritické doplnění -> běžné doplnění -> výhodné akce -> doplnění pro naplnění kapacity
- constraint: palety / objem / váha
- výstup 2 až 3 varianty

#### E. Prezentační vrstva
Nová stránka v `site/`:
- `site/ordering.html`

Napojení na:
- `site/assets/styles.css`
- společné JS utility stejně jako u ostatních dashboardů

### Shrnutí architektury
Nejlepší je stavět to jako:

**WPJ + SKLAD data -> normalizace -> analytické JSON výstupy -> dashboard Objednávání zboží -> AI vysvětlení nad výsledky**

To je nejčitelnější, auditovatelné a dobře rozšiřitelné.

---

## 2. Seznam potřebných dat z WPJ / API

### A. Nutné minimum pro MVP
Bez toho se rozumné doporučení nepostaví:

#### Produkty
- `productId`
- `code`
- `title`
- katalogová cena
- nákupní cena
- kategorie
- značka / řada, pokud existuje
- stav aktivní / neaktivní

#### Aktuální sklad
- aktuální zásoba po produktu
- rozpad podle skladu / pohledu, pokud je potřeba
- rezervace, pokud existují
- dostupnost v relevantních skladech

#### Historie objednávek za 730 dní
Po každé objednávce potřebujeme:
- datum a čas
- ID objednávky
- položky
- kód produktu
- počet kusů (`pieces`)
- prodejní cenu položky
- rozpoznání 1 Kč položek
- země / trh, pokud chceme CZ vs SK chování

#### Produktové dimenze pro logistiku
- kusů v kartonu
- kusů na paletě
- váha
- objem

Pokud to není v API, je potřeba ruční referenční tabulka.

### B. Silně doporučená data pro přesnější doporučení

#### Historie naskladnění
- datum příjmu
- produkt
- množství
- nákupní cena
- zdroj, Riga / Praha

#### Dodavatelská metadata
- standardní lead time Riga
- standardní lead time Praha
- omezení sortimentu podle kanálu
- příznak, že produkt jde jen přes Prahu

#### Promo / akce
- typ akce
- začátek / konec
- dotčené produkty
- parametry slevy
- vazby v setu

### C. Datové atributy, které doporučuji vést i když dnes chybí
Pokud dnes nejsou, vyplatí se doplnit manuálně:
- `source_channel`: Riga / Praha / oba
- `supplement_only_prague`: ano/ne
- `capacity_units`: jednoduchý koeficient zaplnění kamionu
- `strategic_priority`: tahoun / standard / doplněk / rizikový produkt
- `gift_candidate`: vhodný jako dárek

### D. Co už pravděpodobně máme k dispozici
Ze současného stavu reportingu už zjevně existují nebo jsou blízko:
- produktový katalog
- aktuální sklad
- 365 denní analytika obrátkovosti
- prodejní historie po produktu
- signály low cover / dead stock

To je dobrý základ. Hlavní rozšíření je:
- z 365 na 730 dní
- doplnění forecast logiky
- doplnění objednávacího a akčního modulu

---

## 3. Návrh první verze dashboardu po blocích

### Název stránky
**Objednávání zboží**

### Hlavní princip UX
Nejdřív ukázat, **co je potřeba rozhodnout**, až potom detailní tabulky.

### Blok 1. Hero přehled pro provozní ředitelku
Horní KPI pás:
- počet produktů s rizikem vyprodání do další objednávky
- počet produktů v červené obrátkovosti
- odhad peněz v ležácích
- odhad nutných dokupů z Prahy
- doporučená velikost příští objednávky, celý / půl kamion / menší doplnění

### Blok 2. Aktivní alerty
Seznam priorit od nejdůležitějších:
- dojde před další Rigou
- přesun ze zelené do oranžové
- přesun do červené
- nečekaný propad / nárůst prodeje
- kandidát na Praha emergency order

Každý alert by měl mít:
- produkt
- důvod
- dopad
- doporučený krok

### Blok 3. Předobjednávkový režim
Formulář / ovládací panel:
- datum plánované objednávky
- datum očekávaného doručení
- typ kapacity, celý nebo půl kamion
- zapnout / vypnout uvažování akcí
- zapnout / vypnout Praha fallback

Pod tím výstup:

#### Varianta A, konzervativní
- doplnit to nejnutnější
- minimální riziko přebytků

#### Varianta B, vyvážená
- standardní doporučení
- rozumný kompromis cash / kapacita / zásoba

#### Varianta C, plný kamion
- naplnit kapacitu na produktech s dobrou obrátkovostí
- vyšší využití dopravy, vyšší zásoba

Pro každou variantu ukázat:
- cena objednávky
- využití kapacity
- počet kritických produktů po doplnění
- odhad rizika Praha dokupů
- počet produktů s rizikem červené obrátkovosti po 1 / 2 / 3 / 4 měsících

### Blok 4. Tabulka doporučených položek
Sloupce:
- kód
- název
- aktuální sklad
- forecast 30 / 60 / 90 dní
- safety stock
- doporučené minimum
- doporučené optimum
- doporučené maximum
- navržený zdroj, Riga / Praha
- důvod doporučení

### Blok 5. Akce a promo simulace
Sekce zvlášť:
- dostupné 2+1 akce
- dostupné DeExpert akce
- doporučení využít / nevyužít
- odůvodnění čísly

U každé akce:
- úspora
- přidané zásoby
- riziko ležáku
- návratnost

### Blok 6. Obrátkovost a trend
Grafická analytika:
- zelená / oranžová / červená distribuce
- největší zhoršení posledních 30 dní
- největší zlepšení posledních 30 dní
- nejdražší ležáky

### Blok 7. Detail produktu
Po rozkliknutí:
- historie prodejů za 24 měsíců
- sezonalita
- skladová křivka, pokud bude dostupná
- forecast další 4 měsíce
- doporučené objednací množství
- zda je vhodný do naplnění kamionu
- zda je vhodný jako dárek / promo kandidát

### Co do V1 ještě nedávat
Aby se první verze nerozpadla pod vlastní vahou, do V1 bych nedával:
- volný chat s agentem jako hlavní UX
- složité lineární programování, pokud nemáme kvalitní data o kapacitě
- plně automatické rozhodování bez lidského potvrzení

---

## 4. Realistický implementační plán po etapách

### Etapa 0. Datový audit, 1 až 2 dny
Cíl:
- ověřit, co přesně jde vytáhnout z WPJ za 730 dní
- ověřit, zda máme naskladnění, nákupní ceny a akce
- identifikovat chybějící referenční tabulky

Výstup:
- checklist dostupných dat
- seznam chybějících polí
- rozhodnutí, co bude v MVP počítané a co zatím ruční

### Etapa 1. Rozšíření analytiky na 730 dní, 2 až 4 dny
Cíl:
- rozšířit stávající skladovou analytiku z 365 na 730 dní
- dopočítat nové metriky

Dodávka:
- `inventory_analytics_730d.json`
- trend obrátkovosti
- sezonalita
- low cover / dead stock / overstock nad 2 roky

Tohle samo o sobě už dá první business hodnotu.

### Etapa 2. Monitoring obrátkovosti a alertů, 2 až 3 dny
Cíl:
- postavit modul D, průběžný monitoring

Dodávka:
- `ordering_monitoring.json`
- nový dashboard blok alertů
- prioritizace problémů
- týdenní přehled připravený pro případný report

Tohle je vhodný první produkční milestone.

### Etapa 3. Základní doporučení objednávky, 3 až 5 dní
Cíl:
- postavit první verzi modulu B bez složité promo optimalizace

Dodávka:
- forecast spotřeby
- safety stock
- doporučené množství do další objednávky
- Praha fallback upozornění
- 2 až 3 varianty objednávky

Tohle je klíčový business milestone.

### Etapa 4. Promo a akční logika, 3 až 6 dní
Cíl:
- přidat 2+1 a DeExpert simulace

Dodávka:
- model akční výhodnosti
- doporučení využít / nevyužít
- riziko ležáku z akčních položek

Tahle etapa dává smysl až po potvrzení, že máme kvalitní vstupní data.

### Etapa 5. Finální UX a vysvětlovací AI vrstva, 2 až 4 dny
Cíl:
- doplnit slovní komentáře, shrnutí a vysvětlivky

Dodávka:
- generované shrnutí proč systém doporučil danou variantu
- stručný komentář pro ředitelku
- případně tlačítko “vysvětli doporučení”

### Doporučené pořadí release

#### Release 1
- 730d analytika
- monitoring obrátkovosti
- alerty

#### Release 2
- předobjednávkový režim
- varianty objednávky
- doporučené množství

#### Release 3
- promo optimalizace
- AI komentáře

---

## Doporučení k řízení projektu

### Co bych doporučil hned teď
1. Udělat datový audit dostupnosti 730d historie a nákupních dat.
2. Potvrdit, jak budeme modelovat kapacitu, palety nebo zjednodušený koeficient.
3. Rozhodnout, zda akce 2+1 / DeExpert půjdou z API nebo z ručního vstupu.
4. Spustit MVP přes monitoring a analytiku, ne rovnou přes kompletní optimalizaci kamionu.

### Proč tímto směrem
Protože největší hodnota přijde nejdřív z toho, že:
- uvidíte dřív problémy v obrátkovosti
- uvidíte reálné riziko vyprodání
- budete mít doporučené doplnění podle dat

Až potom má smysl přidávat složitější akční logiku.

---

## Závěr
Nejlepší řešení je postavit `Objednávání zboží` jako **nový rozhodovací dashboard nad existujícím SKLAD modulem**, nikoliv jako samostatný izolovaný projekt.

Architektura má být:
- jednoduchá na audit
- postavená na JSON analytických výstupech
- modulární
- postupně rozšiřitelná od obrátkovosti až po optimalizaci objednávky a promo akcí

První produkční cíl by měl být:
**vidět rizika, trend obrátkovosti a doporučené doplnění na základě 2 let historie**.

Teprve druhý krok je plná optimalizace objednávky a třetí krok AI komentáře.
