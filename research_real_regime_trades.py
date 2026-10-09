r"""Доход покупок и продаж по порогам вероятностей Бычий и Медвежий.

Логика: покупка при Бычий >= bull_threshold (по умолчанию 60%), продажа при
Медвежий >= bear_threshold (70%). Целые и дробные пороги включаются. Одна сделка на один
контракт: вход по OPEN, выход по CLOSE того же дневного бара. Доход покупки
равен Real, продажи — −Real. Комиссия commission_bps (3 б.п. на полный круг)
равна OPEN × commission_bps / 10000 в пунктах. Выводит DataFrame с числом
сделок, доходом без комиссии, комиссией, доходом с комиссией и числом сигналов
без Real для покупок, продаж и в совокупности.
Нулевой Real считается сделкой с комиссией. Пустой Real не заменяется нулём.
Пустая вероятность не даёт сигнал. Одновременные сигналы пропускаются.
Использует сохранённые вероятности выбранного листа, всю историю без
дополнительных фильтров и реинвестирования; результаты выражены в пунктах.

Цена OPEN берётся из SQLite по ключу Дата/SECID/LSTTRADE. Путь и серия —
из листа Параметры XLSX; --db позволяет выбрать другую копию БД. Real
проверяется на равенство CLOSE−OPEN. XLSX и SQLite читаются без изменений.
По умолчанию: outputs/MIX_probabilities.xlsx рядом со скриптом, лист Прогноз.
Явные относительные пути отсчитываются от текущей рабочей папки запуска.
Зависимости: pandas, numpy и openpyxl из requirements.txt.

Примеры из папки проекта:
    .venv\Scripts\python.exe research_real_regime_trades.py
    python research_real_regime_trades.py --input outputs/RTS_probabilities.xlsx
    python research_real_regime_trades.py --bull-threshold 65 --bear-threshold 75
    python research_real_regime_trades.py --commission-bps 3 --db C:/data_quote/day2/MIX_day2.db
    python research_real_regime_trades.py --input C:/data/table.xlsx --sheet Прогноз
    python research_real_regime_trades.py --help
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from source.funds_model import read_database
from source.real_data import as_date, finite_number

PROJECT = Path(__file__).resolve().parent
DEFAULT_BULL_THRESHOLD = 60.0
DEFAULT_BEAR_THRESHOLD = 70.0
DEFAULT_COMMISSION_BPS = 3.0


def analyze_regime_trades(
    data: pd.DataFrame,
    bull_threshold: float = DEFAULT_BULL_THRESHOLD,
    bear_threshold: float = DEFAULT_BEAR_THRESHOLD,
    commission_bps: float = DEFAULT_COMMISSION_BPS,
) -> pd.DataFrame:
    """Возвращает сводный DF покупок, продаж и общего дохода по данным data.

    data содержит Бычий/Медвежий в долях 0–1, Real и OPEN в пунктах.
    bull_threshold/bear_threshold — целые или дробные включённые пороги
    в процентах 0–100; десятичное преобразование сохраняет равенство границе.
    commission_bps — неотрицательная комиссия на круг от цены OPEN в б.п.
    Доход — сумма Real для покупок и −Real для продаж, без реинвестирования.
    Учитывает нулевые Real, отдельно считает сигналы без факта, пропускает
    конфликтующие сигналы. Число конфликтов записывает в attrs['conflicts'].
    Исходный DF не изменяет; неверные данные и параметры вызывают ValueError.
    """
    for name, value in (("Бычий", bull_threshold), ("Медвежий", bear_threshold)):
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f"Порог «{name}» должен быть конечным числом от 0 до 100%.")
    if not math.isfinite(commission_bps) or commission_bps < 0:
        raise ValueError("Комиссия в б.п. должна быть конечной и неотрицательной.")
    required = ("Бычий", "Медвежий", "Real", "OPEN")
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError("Отсутствуют колонки: " + ", ".join(missing))
    numeric = pd.DataFrame(index=data.index)
    for column in required:
        try:
            numeric[column] = pd.to_numeric(data[column], errors="raise")
        except (TypeError, ValueError) as error:
            raise ValueError(f"Колонка «{column}» содержит нечисловые значения.") from error
        known = numeric[column].dropna()
        if not np.isfinite(known.to_numpy(dtype=float)).all():
            raise ValueError(f"Колонка «{column}» содержит бесконечные значения.")
        if column in ("Бычий", "Медвежий") and not known.between(0, 1).all():
            raise ValueError(f"Вероятности «{column}» должны быть долями от 0 до 1.")
    paired = numeric[["Бычий", "Медвежий"]].dropna()
    if (paired.sum(axis=1) > 1 + 1e-10).any():
        raise ValueError("Сумма вероятностей Бычий и Медвежий не может превышать 100%.")
    # Десятичное деление устраняет сдвиг границы на один шаг float для 56.7% и подобных порогов.
    bull_cut = float(Decimal(str(bull_threshold)) / 100)
    bear_cut = float(Decimal(str(bear_threshold)) / 100)
    buy = numeric["Бычий"].ge(bull_cut).fillna(False)
    sell = numeric["Медвежий"].ge(bear_cut).fillna(False)
    conflicts = buy & sell
    buy, sell = buy & ~conflicts, sell & ~conflicts
    executed = (buy | sell) & numeric["Real"].notna()
    if (numeric.loc[executed, "OPEN"].isna() | (numeric.loc[executed, "OPEN"] <= 0)).any():
        raise ValueError("Для каждой сделки требуется положительная цена OPEN.")
    rows = []
    for name, signal, side in (("Бычий: покупка", buy, 1), ("Медвежий: продажа", sell, -1)):
        mask = signal & numeric["Real"].notna()
        gross = float((numeric.loc[mask, "Real"] * side).sum())
        commission = float(numeric.loc[mask, "OPEN"].sum()) * commission_bps / 10000
        rows.append({"Режим": name, "Количество сделок": int(mask.sum()),
                     "Доход без комиссии": gross, "Комиссия": commission,
                     "Доход с комиссией": gross - commission,
                     "Сигналы без Real": int((signal & numeric["Real"].isna()).sum())})
    rows.append({"Режим": "Всего", **{column: sum(row[column] for row in rows)
                                      for column in rows[0] if column != "Режим"}})
    result = pd.DataFrame(rows)
    result.attrs["conflicts"] = int(conflicts.sum())
    return result


def load_regime_trade_data(
    path: Path, sheet: str = "Прогноз", db: Path | None = None,
) -> tuple[pd.DataFrame, Path]:
    """Читает сигналы из листа sheet книги path и OPEN из SQLite; возвращает DF и путь БД.

    db задаёт копию БД; без него используется Источник листа Параметры.
    Серия берётся из Параметры. Вероятности берутся из выбранного листа.
    Цены сопоставляет по дате/SECID/LSTTRADE, проверяет Real=CLOSE−OPEN
    для всех известных фактов и исключает синтетические бары. Пустой Real
    сохраняется без OPEN. Входы не изменяет; несоответствия вызывают ValueError.
    """
    path = Path(path).resolve()
    required = ["Дата", "SECID", "LSTTRADE", "Бычий", "Медвежий", "Real"]
    with pd.ExcelFile(path, engine="openpyxl") as book:
        if "Параметры" not in book.sheet_names:
            raise ValueError("В XLSX требуется лист Параметры с серией и источником БД.")
        data = pd.read_excel(book, sheet_name=sheet, usecols=required).dropna(how="all")
        parameters = pd.read_excel(book, sheet_name="Параметры", usecols=[0, 1])
        metadata = dict(parameters.itertuples(index=False, name=None))
    series = metadata.get("Серия")
    if not isinstance(series, str) or not series:
        raise ValueError("В листе Параметры отсутствует серия.")
    if db is None:
        source = metadata.get("Источник")
        if not isinstance(source, str) or not source:
            raise ValueError("Нет пути Источник в Параметры; укажите --db.")
        db = Path(source)
        if not db.is_absolute():
            db = path.parent / db
    database = Path(db).resolve()
    quotes = {}
    for quote in read_database(database, series):
        key = (quote.day, quote.secid, quote.expiry)
        if key in quotes:
            raise ValueError(f"Повтор ключа котировки в БД: {key}.")
        quotes[key] = quote
    days = [as_date(value, "Дата") for value in data["Дата"]]
    if len(set(days)) != len(days):
        raise ValueError("В XLSX должен быть один основной контракт на дату.")
    openings = []
    for day, secid, expiry, real in data[["Дата", "SECID", "LSTTRADE", "Real"]].itertuples(index=False, name=None):
        if pd.isna(real):
            openings.append(np.nan)
            continue
        key = (as_date(day, "Дата"), secid, as_date(expiry, "LSTTRADE"))
        quote = quotes.get(key)
        if quote is None:
            raise ValueError(f"Не найдена котировка для строки XLSX: {key}.")
        opening = finite_number(quote.open, "OPEN", False)
        close = finite_number(quote.close, "CLOSE", False)
        if opening <= 0 or close <= 0 or quote.filled or quote.volume <= 0:
            raise ValueError(f"Для известного Real требуется реальный бар с положительными ценами: {key}.")
        if not math.isclose(close - opening, finite_number(real, "Real", False), rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(f"Real в XLSX не совпадает с CLOSE−OPEN в БД: {key}.")
        openings.append(opening)
    data["OPEN"] = openings
    return data, database


def run_cli(argv: list[str] | None = None) -> int:
    """Читает параметры argv, печатает сводный DF; возвращает 0 при успехе или 2 при ошибке.

    При argv=None использует командную строку. --input/--sheet выбирают XLSX
    и лист, --db меняет путь SQLite. --bull-threshold/--bear-threshold задают
    пороги в процентах, --commission-bps — комиссию на круг. Доходы и комиссии
    печатаются в пунктах с двумя знаками после точки.
    """
    parser = argparse.ArgumentParser(description="Доход сделок по порогам Бычий/Медвежий.", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать параметры и завершить работу")
    parser._optionals.title = "Параметры"
    parser.add_argument("--input", type=Path, default=PROJECT / "outputs/MIX_probabilities.xlsx",
                        help="Исходный XLSX; по умолчанию MIX в outputs проекта")
    parser.add_argument("--sheet", default="Прогноз", help="Лист XLSX; по умолчанию Прогноз")
    parser.add_argument("--db", type=Path, help="Путь SQLite; по умолчанию Источник из Параметры XLSX")
    parser.add_argument("--bull-threshold", type=float, default=DEFAULT_BULL_THRESHOLD,
                        help="Покупка при Бычий не ниже этого процента; по умолчанию 60")
    parser.add_argument("--bear-threshold", type=float, default=DEFAULT_BEAR_THRESHOLD,
                        help="Продажа при Медвежий не ниже этого процента; по умолчанию 70")
    parser.add_argument("--commission-bps", type=float, default=DEFAULT_COMMISSION_BPS,
                        help="Комиссия на круг в б.п. от OPEN; по умолчанию 3 (0.03 процента)")
    args = parser.parse_args(argv)
    try:
        data, database = load_regime_trade_data(args.input, args.sheet, args.db)
        result = analyze_regime_trades(data, args.bull_threshold, args.bear_threshold, args.commission_bps)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Ошибка чтения или анализа: {error}", file=sys.stderr)
        return 2
    print(f"Файл: {args.input.resolve()}\nЛист: {args.sheet}\nБД: {database}")
    print(f"Покупка: Бычий >= {args.bull_threshold:g}%; продажа: Медвежий >= {args.bear_threshold:g}%.")
    print(f"Комиссия на круг: {args.commission_bps:g} б.п. от OPEN. Один контракт на сделку, вход OPEN → выход CLOSE.")
    print(f"Все доходы и комиссии — в пунктах. Пропущено конфликтующих сигналов: {result.attrs['conflicts']}.\n")
    print(result.to_string(index=False, float_format="{:.2f}".format))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
