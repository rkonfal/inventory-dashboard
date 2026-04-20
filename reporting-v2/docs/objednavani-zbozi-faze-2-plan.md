# Objednávání zboží, Fáze 2

Datum: 2026-04-20

## Cíl Fáze 2
Vyčistit business vstupy pro objednávací engine tak, aby doporučení nepadala na neobjednatelné, servisní nebo promo položky a aby šlo bezpečně rozlišit Riga vs. Praha logiku.

---

## Proč je Fáze 2 teď další správný krok

Po Fázi 1 už je planning engine popsaný i generovaný v refresh backendu. Největší riziko už není v technice výpočtu, ale v tom, **co do výpočtu vůbec pouštíme**.

Bez Fáze 2 hrozí hlavně:
- doporučení pro dárky, tiskoviny, služby nebo interní SKU
- smíchání standardně objednatelných SKU s výjimkami
- špatné doporučení zdroje, Riga vs. Praha
- slabá auditovatelnost, proč byl produkt zahrnut nebo vyřazen

---

## Výstup Fáze 2

Nová referenční vrstva nad SKU, která přidá minimálně tato pole:

- `itemType`: `product | gift | service | print | internal | promo`
- `orderable`: `true/false`
- `sourceChannel`: `riga | praha | both | unknown`
- `strategicPriority`: `driver | standard | supplement | risky`
- `giftCandidate`: `true/false`
- `excludeFromOrderingReason`: text / null

Doporučený technický výstup:
- `data/current/ordering_reference_data.json`

Aplikace do výpočtů:
- planning engine bude počítat jen nad `orderable=true`
- dárky / služby / interní položky budou z engine defaultně vyloučené
- source recommendation bude respektovat `sourceChannel`
- UI ukáže důvod vyloučení, pokud je SKU skryté z doporučení

---

## Návrh implementace

### 1. Referenční soubor a merge logika
Doplnit konfigurační mapu SKU, ideálně do `config/`:

- `config/ordering_reference_overrides.json`

Struktura po SKU nebo prefixech:

```json
{
  "135467": {
    "itemType": "product",
    "orderable": true,
    "sourceChannel": "both",
    "strategicPriority": "driver",
    "giftCandidate": false
  },
  "000077": {
    "itemType": "print",
    "orderable": false,
    "excludeFromOrderingReason": "brožura"
  }
}
```

### 2. Automatické předvyplnění
Tam, kde metadata nejsou ručně známá, doplnit první heuristiky:
- `SET*` -> promo / set candidate
- `DOPL*` -> service/internal candidate
- `BIOANALYZA*` -> service
- brožury / tiskoviny podle názvu -> print
- nulové nebo nestandardní ceny -> flag k revizi

Heuristiky nesmí být finální pravda, jen bootstrap pro rychlé vyčištění.

### 3. Rozšíření refresh vrstvy
Do `refresh_data.py` přidat:
- loader referenčních override dat
- merge referencí do analytických položek
- export `ordering_reference_data.json`
- filtr pro planning engine

### 4. Rozšíření UI
Na `site/ordering.html` doplnit:
- filtr „jen objednatelné“ / „ukázat vyloučené“
- badge typu SKU
- důvod vyloučení z objednávání
- source-channel badge

---

## Doporučené pořadí práce

### Krok 2.1
Založit `ordering_reference_overrides.json` a naplnit první sadu problematických SKU.

### Krok 2.2
Napojit merge referencí do refresh vrstvy a export `ordering_reference_data.json`.

### Krok 2.3
Filtrovat planning engine podle `orderable` a `itemType`.

### Krok 2.4
Doplnit badge a explainability do UI.

### Krok 2.5
Projít top 100 SKU z kritického doplnění a červené obrátkovosti a ručně je očistit.

---

## Akceptační kritéria

Fáze 2 je hotová, když:
- planning už nenavrhuje zjevně neobjednatelné SKU
- každé vyloučené SKU má dohledatelný důvod
- Riga / Praha doporučení respektuje referenční metadata
- top shortlist pro objednávku je businessově čistší než ve Fázi 1

---

## Co ještě neřešit ve Fázi 2

Do Fáze 2 ještě nepatří:
- přesná logistická kapacita kamionu
- promo simulace 2+1 / DeExpert
- AI textové komentáře
- automatické schvalování objednávky

To patří až do dalších etap po vyčištění katalogu.
