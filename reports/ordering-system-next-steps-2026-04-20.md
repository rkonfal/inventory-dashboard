# Objednávkový systém, další kroky po napárování katalogu

## 1. Stav po dnešku

Napárování objednávkového formuláře TianDe na aktuální katalog dopadlo takto:

- přesná shoda SKU: **186**
- shoda po odvození základního SKU (např. bez `HM`, bez variantního suffixu): **72**
- silná shoda jen podle názvu: **2**
- zatím nespárováno: **17**

### Soubory
- `knowledge/tiande_order_packaging_map.json`
- `knowledge/tiande_order_packaging_map.csv`
- `knowledge/tiande_order_packaging_catalog_match.json`
- `knowledge/tiande_order_packaging_catalog_match.csv`

## 2. Co z toho plyne

Máme dost dobrý základ, aby se balení začalo používat přímo v replenishment / objednávkovém enginu.

Prakticky:
- u většiny katalogu už víme, po jakých množstvích se má objednávat
- systém může přestat navrhovat „divná“ kusová množství
- další krok už není ruční mapování, ale zapojení do výpočtu doporučené objednávky

## 3. Doporučené pořadí dalších prací

Tohle navazuje na dřívější plán logistické vrstvy nad replenishment enginem.

### Fáze A, dokončení datové vrstvy
1. **Uzamknout mapování balení jako referenční dataset**
   - načítat `recommended_order_qty`
   - zároveň držet i `order_options`, ne jen největší balení

2. **Vyřešit 17 nepřiřazených položek**
   - část jsou tiskoviny a obalový materiál
   - část jsou variantní nebo starší SKU mimo hlavní katalog
   - rozhodnout, zda:
     - je ignorovat pro hlavní objednávkový engine,
     - nebo je vést jako pomocné / neprodejní položky

3. **Zavést normalizační vrstvu SKU**
   - pravidla typu:
     - odebrat `HM`
     - oddělit variantní suffixy `/01`, `/02`, ...
     - evidovat vztah `supplier_sku -> catalog_code`
   - tím se párování stane opakovatelné i pro další formuláře

### Fáze B, napojení do objednávkového enginu
4. **Roundování návrhu objednávky na povolená balení**
   - engine nejdřív spočítá ideální kusy
   - potom je zaokrouhlí na nejbližší rozumnou objednací jednotku
   - výstup musí ukázat:
     - ideální množství
     - finální objednané množství
     - důvod roundingu

5. **Dvojí režim objednávání**
   - `strict_full_pack`: jen celé balíky / kartony
   - `flexible`: dovolí i menší balení nebo kusy
   - to je důležité pro situace typu:
     - krizové doskladnění
     - běžná velká objednávka

6. **Rozlišit top SKU vs fill-up SKU**
   - top SKU: business priorita, držet dostupnost
   - fill-up SKU: použít hlavně pro doplnění kapacity objednávky
   - fill-up musí respektovat balení a nesmí tlačit ležáky

### Fáze C, logistická vrstva
7. **Kapacitní metadata pro top SKU**
   - počet kusů v kartonu už máme částečně z formuláře
   - doplnit, kde to jde:
     - objem kartonu
     - hmotnost
     - počet kartonů na paletu, pokud bude dostupné

8. **Fallback capacity model pro zbytek**
   - když nebudeme mít přesná logistická data,
   - použít odhad podle typu produktu / balení / ceny / kategorie

9. **Scénáře objednávky podle cílové kapacity**
   - např. malá objednávka / půl kamion / celý kamion
   - systém musí umět říct:
     - kolik business hodnoty objednávka přináší
     - jak moc zaplní cíl
     - co je top-up vs nutné core SKU

## 4. Co bych udělal hned teď v repu

### Bezprostřední implementační úkoly
1. vytvořit loader pro `tiande_order_packaging_catalog_match.json`
2. připojit `order_options` a `recommended_order_qty` do `ordering_reference_data.json`
3. do replenishment výpočtu přidat funkci `roundToAllowedPackSizes()`
4. do UI přidat u SKU sloupce:
   - ideální kusy
   - objednat po
   - finální objednávka
   - typ zaokrouhlení
5. přidat filtr `jen nepřiřazené / nejisté mapování`

## 5. Rozhodnutí, které bude potřeba

Než se to nasadí napevno, bude potřeba potvrdit 3 pravidla:

1. **Má být default objednávka vždy po největším balení?**
   - moje doporučení: ano pro standardní objednávky, ne pro krizové dosklady

2. **Mají se tiskoviny a obalový materiál řešit ve stejném enginu?**
   - moje doporučení: ne, oddělit do pomocné sekce

3. **Jak zacházet s variantními SKU, která se v katalogu slévají na základní kód?**
   - moje doporučení: držet supplier SKU i catalog code zároveň

## 6. Můj doporučený další tah

Nejlepší další krok je:

**napojit mapu balení přímo do `ordering_reference_data.json` a udělat první verzi roundingu objednávky na povolená balení.**

Tím se z dnešní mapy stane okamžitě použitelná část objednávkového systému.
