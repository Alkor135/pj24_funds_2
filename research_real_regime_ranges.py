r"""Подбор диапазонов MIX/RTS с шагом 5% и автономная HTML-симуляция.

Логика: для каждого инструмента отдельно читает вероятности Бычий/Медвежий
из XLSX и проверяет Real по OPEN/CLOSE SQLite. Совместно перебирает 44 521
пару диапазонов покупки/продажи на всей истории, включая отключение стороны,
и выбирает максимум после комиссии. Симулирует один контракт OPEN → CLOSE
того же дня. При двух сигналах день пропускает. Границы: [нижняя; верхняя),
но 100% включено. По умолчанию комиссия — 3 б.п. на полный круг от OPEN.
Вероятности — сохранённые значения листа Прогноз; дополнительных фильтров нет.

Один автономный HTML содержит отдельные разделы MIX и RTS, непрерывные кривые
совокупного P&L и обеих сторон за всю историю, столбчатый PnL каждого года
по совокупности, покупкам и продажам после комиссии, полные и годовые показатели,
лучшие пары, журнал и встроенные данные. Кривые не обнуляются между годами.
Результаты в пунктах без модели капитала; пункты инструментов не складываются.
Это исторический оптимум на использованных данных, без отдельной проверки.
XLSX и SQLite не изменяются; совпадение пути отчёта с фактической БД запрещено.
Отчёт с постоянным именем перезаписывается;
логов нет. Требуются зависимости из requirements.txt.

Примеры из папки проекта:
    .venv\Scripts\python.exe research_real_regime_ranges.py
    python research_real_regime_ranges.py --only RTS --commission-bps 3
    python research_real_regime_ranges.py --mix outputs/MIX_probabilities.xlsx --rts outputs/RTS_probabilities.xlsx
    python research_real_regime_ranges.py --only MIX --mix-db C:/data_quote/day2/MIX_day2.db
    python research_real_regime_ranges.py --output outputs/regime_ranges/report.html
    python research_real_regime_ranges.py --help

Стандартные пути отсчитываются от папки скрипта; явно заданные относительные
пути — от рабочей папки запуска. Успех возвращает 0, ошибка — 2.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from research_real_regime_trades import load_regime_trade_data
from source.regime_ranges import optimize_ranges, simulate_ranges, trade_metrics, yearly_metrics
from source.regime_ranges_report import range_label, write_html_report

PROJECT = Path(__file__).resolve().parent


def describe_range(interval) -> dict | None:
    """Возвращает границы interval для HTML/JSON; None означает отключённую сторону."""
    return {"lower": interval.lower, "upper": interval.upper} if interval else None


def analyze_instrument(series: str, path: Path, sheet: str, database: Path | None,
                       commission_bps: float) -> dict:
    """Возвращает подбор, всю симуляцию и годовые результаты отдельного series.

    path/sheet выбирают книгу и лист; database переопределяет SQLite.
    commission_bps задаёт расходы на круг. Проверяет серию в метаданных
    и совпадение максимума перебора с итогом дневной симуляции. Входы не меняет.
    """
    path = Path(path).resolve()
    with pd.ExcelFile(path, engine="openpyxl") as book:
        metadata = dict(pd.read_excel(book, sheet_name="Параметры", usecols=[0, 1])
                        .itertuples(index=False, name=None))
    if metadata.get("Серия") != series:
        raise ValueError(f"Для {series} требуется книга этой серии; в {path.name} указано {metadata.get('Серия')}.")
    data, used_database = load_regime_trade_data(path, sheet, database)
    if data.empty:
        raise ValueError(f"В книге {series} нет строк для симуляции.")
    selection = optimize_ranges(data, commission_bps)
    daily = simulate_ranges(data, selection["bull_range"], selection["bear_range"], commission_bps)
    metrics = {name: trade_metrics(daily, side) for name, side in (("combined", 0), ("long", 1), ("short", -1))}
    if not math.isclose(metrics["combined"]["net_profit"], selection["net_profit"], rel_tol=1e-12, abs_tol=1e-7):
        raise ValueError(f"Итог симуляции {series} не совпал с результатом полного перебора.")
    export = daily.rename(columns={"Дата": "date", "SECID": "secid", "LSTTRADE": "expiry",
                                    "Бычий": "p_bull", "Медвежий": "p_bear", "Real": "real",
                                    "OPEN": "open", "CLOSE": "close"}).copy()
    export["date"] = export["date"].dt.strftime("%Y-%m-%d")
    if "expiry" in export:
        export["expiry"] = pd.to_datetime(export["expiry"]).dt.strftime("%Y-%m-%d")
    return {"series": series, "input": str(path), "sheet": sheet, "database": str(used_database),
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bull_range": describe_range(selection["bull_range"]),
            "bear_range": describe_range(selection["bear_range"]),
            "pairs_evaluated": selection["pairs_evaluated"], "metrics": metrics,
            "yearly": yearly_metrics(daily).to_dict(orient="records"),
            "daily": export.to_dict(orient="records"),
            "top_candidates": selection["candidates"].head(12).to_dict(orient="records")}


def run_cli(argv: list[str] | None = None) -> int:
    """Обрабатывает argv, создаёт отчёт; возвращает 0 при успехе, 2 при ошибке.

    argv=None использует командную строку. --mix/--rts выбирают книги,
    --mix-db/--rts-db задают копии БД, --only ограничивает один инструмент,
    --sheet выбирает лист, --commission-bps — расходы, --output — HTML.
    Запрещает заменять фактическую БД, в том числе взятую из метаданных книги.
    """
    parser = argparse.ArgumentParser(description="Диапазоны с шагом 5% и симуляция MIX/RTS за всю историю.", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать параметры и завершить работу")
    parser._optionals.title = "Параметры"
    parser.add_argument("--mix", type=Path, default=PROJECT / "outputs/MIX_probabilities.xlsx", help="Книга MIX")
    parser.add_argument("--rts", type=Path, default=PROJECT / "outputs/RTS_probabilities.xlsx", help="Книга RTS")
    parser.add_argument("--mix-db", type=Path, help="Копия SQLite MIX; по умолчанию Источник из книги")
    parser.add_argument("--rts-db", type=Path, help="Копия SQLite RTS; по умолчанию Источник из книги")
    parser.add_argument("--only", choices=("MIX", "RTS"), help="Обработать только указанный инструмент; по умолчанию оба")
    parser.add_argument("--sheet", default="Прогноз", help="Лист вероятностей; по умолчанию Прогноз")
    parser.add_argument("--commission-bps", type=float, default=3, help="Комиссия на полный круг от OPEN в б.п.; по умолчанию 3")
    parser.add_argument("--output", type=Path, default=PROJECT / "outputs/regime_ranges/report.html", help="Автономный HTML; существующий файл перезаписывается")
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        if output.suffix.lower() != ".html":
            raise ValueError("Путь --output должен оканчиваться на .html.")
        protected = (args.mix, args.rts, args.mix_db, args.rts_db)
        if any(output == path.resolve() for path in protected if path is not None):
            raise ValueError("Отчёт не должен перезаписывать входные файлы.")
        instruments = []
        for series, path, database in (("MIX", args.mix, args.mix_db), ("RTS", args.rts, args.rts_db)):
            if args.only and series != args.only:
                continue
            print(f"{series}: чтение, перебор диапазонов и симуляция по всей истории...", flush=True)
            result = analyze_instrument(series, path, args.sheet, database, args.commission_bps)
            if output == Path(result["database"]).resolve():
                raise ValueError("Отчёт не должен перезаписывать фактическую исходную SQLite.")
            instruments.append(result)
            total = result["metrics"]["combined"]
            print(f"{series}: покупка {range_label(result['bull_range'])}; продажа {range_label(result['bear_range'])}; "
                  f"P&L после комиссии {total['net_profit']:.2f} п.; сделок {total['trades']}.", flush=True)
        write_html_report({"step_pct": 5, "commission_bps": args.commission_bps,
                           "optimization_period": "full_history", "instruments": instruments}, output)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Ошибка чтения, расчёта или сохранения: {error}", file=sys.stderr)
        return 2
    print(f"Отчёт: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
