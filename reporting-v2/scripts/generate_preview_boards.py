from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

ROOT = Path('/Users/rudolfkonfal/.openclaw/workspace/reporting-v2')
DATA = ROOT / 'data' / 'current'
OUT = ROOT / 'previews'
OUT.mkdir(parents=True, exist_ok=True)

COLORS = {
    'bg': '#f6f1ea',
    'bg_soft': '#efe7dc',
    'sidebar': '#fbf8f2',
    'panel': '#fffdf9',
    'panel_soft': '#faf5ee',
    'line': '#e3d9cb',
    'text': '#2b2230',
    'muted': '#6e6476',
    'faint': '#978f9f',
    'gold': '#c8a24b',
    'plum': '#6b3d63',
    'green': '#2f9a70',
    'red': '#c05a67',
    'blue': '#5078c8',
}

WIDTH = 1600
HEIGHT = 1200
SIDEBAR_W = 270
CONTENT_X = SIDEBAR_W + 32
CONTENT_W = WIDTH - CONTENT_X - 34


def load_font(size, bold=False):
    candidates = [
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold else '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/System/Library/Fonts/Supplemental/Helvetica.ttc',
        '/System/Library/Fonts/SFNS.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

FONT_H1 = load_font(42, bold=True)
FONT_H2 = load_font(24, bold=True)
FONT_BIG = load_font(34, bold=True)
FONT = load_font(20)
FONT_SMALL = load_font(17)
FONT_TINY = load_font(15)
FONT_MICRO = load_font(13)


def fmt_num(value):
    return f"{int(round(float(value))):,}".replace(',', ' ')


def fmt_money(value):
    return f"{fmt_num(value)} Kč"


def wrap_text(draw, content, font, max_width):
    words = str(content).split()
    if not words:
        return ['']
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = current + ' ' + word
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def image_base(active_index=0):
    img = Image.new('RGB', (WIDTH, HEIGHT), COLORS['bg'])
    draw = ImageDraw.Draw(img)
    draw.ellipse((-160, -140, 260, 280), fill='#f0e2c1')
    draw.ellipse((WIDTH - 280, -120, WIDTH + 60, 220), fill='#eeded6')

    draw.rounded_rectangle((0, 0, SIDEBAR_W, HEIGHT), radius=0, fill=COLORS['sidebar'])
    draw.line((SIDEBAR_W, 0, SIDEBAR_W, HEIGHT), fill=COLORS['line'], width=1)

    draw.rounded_rectangle((28, 28, 72, 72), radius=14, fill=COLORS['gold'])
    draw.text((28, 94), 'Diamond Plus', font=FONT_H2, fill=COLORS['text'])
    draw.text((28, 126), 'Reporting V2', font=FONT_MICRO, fill=COLORS['muted'])

    nav_items = ['Portal', 'Sklad', 'Logistika 4PX', 'E-shop / WPJ']
    for idx, item in enumerate(nav_items):
        y = 210 + idx * 58
        active = idx == active_index
        if active:
            draw.rounded_rectangle((14, y - 8, SIDEBAR_W - 14, y + 32), radius=14, fill='#f3ece4', outline=COLORS['line'])
            draw.rounded_rectangle((18, y + 4, 24, y + 20), radius=3, fill=COLORS['gold'])
        draw.text((36, y), item, font=FONT_SMALL, fill=COLORS['text'] if active else COLORS['muted'])

    draw.text((28, HEIGHT - 86), 'Světlý, čistý a zároveň', font=FONT_MICRO, fill=COLORS['muted'])
    draw.text((28, HEIGHT - 64), 'lehce luxury reporting look.', font=FONT_MICRO, fill=COLORS['muted'])
    return img, draw


def round_rect(draw, box, fill, radius=28, outline=COLORS['line']):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def hero(draw, title, subtitle, chips):
    x, y, w, h = CONTENT_X, 28, CONTENT_W, 172
    round_rect(draw, (x, y, x + w, y + h), COLORS['panel'], radius=30)
    draw.ellipse((x + w - 220, y - 50, x + w + 40, y + 210), fill='#f2e4c6')
    draw.ellipse((x + w - 120, y + 40, x + w + 80, y + 240), fill='#eee2da')
    draw.text((x + 28, y + 28), title, font=FONT_H1, fill=COLORS['text'])
    draw.text((x + 28, y + 84), subtitle, font=FONT_SMALL, fill=COLORS['muted'])
    chip_x = x + 28
    chip_y = y + 122
    for label, tone in chips:
        tone_color = COLORS[tone]
        width = int(draw.textlength(label, font=FONT_MICRO)) + 28
        draw.rounded_rectangle((chip_x, chip_y, chip_x + width, chip_y + 28), radius=14, fill='#faf5ee', outline=tone_color)
        draw.text((chip_x + 14, chip_y + 7), label, font=FONT_MICRO, fill=tone_color)
        chip_x += width + 10


def kpi(draw, x, y, w, h, label, value, sub, accent):
    round_rect(draw, (x, y, x + w, y + h), COLORS['panel'], radius=24)
    draw.rounded_rectangle((x + 18, y + 18, x + 88, y + 23), radius=3, fill=COLORS[accent])
    draw.text((x + 22, y + 40), label.upper(), font=FONT_MICRO, fill=COLORS['faint'])
    draw.text((x + 22, y + 74), value, font=FONT_BIG, fill=COLORS['text'])
    lines = wrap_text(draw, sub, FONT_MICRO, w - 44)
    yy = y + 126
    for line in lines[:2]:
        draw.text((x + 22, yy), line, font=FONT_MICRO, fill=COLORS['muted'])
        yy += 18


def section_title(draw, x, y, title, subtitle=None):
    draw.text((x, y), title, font=FONT_H2, fill=COLORS['text'])
    if subtitle:
        draw.text((x, y + 30), subtitle, font=FONT_MICRO, fill=COLORS['muted'])


def bullet_list(draw, x, y, rows, max_width, limit=None, bullet_color='gold', font=FONT_SMALL, line_gap=8):
    rows = rows[:limit] if limit else rows
    yy = y
    for row in rows:
        lines = wrap_text(draw, row, font, max_width - 18)
        draw.ellipse((x, yy + 8, x + 8, yy + 16), fill=COLORS[bullet_color])
        line_y = yy
        for line in lines:
            draw.text((x + 18, line_y), line, font=font, fill=COLORS['text'])
            line_y += font.size + 4
        yy = line_y + line_gap
    return yy


def render_portal():
    summary = json.loads((DATA / 'portal_summary.json').read_text())
    report = json.loads((DATA / 'morning_report_previous_day.json').read_text())
    img, draw = image_base(active_index=0)
    hero(draw, 'Reporting portal, čistší executive směr', 'Světlý přehled s jemným luxury tónem. Méně technický noise, víc klid a čitelnost.', [
        ('hodinový refresh', 'green'),
        (summary['report']['date'], 'gold'),
        ('WPJ live', 'blue'),
    ])

    top_y = 226
    gap = 18
    card_w = (CONTENT_W - gap * 3) // 4
    cards = [
        ('CZ sklad', fmt_num(summary['accounts']['cz']['inventory']['availableStockTotal']), f"{fmt_num(summary['accounts']['cz']['inventory']['items'])} řádků", 'green'),
        ('SK sklad', fmt_num(summary['accounts']['sk']['inventory']['availableStockTotal']), f"{fmt_num(summary['accounts']['sk']['inventory']['items'])} řádků", 'blue'),
        ('WPJ objednávky', fmt_num(summary['wpJ']['orders']), fmt_money(summary['wpJ']['revenueWithVat']), 'gold'),
        ('4PX zásilky', fmt_num(summary['report']['shipments']), 'včerejší okno', 'red'),
    ]
    for idx, card in enumerate(cards):
        kpi(draw, CONTENT_X + idx * (card_w + gap), top_y, card_w, 156, *card)

    col_gap = 18
    left_w = int(CONTENT_W * 0.48)
    right_w = CONTENT_W - left_w - col_gap
    box_y = 410

    round_rect(draw, (CONTENT_X, box_y, CONTENT_X + left_w, 1140), COLORS['panel_soft'], radius=28)
    section_title(draw, CONTENT_X + 24, box_y + 20, 'Rychlý souhrn', 'Ranní report, včerejšek')
    quick_rows = [
        f"Objednávky: {report['eshop']['orders']} ({report['quickSummary']['orders']['deltaPct']} % vs 7denní průměr)",
        f"Tržby s DPH: {fmt_money(report['eshop']['revenueWithVat'])}",
        f"Průměrná hodnota objednávky: {fmt_money(report['eshop']['averageOrderValue'])}",
        f"Problematické objednávky: {report['eshop']['problematicOrders']}",
        f"4PX zásilky: {report['logistics']['shipmentsTotal']} (CZ {report['logistics']['byAccount']['CZ']}, SK {report['logistics']['byAccount']['SK']})",
    ]
    yy = bullet_list(draw, CONTENT_X + 24, box_y + 84, quick_rows, max_width=left_w - 48, bullet_color='gold')
    section_title(draw, CONTENT_X + 24, yy + 6, 'Alerty')
    yy = bullet_list(draw, CONTENT_X + 24, yy + 42, report['quickSummary']['alerts'], max_width=left_w - 48, bullet_color='red')
    section_title(draw, CONTENT_X + 24, yy + 6, 'Dnešní priority')
    bullet_list(draw, CONTENT_X + 24, yy + 42, report['priorities'], max_width=left_w - 48, bullet_color='blue', limit=4)

    rx = CONTENT_X + left_w + col_gap
    round_rect(draw, (rx, box_y, rx + right_w, 1140), COLORS['panel_soft'], radius=28)
    section_title(draw, rx + 24, box_y + 20, 'Top produkty a logistika', 'Víc whitespace, kratší řádky, bez přetékání.')
    yy = box_y + 84
    draw.text((rx + 24, yy), 'Top produkty podle kusů', font=FONT_SMALL, fill=COLORS['gold'])
    rows = [f"{i+1}. {row['code']} · {row['name']} ({fmt_num(row['value'])} ks)" for i, row in enumerate(report['eshop']['topProductsByUnits'][:5])]
    yy = bullet_list(draw, rx + 24, yy + 28, rows, max_width=right_w - 48)
    draw.text((rx + 24, yy + 8), 'Top dopravci / 4PX produkty', font=FONT_SMALL, fill=COLORS['gold'])
    carrier_rows = [f"{row['name']}: {row['count']} zásilek" for row in report['logistics']['carrierCounts'][:5]]
    yy = bullet_list(draw, rx + 24, yy + 36, carrier_rows, max_width=right_w - 48, bullet_color='blue')
    draw.text((rx + 24, yy + 8), 'Nízký sklad po včerejším prodeji', font=FONT_SMALL, fill=COLORS['gold'])
    low_rows = [f"{row['code']} · {row['title']} ({fmt_num(row['stock'])} ks)" for row in report['stock']['lowStockSoldYesterday'][:4]] or ['aktuálně bez kritického shortlistu']
    bullet_list(draw, rx + 24, yy + 36, low_rows, max_width=right_w - 48, bullet_color='green')

    out = OUT / 'dashboard-portal-preview-clean-light.png'
    img.save(out)
    return out


def render_eshop():
    summary = json.loads((DATA / 'portal_summary.json').read_text())
    report = json.loads((DATA / 'morning_report_previous_day.json').read_text())
    img, draw = image_base(active_index=3)
    hero(draw, 'WPJ dashboard, clean luxury směr', 'Světlý reporting pro rychlé vedení. Čistší bloky, jemnější brand, žádné přetékání textu.', [
        ('WPJ připojeno', 'green'),
        (summary['report']['date'], 'gold'),
        ('ready for sign-off', 'blue'),
    ])

    top_y = 226
    gap = 18
    card_w = (CONTENT_W - gap * 3) // 4
    cards = [
        ('Objednávky', fmt_num(report['eshop']['orders']), 'včerejší den', 'green'),
        ('Obrat s DPH', fmt_money(report['eshop']['revenueWithVat']), 'WPJ GraphQL', 'gold'),
        ('AOV', fmt_money(report['eshop']['averageOrderValue']), 'průměrná objednávka', 'blue'),
        ('Problematické', fmt_num(report['eshop']['problematicOrders']), 'chyba / storno', 'red'),
    ]
    for idx, card in enumerate(cards):
        kpi(draw, CONTENT_X + idx * (card_w + gap), top_y, card_w, 156, *card)

    box_y = 410
    col_gap = 18
    col_w = (CONTENT_W - col_gap) // 2
    box_h = 320

    round_rect(draw, (CONTENT_X, box_y, CONTENT_X + col_w, box_y + box_h), COLORS['panel_soft'], radius=28)
    section_title(draw, CONTENT_X + 24, box_y + 20, 'Platební metody')
    pay_rows = [f"{row['name']}: {row['count']}" for row in report['eshop']['paymentMethods'][:7]]
    bullet_list(draw, CONTENT_X + 24, box_y + 70, pay_rows, max_width=col_w - 48, bullet_color='gold')

    rx = CONTENT_X + col_w + col_gap
    round_rect(draw, (rx, box_y, rx + col_w, box_y + box_h), COLORS['panel_soft'], radius=28)
    section_title(draw, rx + 24, box_y + 20, 'Dopravní metody')
    del_rows = [f"{row['name']}: {row['count']}" for row in report['eshop']['deliveryMethods'][:7]]
    bullet_list(draw, rx + 24, box_y + 70, del_rows, max_width=col_w - 48, bullet_color='blue')

    lower_y = 752
    round_rect(draw, (CONTENT_X, lower_y, CONTENT_X + col_w, 1140), COLORS['panel_soft'], radius=28)
    section_title(draw, CONTENT_X + 24, lower_y + 20, 'Top produkty podle kusů')
    unit_rows = [f"{i+1}. {row['code']} · {row['name']} ({fmt_num(row['value'])} ks)" for i, row in enumerate(report['eshop']['topProductsByUnits'][:5])]
    bullet_list(draw, CONTENT_X + 24, lower_y + 70, unit_rows, max_width=col_w - 48, bullet_color='green')

    round_rect(draw, (rx, lower_y, rx + col_w, 1140), COLORS['panel_soft'], radius=28)
    section_title(draw, rx + 24, lower_y + 20, 'Top produkty podle obratu')
    rev_rows = [f"{i+1}. {row['code']} · {row['name']} ({row['formatted']})" for i, row in enumerate(report['eshop']['topProductsByRevenue'][:5])]
    bullet_list(draw, rx + 24, lower_y + 70, rev_rows, max_width=col_w - 48, bullet_color='gold')

    out = OUT / 'dashboard-eshop-preview-clean-light.png'
    img.save(out)
    return out


if __name__ == '__main__':
    print(render_portal())
    print(render_eshop())
