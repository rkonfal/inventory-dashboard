# Daily 8:00 report, previous-day ops snapshot

## Purpose
A daily morning report delivered at 08:00 Europe/Prague with data for the previous calendar day.

## Time window
- From: 00:00:00 previous day, Europe/Prague
- To: 23:59:59 previous day, Europe/Prague

## Default structure

### 1. Rychlý souhrn
- Objednávky: count and delta vs recent average
- Tržby: previous day total and delta vs recent average
- Expedice / 4PX: total shipped parcels and any blockers
- Alerty: 1 to 3 most important issues

### 2. WPJ, e-shop výkon za včerejšek
- Počet objednávek
- Obrat s DPH
- Průměrná hodnota objednávky
- Počet stornovaných / problematických objednávek
- Top 5 prodaných produktů podle kusů
- Top 5 produktů podle obratu
- Přehled platebních a dopravních metod, pokud budou v datech dostupné
- Krátká poznámka, pokud je v datech něco podezřelé nebo neúplné

### 3. Sklad a dostupnost
- Produkty s nízkým skladem
- Produkty do mínusu / rezervované nad fyzický stav
- Největší skladové pohyby dne
- Doporučení, co hlídat dnes ráno

### 4. 4PX logistika za včerejšek
- Počet zásilek celkem
- Rozpad podle TIANDE_CZ / TIANDE_SK
- Rozpad podle dopravce, Česká pošta / Packeta / DPD
- Spotřeba obalového materiálu podle velikostí S, M, L, XL, XXL, OVERSIZE
- Kritické zásoby obalového materiálu a odhad, za kolik dní dojdou
- Chybějící report nebo datová anomálie, pokud něco nesedí

### 5. Dnešní priority
- 2 až 5 stručných doporučení, na co se dnes zaměřit

## Tone
- Calm, concise, decision-oriented
- No filler, no vague optimism
- Always call out missing data explicitly

## Delivery rules
- Delivery time: 08:00 Europe/Prague every day
- Report must always cover the previous day only
- If one source is missing, the report still sends, but starts with a visible warning

## Example header
**Ranní report, včerejšek (14. 4. 2026)**

## Confirmed 4PX data path
4PX can be pulled directly through the live Open API, so this part is now unblocked.

Verified on 2026-04-15:
- Warehouse list endpoint works: `com.basis.warehouse.getlist`
- Inventory endpoint works: `fu.wms.inventory.get`
- Outbound parcels endpoint works: `fu.wms.outbound.getlist`
- Prague warehouse code is available as `CZPRGA`
- Both TIANDE_SK and TIANDE_CZ credentials returned live data successfully

Practical use for the morning report:
- Previous-day shipped parcels can come from `fu.wms.outbound.getlist`
- Current warehouse stock and low-stock checks can come from `fu.wms.inventory.get`
- Carrier and packaging views can be derived from outbound data plus the existing packaging logic/dashboard assumptions

## Blocking reality today
This report format is ready, but live automation still needs:
1. WPJ refresh script or dashboard source files
2. A small 4PX extraction layer that pulls previous-day parcel and inventory data into a stable local dataset
3. Cron setup on the host once the data path is verified end to end
