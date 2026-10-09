"""Автономный HTML-отчёт диапазонов с графиками по всей истории.

Логика: встраивает SVG Matplotlib, CSS, JavaScript, исходные результаты JSON,
полные и годовые показатели, лучшие пары диапазонов и журнал в один HTML.
Интернет и соседние файлы для просмотра не нужны. MIX и RTS оформляются
отдельно; пункты разных инструментов не суммируются. Непрерывные графики
сохраняют все даты и не обнуляются на границах годов. Дополнительный столбчатый
график показывает PnL каждого отдельного года: всего, покупки и продажи после
комиссии, включая нулевые годы. Файл атомарно перезаписывается.

Модуль вызывается корневым скриптом; отдельных параметров CLI нет.
Примеры из папки проекта:
    python research_real_regime_ranges.py
    python research_real_regime_ranges.py --output outputs/regime_ranges/report.html
    python research_real_regime_ranges.py --only MIX --commission-bps 5
"""

from __future__ import annotations

import html
import io
import json
import math
import os
import tempfile
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / "outputs/regime_ranges/.matplotlib"))

import matplotlib.dates as mdates
from matplotlib.figure import Figure
import numpy as np
import pandas as pd

DIRECTIONS = {"combined": "Всего", "long": "Покупки", "short": "Продажи"}
COLORS = {"combined": "#4169d9", "long": "#07856e", "short": "#d87634"}
METRICS = (
    ("trades", "Количество сделок"), ("gross_profit", "P&L до комиссии, п."),
    ("commission", "Комиссия, п."), ("net_profit", "P&L после комиссии, п."),
    ("win_rate", "Прибыльных сделок"), ("profit_factor", "Profit factor"),
    ("max_drawdown", "Макс. просадка, п."), ("recovery_factor", "Фактор восстановления"),
    ("mean_profit", "Средняя сделка, п."), ("median_profit", "Медианная сделка, п."),
    ("best_trade", "Лучшая сделка, п."), ("worst_trade", "Худшая сделка, п."),
    ("average_win", "Средняя прибыльная, п."), ("average_loss", "Средняя убыточная, п."),
    ("wins", "Прибыльных сделок, шт."), ("losses", "Убыточных сделок, шт."),
    ("breakeven", "Нулевых после комиссии, шт."), ("missing_facts", "Сигналов без Real"),
)
INTEGER_METRICS = {"trades", "wins", "losses", "breakeven", "missing_facts", "signals", "days", "conflicts"}


def json_safe(value):
    """Возвращает JSON-совместимую копию value: пропуски → None, бесконечность → строка.

    Обрабатывает вложенные словари/списки и скаляры NumPy; исходное значение
    не изменяет. Символ ∞ сохраняет смысл profit factor без убыточных сделок.
    """
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "∞" if value > 0 else "−∞"
    return value


def format_value(value, key: str = "") -> str:
    """Возвращает русское отображение value; key задаёт проценты или целое число."""
    if value is None or isinstance(value, float) and math.isnan(value):
        return "—"
    if value in ("∞", "−∞"):
        return value
    if isinstance(value, float) and math.isinf(value):
        return "∞" if value > 0 else "−∞"
    if key == "win_rate":
        return f"{value * 100:.1f}%".replace(".", ",")
    if key in INTEGER_METRICS:
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def range_label(interval: dict | None) -> str:
    """Возвращает русскую подпись словаря interval или отключённого направления."""
    if interval is None:
        return "Отключено"
    closing = "]" if interval["upper"] == 100 else ")"
    return f"[{int(interval['lower'])}%; {int(interval['upper'])}%{closing}"


def svg_chart(instrument: dict, kind: str) -> str:
    """Возвращает встроенный SVG непрерывного P&L instrument для kind.

    kind=combined показывает совокупность и обе стороны; long/short — отдельную
    сторону. Все исходные дни сохраняются. Перед первой сделкой добавляется
    начальный ноль. Для каждой стороны используется собственная шкала Y.
    """
    daily = instrument["daily"]
    days = [pd.Timestamp(row["date"]) for row in daily]
    start = days[0] - timedelta(days=1)
    dates = [start] + days
    figure = Figure(figsize=(12.4, 3.8), facecolor="white", layout="constrained")
    axis = figure.subplots()
    names = ("combined", "long", "short") if kind == "combined" else (kind,)
    columns = {"combined": "cumulative_net", "long": "cumulative_long", "short": "cumulative_short"}
    for name in names:
        values = [0] + [row[columns[name]] for row in daily]
        axis.plot(dates, values, color=COLORS[name], linewidth=1.5 if name == "combined" else 1.15,
                  label=DIRECTIONS[name], zorder=3)
    axis.axhline(0, color="#96a1b1", linewidth=.8)
    axis.grid(axis="y", color="#e5eaf2", linewidth=.7)
    axis.set_axisbelow(True)
    axis.set_xlim(start, max(days[-1], start + timedelta(days=1)))
    axis.set_ylabel("P&L после комиссии, пункты", fontsize=10, color="#526176")
    axis.tick_params(colors="#526176", labelsize=9)
    locator = mdates.AutoDateLocator(minticks=6, maxticks=11)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(
        locator, formats=["%Y", "%m.%Y", "%d.%m", "%H:%M", "%H:%M", "%S"],
        zero_formats=["", "%Y", "%m.%Y", "%d.%m", "%H:%M", "%H:%M"],
        offset_formats=["", "%Y", "%m.%Y", "%d.%m.%Y", "%d.%m.%Y", "%d.%m.%Y"]))
    for edge in ("top", "right"):
        axis.spines[edge].set_visible(False)
    for edge in ("left", "bottom"):
        axis.spines[edge].set_color("#dbe2ec")
    if kind == "combined":
        axis.legend(loc="upper left", ncols=3, frameon=False, fontsize=9)
    buffer = io.StringIO()
    figure.savefig(buffer, format="svg", metadata={"Date": None})
    figure.clear()
    svg = buffer.getvalue()
    svg = svg[svg.index("<svg"):]
    svg = svg.replace("<svg ", '<svg class="pnl-chart" role="img" ', 1)
    title = html.escape(f"{instrument['series']}: {DIRECTIONS[kind]}, P&L по всей истории")
    return svg.replace(">", f"><title>{title}</title>", 1)


def yearly_svg_chart(instrument: dict) -> str:
    """Возвращает автономный SVG столбчатого годового PnL отдельного instrument.

    instrument содержит series и yearly с годом, направлением и net_profit.
    Для каждого года рисует три соседних столбца: совокупность, покупки,
    продажи. Высота — PnL только данного года после комиссии в пунктах,
    без накопления между годами. Нулевые годы сохраняются, убытки ниже нуля.
    Встроенные подсказки столбцов показывают год, направление и точную сумму.
    """
    years = sorted({row["year"] for row in instrument["yearly"]})
    totals = {(row["year"], row["direction"]): row["net_profit"] for row in instrument["yearly"]}
    positions = np.arange(len(years))
    figure = Figure(figsize=(12.4, 4.2), facecolor="white", layout="constrained")
    axis = figure.subplots()
    tooltips = {}
    for index, name in enumerate(DIRECTIONS):
        values = [totals.get((year, name), 0) for year in years]
        bars = axis.bar(positions + (index - 1) * .26, values, width=.24,
                        color=COLORS[name], label=DIRECTIONS[name], zorder=3)
        for year, value, bar in zip(years, values, bars):
            identifier = f"annual-{instrument['series']}-{year}-{name}"
            bar.set_gid(identifier)
            tooltips[identifier] = html.escape(f"{year} · {DIRECTIONS[name]}: {format_value(value)} п.")
    axis.axhline(0, color="#78869b", linewidth=1, zorder=4)
    axis.grid(axis="y", color="#e5eaf2", linewidth=.7)
    axis.set_axisbelow(True)
    axis.set_xticks(positions, [str(year) for year in years])
    axis.set_xlabel("Год", fontsize=10, color="#526176")
    axis.set_ylabel("Годовой PnL после комиссии, пункты", fontsize=10, color="#526176")
    axis.tick_params(colors="#526176", labelsize=9)
    for edge in ("top", "right"):
        axis.spines[edge].set_visible(False)
    for edge in ("left", "bottom"):
        axis.spines[edge].set_color("#dbe2ec")
    axis.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncols=3, frameon=False, fontsize=9)
    buffer = io.StringIO()
    figure.savefig(buffer, format="svg", metadata={"Date": None})
    figure.clear()
    svg = buffer.getvalue()
    svg = svg[svg.index("<svg"):]
    svg = svg.replace("<svg ", '<svg class="pnl-chart yearly-pnl-chart" role="img" ', 1)
    title = html.escape(f"{instrument['series']}: годовой PnL — всего, покупки и продажи")
    svg = svg.replace(">", f"><title>{title}</title>", 1)
    for identifier, tooltip in tooltips.items():
        escaped_id = html.escape(identifier, quote=True)
        tag = f'<g id="{escaped_id}">'
        svg = svg.replace(tag, f"{tag}<title>{tooltip}</title>")
    return svg


def table_html(headers: list[str], rows: list[list[str]], row_classes: list[str] | None = None) -> str:
    """Возвращает прокручиваемую таблицу headers/rows; row_classes задаёт классы строк.

    Ячейки rows уже экранированы или содержат контролируемую HTML-разметку;
    заголовки экранируются здесь. Пустая таблица получает поясняющую строку.
    """
    head = "".join(f"<th>{html.escape(name)}</th>" for name in headers)
    body = []
    for index, row in enumerate(rows):
        css = row_classes[index] if row_classes else ""
        body.append(f'<tr class="{css}">' + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    if not rows:
        body.append(f'<tr><td colspan="{len(headers)}">Нет сделок</td></tr>')
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def metrics_table(instrument: dict) -> str:
    """Возвращает сравнение полных показателей instrument: всего, покупки, продажи."""
    rows = [[label] + [format_value(instrument["metrics"][name][key], key) for name in DIRECTIONS]
            for key, label in METRICS]
    return table_html(["Показатель", *DIRECTIONS.values()], rows)


def yearly_table(instrument: dict) -> str:
    """Возвращает основные и раскрываемые полные годовые показатели instrument.

    Основная таблица сравнивает все годы и три направления; дополнительный
    блок показывает для каждого года тот же полный набор, что за всю историю.
    """
    keys = ("trades", "gross_profit", "commission", "net_profit", "win_rate", "profit_factor", "max_drawdown", "mean_profit")
    labels = [dict(METRICS)[key] for key in keys]
    rows, classes = [], []
    for row in instrument["yearly"]:
        rows.append([str(row["year"]), DIRECTIONS[row["direction"]]] + [format_value(row[key], key) for key in keys])
        classes.append("year-total" if row["direction"] == "combined" else "")
    primary = table_html(["Год", "Направление", *labels], rows, classes)
    extended = ['<details><summary>Все показатели по отдельным годам</summary>']
    for year in sorted({row["year"] for row in instrument["yearly"]}):
        metrics = {row["direction"]: row for row in instrument["yearly"] if row["year"] == year}
        extended.append(f"<h4>{year}</h4>" + metrics_table({"metrics": metrics}))
    extended.append("</details>")
    return primary + "".join(extended)


def candidates_table(instrument: dict) -> str:
    """Возвращает таблицу лучших проверенных пар диапазонов instrument."""
    rows = []
    for position, row in enumerate(instrument["top_candidates"], 1):
        buy = None if row["bull_lower"] is None else {"lower": row["bull_lower"], "upper": row["bull_upper"]}
        sell = None if row["bear_lower"] is None else {"lower": row["bear_lower"], "upper": row["bear_upper"]}
        rows.append([str(position), range_label(buy), range_label(sell), format_value(row["net_profit"]),
                     format_value(row["trades"], "trades"), format_value(row["conflicts"], "conflicts")])
    return table_html(["Место", "Покупка: Бычий", "Продажа: Медвежий", "P&L после комиссии, п.", "Сделок", "Конфликтов"], rows)


def journal_table(instrument: dict) -> str:
    """Возвращает журнал instrument с исполнениями, конфликтами и сигналами без Real.

    Дни без сигналов представлены в графиках и встроенном JSON. Для сигналов
    без факта и конфликтов финансовые ячейки пусты: это не нулевые сделки.
    """
    rows = []
    for row in instrument["daily"]:
        if not row["side"] and not row["conflict"]:
            continue
        name = "Конфликт" if row["conflict"] else ("Покупка" if row["side"] == 1 else "Продажа")
        if row["missing_fact"]:
            name += " · нет Real"
        probability = [format_value(row[key], "win_rate") for key in ("p_bull", "p_bear")]
        money = [format_value(row[key]) if row["executed"] else "—"
                 for key in ("open", "close", "gross_profit", "commission", "net_profit")]
        rows.append([html.escape(row["date"]), html.escape(str(row.get("secid", "—"))), name,
                     *probability, *money, format_value(row["cumulative_net"])])
    return table_html(["Дата", "SECID", "Направление / статус", "Бычий", "Медвежий", "OPEN", "CLOSE",
                       "P&L до комиссии", "Комиссия", "P&L после комиссии", "Накопленный P&L"], rows)


def instrument_section(instrument: dict) -> str:
    """Возвращает раздел instrument с непрерывными кривыми и годовым столбчатым PnL."""
    series = html.escape(instrument["series"])
    daily = instrument["daily"]
    total = instrument["metrics"]["combined"]
    cards = []
    for name, value in (("P&L после комиссии, п.", total["net_profit"]),
                        ("Макс. просадка, п.", total["max_drawdown"]),
                        ("Сделок", total["trades"]), ("Profit factor", total["profit_factor"])):
        key = "trades" if name == "Сделок" else ""
        cards.append(f'<div class="stat-card"><span>{name}</span><strong>{format_value(value, key)}</strong></div>')
    graphs = []
    for kind in DIRECTIONS:
        title = "Совокупный P&L и обе стороны" if kind == "combined" else f"P&L: {DIRECTIONS[kind].lower()}"
        graphs.append(f'<figure><div class="chart-heading"><h3>{title}</h3>'
                      f'<button type="button" class="expand-chart">Развернуть</button></div>'
                      f'{svg_chart(instrument, kind)}'
                      '<figcaption>Вся история · после комиссии · без обнуления между годами</figcaption></figure>')
    source = html.escape(instrument["input"])
    database = html.escape(instrument["database"])
    return f'''
<section id="{series}" class="instrument">
  <div class="section-heading"><div><span class="eyebrow">Отдельный инструмент</span><h2>{series}</h2></div>
    <p>{daily[0]['date']} — {daily[-1]['date']}<br>{len(daily):,} дат в непрерывной симуляции</p></div>
  <div class="ranges">
    <div class="range-card buy"><span>Покупка · вероятность «Бычий»</span><strong>{range_label(instrument['bull_range'])}</strong></div>
    <div class="range-card sell"><span>Продажа · вероятность «Медвежий»</span><strong>{range_label(instrument['bear_range'])}</strong></div>
  </div>
  <div class="stats">{''.join(cards)}</div>
  <div class="charts">{''.join(graphs)}</div>
  <h3>Показатели за всю историю</h3>{metrics_table(instrument)}
  <p class="caption">Конфликтующих дней: {total['conflicts']}; сигналов без Real: {total['missing_facts']}.
    Profit factor и доля прибыльных сделок рассчитаны после комиссии.
    Фактор восстановления = P&L / максимальная просадка.</p>
  <h3>Показатели по годам</h3>
  <figure><div class="chart-heading"><h3>Годовой PnL</h3>
    <button type="button" class="expand-chart">Развернуть</button></div>
    {yearly_svg_chart(instrument)}
    <figcaption>Результат каждого отдельного года · после комиссии · пункты.
      Наведите курсор на столбец для точного значения.</figcaption></figure>
  {yearly_table(instrument)}
  <p class="caption">Годовая просадка начинается с нуля на границе года. Полная просадка и кривые выше учитывают переходы между годами.
    Годовые P&L и числа сделок складываются в результат за всю историю.</p>
  <details><summary>Лучшие 12 пар из {instrument['pairs_evaluated']:,} проверенных</summary>
    <p class="caption">Для каждого направления проверено 210 диапазонов и отключение торговли.
      При равном P&L с точностью 0,00000001 пункта: меньше сделок, меньшая общая ширина, затем меньшие границы.</p>
    {candidates_table(instrument)}</details>
  <details><summary>Полный журнал сделок и пропущенных сигналов</summary>
    <div class="journal-toolbar"><p class="caption">Все годы. P&L и цены — в пунктах.</p>
      <button type="button" class="download-journal" data-series="{series}">Скачать сделки CSV</button></div>
    {journal_table(instrument)}</details>
  <details><summary>Источники и воспроизводимость</summary><p>Книга: <code>{source}</code></p>
    <p>Лист: <code>{html.escape(instrument['sheet'])}</code></p><p>SQLite: <code>{database}</code></p>
    <p>SHA-256 XLSX: <code>{instrument['input_sha256']}</code></p>
    <p>Котировки сопоставлены по Дата/SECID/LSTTRADE; проверено Real = CLOSE − OPEN.
      Вероятности взяты из выбранного листа, включая сохранённое округление.
      Дополнительные фильтры размера истории и наблюдаемого режима не применяются.</p></details>
</section>'''


CSS = """
:root{color-scheme:light;--ink:#1b2d45;--muted:#61718a;--blue:#4169d9;--line:#dde5ef}
*{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:var(--ink);font:15px/1.6 'Segoe UI',Arial,sans-serif}
header{background:#14263f;color:white;padding:42px max(24px,calc((100vw - 1320px)/2)) 30px}
header p{max-width:950px;color:#c4d1e3;margin:8px 0}h1{font-size:32px;line-height:1.2;margin:8px 0 16px;font-weight:650}
nav{display:flex;gap:12px;margin-top:22px}nav a{color:white;border:1px solid #51647f;border-radius:8px;padding:8px 20px;text-decoration:none}
main{max-width:1360px;margin:0 auto;padding:24px 20px 60px}.method{background:#e9effb;border-left:4px solid var(--blue);padding:18px 24px;border-radius:8px}
.method p{margin:6px 0}.instrument{margin-top:32px;background:white;border:1px solid var(--line);border-radius:14px;padding:28px}
.section-heading{display:flex;justify-content:space-between;gap:20px;align-items:center}.section-heading p{text-align:right;color:var(--muted);font-size:14px}
.eyebrow{text-transform:uppercase;letter-spacing:1.6px;font-size:11px;font-weight:650;color:var(--muted)}h2{font-size:30px;margin:2px 0}h3{margin:28px 0 14px;font-size:20px;font-weight:600}
.ranges{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0}.range-card{border-radius:10px;padding:16px 22px}
.range-card span,.stat-card span{display:block;font-size:13px;color:var(--muted)}.range-card strong{display:block;font-size:27px;font-weight:650;margin-top:4px}
.buy{background:#edf8f4;border-left:4px solid #07856e}.sell{background:#fff5ec;border-left:4px solid #d87634}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0}.stat-card{padding:16px 18px;border:1px solid var(--line);border-radius:10px;background:#fafcfe}
.stat-card strong{display:block;font-size:25px;line-height:1.4;margin-top:6px;font-variant-numeric:tabular-nums}
figure{margin:24px 0;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:white}.chart-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}
.chart-heading h3{font-size:17px;margin:4px 8px 12px}.pnl-chart{display:block;width:100%;height:auto}figcaption{font-size:12px;color:var(--muted);text-align:center;margin:4px 0}
.expanded{position:fixed;inset:16px;z-index:100;background:white;overflow:auto;box-shadow:0 0 0 30px #14263fcc;margin:0}
button{border:1px solid var(--line);background:#f7f9fd;color:var(--ink);border-radius:7px;padding:7px 12px;cursor:pointer;font:inherit;font-size:12px}
button:hover{background:#e9effb}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px;max-height:700px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:10px 13px;border-bottom:1px solid #e8edf4;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{position:sticky;top:0;background:#edf2f9;color:#415675;z-index:1;font-weight:600}
tbody tr:nth-child(even){background:#f9fbfe}tbody tr:hover{background:#edf3fd}.year-total{font-weight:600;border-top:2px solid #d1dceb;background:#eff4fb!important}
.caption{font-size:13px;color:var(--muted);margin:12px 0 20px}details{margin:20px 0;border:1px solid var(--line);border-radius:9px;padding:14px 18px}
summary{cursor:pointer;font-weight:600}details[open] summary{margin-bottom:16px}.journal-toolbar{display:flex;justify-content:space-between;align-items:center;gap:16px}
code{font-size:12px;overflow-wrap:anywhere;white-space:normal}footer{padding:12px 4px;color:var(--muted);font-size:13px}
@media(max-width:800px){header{padding:26px 20px}h1{font-size:26px}main{padding:16px 10px}.instrument{padding:18px 12px}.stats{grid-template-columns:1fr 1fr}.ranges{grid-template-columns:1fr}.section-heading p{font-size:12px}.stat-card strong{font-size:21px}figure{padding:8px 0}.chart-heading{padding:0 8px}.expanded{inset:6px}}
@media print{body{background:white}header{background:white;color:var(--ink);padding:20px}header p{color:var(--muted)}nav,button{display:none}main{padding:0}.instrument{border:0;padding:14px;break-before:page}figure{break-inside:avoid}.table-wrap{max-height:none;overflow:visible}th{position:static}.method{background:white}details{break-inside:avoid}}
"""

JAVASCRIPT = """
/** Переключает увеличенный просмотр графика по событию event; результата нет. */
function toggleChart(event) {
  const figure = event.currentTarget.closest('figure');
  figure.classList.toggle('expanded');
  event.currentTarget.textContent = figure.classList.contains('expanded') ? 'Свернуть' : 'Развернуть';
}
/** Закрывает увеличенные графики клавишей Escape из event; результата нет. */
function closeCharts(event) {
  if (event.key !== 'Escape') return;
  for (const figure of document.querySelectorAll('figure.expanded')) {
    figure.classList.remove('expanded');
    figure.querySelector('button').textContent = 'Развернуть';
  }
}
/** Возвращает безопасную CSV-ячейку value с удвоенными кавычками. */
function csvCell(value) {
  return '"' + String(value ?? '').replaceAll('"', '""') + '"';
}
/** Сохраняет все исполненные сделки выбранного инструмента по event в CSV с BOM. */
function downloadJournal(event) {
  const payload = JSON.parse(document.getElementById('report-data').textContent);
  let instrument;
  for (const item of payload.instruments) if (item.series === event.currentTarget.dataset.series) instrument = item;
  const keys = ['date','secid','side','p_bull','p_bear','open','close','real','gross_profit','commission','net_profit','cumulative_net','cumulative_long','cumulative_short'];
  const rows = [['Дата','SECID','Направление (1 покупка, -1 продажа)','Бычий (доля)','Медвежий (доля)','OPEN','CLOSE','Real','P&L до комиссии','Комиссия','P&L после комиссии','Совокупный P&L','P&L покупок','P&L продаж']];
  for (const day of instrument.daily) {
    if (!day.executed) continue;
    const row = [];
    for (const key of keys) row.push(day[key]);
    rows.push(row);
  }
  const lines = [];
  for (const row of rows) {
    const cells = [];
    for (const value of row) cells.push(csvCell(value));
    lines.push(cells.join(';'));
  }
  const url = URL.createObjectURL(new Blob(['\\ufeff' + lines.join('\\r\\n')], {type:'text/csv;charset=utf-8'}));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = instrument.series + '_regime_trades.csv';
  anchor.click();
  URL.revokeObjectURL(url);
}
for (const button of document.querySelectorAll('.expand-chart')) button.addEventListener('click', toggleChart);
for (const button of document.querySelectorAll('.download-journal')) button.addEventListener('click', downloadJournal);
document.addEventListener('keydown', closeCharts);
"""


def write_html_report(payload: dict, path: Path) -> None:
    """Сохраняет payload в один автономный HTML path; существующий файл заменяется.

    Все SVG, таблицы, стили, код и JSON встроены. Входные словари не изменяются.
    При ошибке до замены прежний отчёт сохраняется; временный файл удаляется.
    """
    payload = json_safe(payload)
    links = "".join(f'<a href="#{html.escape(item["series"])}">{html.escape(item["series"])}</a>'
                    for item in payload["instruments"])
    sections = "".join(instrument_section(item) for item in payload["instruments"])
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    serialized = serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    document = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Диапазоны вероятностей · MIX и RTS · симуляция</title><style>{CSS}</style></head><body>
<header><span class="eyebrow" style="color:#b4c5dd">pj24_funds_2 · исследование всей истории</span>
<h1>Диапазоны вероятностей и симуляционная торговля</h1>
<p>Отдельные диапазоны для покупок и продаж каждого инструмента. Непрерывный P&L по всему календарю и показатели по годам.</p>
<nav>{links}</nav></header><main>
<div class="method"><p><strong>Правила расчёта.</strong> Шаг границ — 5 процентных пунктов. Подбирается пара диапазонов,
максимизирующая совокупный P&L после комиссии на всей истории каждого инструмента отдельно.</p>
<p>Один контракт; покупка даёт Real, продажа — −Real. Вход OPEN → выход CLOSE того же дня.
Комиссия на полный круг: {format_value(payload['commission_bps'])} б.п. от OPEN
(OPEN × {payload['commission_bps']:g} / 10000 пунктов). Прочие расходы и проскальзывание не моделируются.</p>
<p>Нижняя граница включена; верхняя исключена, кроме 100%. Два сигнала в один день отменяют сделку.
Сигналы без Real не исполняются; Real = 0 исполняется с комиссией.</p>
<p>Доход и просадка выражены в пунктах, без реинвестирования и модели капитала. Пункты MIX и RTS не суммируются.</p>
<p><strong>Интерпретация.</strong> Подбор и симуляция используют одни и те же данные: это лучший результат на данной истории,
без проверки на отдельном последующем периоде. Исторический максимум не гарантирует будущую прибыль.</p></div>
{sections}<footer>Автономный отчёт: графики, данные и журнал встроены в этот файл. Для просмотра интернет не требуется.
Даты без сигналов сохранены в кривых и во встроенных данных.</footer></main>
<script id="report-data" type="application/json">{serialized}</script><script>{JAVASCRIPT}</script></body></html>'''
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".regime-report-", suffix=".html", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(document)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
