# Finální návrh podoby daily reportu

Datum návrhu: 2026-04-16
Stav: návrh ke schválení
Určeno pro: Ruda / ranní operativní řízení Království TianDe

## 1. Co má report dělat

Daily report nemá být další dashboard. Má být ranní rozhodovací vrstva.

Cíl v 8:00 ráno:
- během 20 až 40 sekund říct, jestli je včerejšek dobrý, slabší, nebo problémový
- během 1 minuty ukázat, co je potřeba dnes řešit
- jedním klikem otevřít detail, pokud je potřeba jít hlouběji

Proto navrhuju oddělit report na dvě vrstvy:

1. **Primární vrstva, Telegram message**
   - krátká, mobilní, rozhodovací
   - bez technického balastu
   - vždy čitelná sama o sobě

2. **Sekundární vrstva, detail page**
   - přehledná webová stránka na jednu pracovní obrazovku
   - stejné pořadí informací jako v Telegramu
   - pod tím drill-down a technická důvěra dat

---

## 2. Finální rozhodnutí o formě

### Doporučený formát
- **Textový ranní report do Telegramu** jako hlavní výstup
- **Odkaz na detailní stránku** jako druhá vrstva
- **Tmavý executive layout** jako výchozí vizuální směr detailní stránky

### Proč právě takto
- Telegram je nejrychlejší ranní spotřeba na mobilu
- detail page je správná pro dohledání kontextu, ne jako první čtení dne
- tmavý layout z preview působí víc jako command center a má lepší scanning než světlejší portálová varianta

---

## 3. Finální informační architektura reportu

Pořadí sekcí bude vždy stejné:

### A. Hlavička + stav dat
Jedna horní řádka, která hned řekne:
- za jaký den report je
- jestli jsou data kompletní
- jestli nějaký zdroj chybí

Formát:
- `**Ranní report, včerejšek (15. 4. 2026)**`
- pod tím jeden stavový řádek:
  - `✅ WPJ + 4PX kompletní`
  - nebo `⚠️ Report je částečný, chybí WPJ logistická vrstva`

### B. Executive snapshot
4 nejdůležitější čísla v prvním bloku:
- objednávky
- tržby s DPH
- expedované zásilky
- aktuální dostupný sklad CZ + SK

U objednávek a tržeb vždy ukázat i srovnání proti 7dennímu průměru.

### C. Co pálí dnes
Nejdůležitější blok celého reportu.
Maximálně 3 alerty.
Každý alert musí být akční, ne jen popisný.

Příklady:
- 6 problematických nebo stornovaných objednávek
- 2 včera prodané produkty jsou teď na kritickém skladu
- 10 skladových pozic je v mínusu

### D. E-shop výkon za včerejšek
Jen to, co pomáhá rozhodnutí.

Povinné položky:
- počet objednávek
- obrat s DPH
- AOV
- problematické / stornované objednávky
- top 3 produkty podle kusů
- top 3 produkty podle obratu
- top platební metoda
- top dopravní metoda

Poznámka:
- v detailu může zůstat top 5
- v Telegramu jen top 3, aby to bylo čitelné

### E. Sklad + logistika
Spojený blok, protože ráno jde hlavně o dopad na dnešní operativu.

Povinné položky:
- počet zásilek celkem
- rozpad CZ / SK
- stav skladu CZ / SK
- 2 nejkritičtější low-stock produkty z včerejšího prodeje
- 3 největší mínusové skladové pozice
- 2 nejrizikovější expirace

### F. Dnešní priority
Maximálně 4 body.
Každý bod musí být formulovaný jako úkol.

Příklad:
- prověřit 6 problematických objednávek
- rozhodnout o doprodeji zásoby 40203 před expirací
- dořešit POP00060, aktuálně 0 ks

### G. Detail
Na konci jen jeden řádek:
- `Detail: https://...`

---

## 4. Co do reportu naopak nepatří

Do primárního ranního reportu nepatří:
- technický monitoring zdrojů
- tabulka source status
- vysvětlení API nebo refresh logiky
- marketing a finance za měsíc
- dlouhé seznamy SKU bez priority
- opakování stejných čísel ve více blocích
- obecné pozitivní řeči typu „vše běží dobře“, pokud to nenese rozhodovací hodnotu

Tyto věci mají být až v detailní stránce, níž nebo ve sbalitelném bloku.

---

## 5. Finální pravidla délky a hustoty

### Telegram message
Cíl:
- mobilně čitelná bez únavy
- žádný odstavec delší než 3 řádky
- maximálně 6 hlavních bloků
- maximálně 3 alerty
- maximálně 4 priority
- v seznamech používat jen nejdůležitější položky

### Detail page
Cíl:
- první obrazovka musí říct vše důležité bez scrollování, nebo s minimálním scrollováním
- spodní část může obsahovat drill-down a důvěryhodnost dat
- první screen nesmí být zaplněný technickou vrstvou

---

## 6. Finální návrh textové podoby Telegram reportu

```text
**Ranní report, včerejšek (DD. M. YYYY)**
✅ WPJ + 4PX kompletní

**1. Executive snapshot**
• Objednávky: X (±Y % vs 7denní průměr)
• Tržby s DPH: X Kč (±Y % vs 7denní průměr)
• Expedice: X zásilek (CZ X / SK X)
• Sklad CZ+SK: X ks

**2. Co pálí dnes**
• Alert 1
• Alert 2
• Alert 3

**3. E-shop včera**
• AOV: X Kč
• Problematické / storno: X / X
• Top kusy: SKU1 X ks, SKU2 X ks, SKU3 X ks
• Top obrat: SKU1 X Kč, SKU2 X Kč, SKU3 X Kč
• Top platba: X
• Top doprava: X

**4. Sklad a logistika**
• Low stock po včerejším prodeji: SKU1 X ks, SKU2 X ks
• Mínusové pozice: SKU1 X ks, SKU2 X ks, SKU3 X ks
• Expirace: SKU1 datum / zásoba, SKU2 datum / zásoba

**5. Dnešní priority**
• Priorita 1
• Priorita 2
• Priorita 3
• Priorita 4

**6. Detail**
• https://...
```

---

## 7. Finální návrh detailní webové stránky

Výchozí vizuální směr:
- tmavý režim jako default
- TianDe / Diamond Plus paleta, ale střídmě
- vysoký kontrast
- výrazná hierarchie, ne dekorace

### Layout první obrazovky

```text
┌────────────────────────────────────────────────────────────┐
│ Ranní report, včerejšek (datum)   ✅ kompletní data       │
│ Krátká věta: co bylo hlavní odchylkou dne                 │
├───────────────┬───────────────┬───────────────┬────────────┤
│ Objednávky    │ Tržby         │ Expedice      │ Sklad      │
│ X             │ X Kč          │ X             │ X ks       │
│ vs 7D avg     │ vs 7D avg     │ CZ/SK         │ CZ/SK      │
├────────────────────────────────┬───────────────────────────┤
│ Co pálí dnes                   │ Dnešní priority          │
│ - alert 1                      │ - priorita 1             │
│ - alert 2                      │ - priorita 2             │
│ - alert 3                      │ - priorita 3             │
├────────────────────────────────┼───────────────────────────┤
│ E-shop včera                   │ Sklad a logistika        │
│ AOV, storna, top kusy, top Kč  │ low stock, mínusy, exp.  │
└────────────────────────────────┴───────────────────────────┘
```

### Co má být až pod prvním screenem
- rozšířené top 5 žebříčky
- detailní rozpad stavů objednávek
- detailní rozpad plateb a doprav
- source status / data health
- technické poznámky a coverage warnings

---

## 8. Pravidla pro alerty a priority

### Alerty
Alert se ukáže jen pokud splní aspoň jednu z podmínek:
- problém přímo ohrožuje dnešní expedici nebo servis
- odchylka proti normálu je významná
- je potřeba rozhodnutí nebo kontrola hned ráno

Pořadí alertů:
1. zákaznický dopad
2. sklad / dostupnost
3. logistika / expirace
4. datová neúplnost

### Priority
Priority nejsou kopie alertů.
Priority mají být přeložené do akce.

Správně:
- Prověřit 6 problematických objednávek z včerejška.

Špatně:
- Bylo 6 problematických objednávek.

---

## 9. Pravidla fallbacku při chybě dat

Když jeden zdroj vypadne, report se pořád pošle.
Ale nahoře musí být čitelné varování.

Příklad:
- `⚠️ WPJ nedostupné, e-shop část je z dnešního reportu vynechaná`
- `⚠️ 4PX outbound neúplné, logistická čísla jsou orientační`

Fallback pravidla:
- chybějící zdroj se nesmí maskovat nulou
- u neúplných dat musí být explicitně napsáno, co chybí
- priorita reportu je důvěryhodnost, ne kosmetická úplnost

---

## 10. Konkrétní ukázka reportu na aktuálních datech

```text
**Ranní report, včerejšek (15. 4. 2026)**
✅ WPJ + 4PX kompletní

**1. Executive snapshot**
• Objednávky: 418 (-7,4 % vs 7denní průměr)
• Tržby s DPH: 600 411 Kč (-16,2 % vs 7denní průměr)
• Expedice: 569 zásilek (CZ 503 / SK 66)
• Sklad CZ+SK: 379 813 ks

**2. Co pálí dnes**
• 6 problematických nebo stornovaných objednávek
• 2 včera prodané produkty jsou teď na kritickém skladu
• 10 skladových pozic je v mínusu

**3. E-shop včera**
• AOV: 1 436 Kč
• Problematické / storno: 6 / 6
• Top kusy: 64404-2 137 ks, 44402-2 128 ks, 61914 114 ks
• Top obrat: 44402-2 35 095 Kč, 64404-2 32 068 Kč, 10118 16 126 Kč
• Top platba: Bezpečná platba online (246)
• Top doprava: Balíkovna (190)

**4. Sklad a logistika**
• Low stock po včerejším prodeji: POP00060 0 ks, 195464 2 ks
• Mínusové pozice: 00102 -8 339 ks, OMA1 -723 ks, SET04 -432 ks
• Expirace: 40203 do 8. 5. (2 277 ks), 52902/03 do 7. 5. (346 ks)

**5. Dnešní priority**
• Prověřit 6 problematických nebo stornovaných objednávek.
• Dořešit POP00060, aktuálně 0 ks.
• Dořešit 195464, aktuálně 2 ks.
• Rozhodnout o doprodeji nebo přesunu nejbližších expirací v 4PX.

**6. Detail**
• https://rkonfal.github.io/diamond-plus-reporting-preview/site/index.html
```

---

## 11. Jednoznačné doporučení ke schválení

Doporučuju schválit tento směr:

- **hlavní ranní výstup = krátký Telegram executive report**
- **detail = tmavá webová stránka s první obrazovkou ve stejném pořadí jako zpráva**
- **bez technického šumu nahoře**
- **bez duplicitních KPI**
- **maximální důraz na alerty, priority a ranní rozhodnutí**

Tohle je podle mě správný kompromis mezi:
- rychlostí čtení
- operativní hodnotou
- důvěryhodností dat
- možností jít do detailu bez přehlcení hlavní ranní zprávy

---

## 12. Co bych po schválení implementoval

1. upravit generátor `morning_report_previous_day.txt` do této přesné struktury
2. přeuspořádat první screen `site/index.html`, aby kopíroval strukturu reportu
3. schovat technickou vrstvu níž pod decision layer
4. nechat detailnější top 5 a source health až v rozšířené části stránky
5. doladit názvy dopravců 4PX z kódů na lidské názvy, pokud mapování bude k dispozici
