"""Причинный расчёт дневных вероятностей RTS/MIX по методике проекта.

Логика: SQLite открывается только для чтения; история объединяется по LSTTRADE,
а цены разных экспираций не соединяются. Перед каждым баром выбирается ближайшая
из ранее известных экспираций строго позже TRADEDATE. По истории этого контракта
рассчитываются режим, пороги и две таблицы условных частот. Исход целевой даты
добавляется в обучение только после формирования её прогноза. FilledQuotes и
VOLUME=0 участвуют в режиме, но исключаются из движения OPEN → CLOSE.
Факт Real сохраняется отдельно как CLOSE−OPEN в пунктах; он не участвует
в прогнозе своей даты и отсутствует для будущего или синтетического бара.

Это модуль пакета source для двух корневых точек запуска; профиль — полный
дневной бар Day. Результаты по умолчанию сохраняются в outputs корня проекта.
Примеры из папки проекта:
    python forecast_rts.py
    python forecast_mix.py --start-date 2025-01-01 --end-date 2026-10-05
    python forecast_rts.py --forecast-date 2026-10-06 --previous-session 2026-10-05
    python forecast_mix.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sqlite3
import statistics
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Sequence


STATE_NAMES = ("Бычий", "Медвежий", "Боковой")
MOVEMENT_NAMES = ("Рост", "Падение", "Флэт")
MODEL_VERSION = "day-expiry-v1.0"


@dataclass(frozen=True)
class Config:
    """Параметры режима, волатильностей, сглаживания и признака малой выборки."""

    length: int = 20
    regime_window: int = 60
    movement_window: int = 60
    k: float = 1.0
    q: float = 0.25
    alpha: float = 0.5
    n_min: int = 200

    def __post_init__(self):
        """Проверяет допустимость полей конфигурации; результат отсутствует."""
        if self.length < 3 or self.regime_window < 2 or self.movement_window < 2:
            raise ValueError("L должен быть >=3, окна волатильности — >=2.")
        if any(not math.isfinite(v) or v <= 0 for v in (self.k, self.q, self.alpha)):
            raise ValueError("k, q и alpha должны быть конечными положительными числами.")
        if self.n_min < 1:
            raise ValueError("n_min должен быть положительным.")


@dataclass(frozen=True)
class Quote:
    """Бар day/secid/expiry с OPEN, CLOSE, объёмом и признаком перенесённых цен."""

    day: date
    secid: str
    expiry: date
    open: float | None
    close: float | None
    volume: int
    filled: bool = False


@dataclass(frozen=True)
class Volatility:
    """Выборочная sigma, размер последовательного окна и причина недоступности."""

    sigma: float | None
    window: int
    reason: str = ""


@dataclass(frozen=True)
class RegimeFeature:
    """Режим 0/1/2, накопленная логдоходность и сдвинутая волатильность."""

    state: int | None
    g: float | None
    sigma: float | None
    window: int
    reason: str = ""


@dataclass(frozen=True)
class Forecast:
    """Строка day/secid/expiry, две тройки долей и факты в details, включая Real."""

    day: date
    secid: str | None
    expiry: date | None
    movement: tuple[float, float, float] | None
    regime: tuple[float, float, float] | None
    details: dict


def positive(value: float | None) -> bool:
    """Возвращает True, если value — конечная положительная цена."""
    return value is not None and math.isfinite(value) and value > 0


def log_ratio(end: float, start: float) -> float:
    """Возвращает ln(end/start) для положительных цен без переполнения отношения."""
    ratio = end / start
    return math.log(ratio) if positive(ratio) else math.log(end) - math.log(start)


def volatility(values: Sequence[float | None], maximum: int) -> Volatility:
    """Оценивает sigma с ddof=1 по максимум maximum последним значениям values.

    Пропуск обрывает окно. Возвращает sigma=None при окне короче двух или нуле.
    """
    tail = []
    for value in reversed(values):
        if value is None or not math.isfinite(value):
            break
        tail.append(value)
        if len(tail) >= maximum:
            break
    if len(tail) < 2:
        return Volatility(None, len(tail), "INSUFFICIENT_VOL_HISTORY")
    sigma = statistics.stdev(tail)
    if sigma == 0:
        return Volatility(None, len(tail), "ZERO_VOLATILITY")
    return Volatility(sigma, len(tail))


def regime_feature(closes: Sequence[float | None], config: Config) -> RegimeFeature:
    """Размечает последнее CLOSE в closes по L и параметрам config.

    Использует непрерывную историю и исключает текущую доходность из sigma.
    Возвращает режим 0=Бычий, 1=Медвежий, 2=Боковой либо причину NA.
    """
    tail = []
    for close in reversed(closes):
        if not positive(close):
            break
        tail.append(close)
        if len(tail) >= max(config.length + 1, config.regime_window + 2):
            break
    tail.reverse()
    if len(tail) < config.length + 1:
        return RegimeFeature(None, None, None, max(0, len(tail) - 2),
                             "INSUFFICIENT_REGIME_HISTORY")
    returns = [log_ratio(b, a) for a, b in zip(tail, tail[1:])]
    vol = volatility(returns[:-1], config.regime_window)
    g = log_ratio(tail[-1], tail[-config.length - 1])
    if vol.sigma is None:
        return RegimeFeature(None, g, None, vol.window, vol.reason)
    boundary = config.k * vol.sigma * math.sqrt(config.length)
    state = 0 if g > boundary else 1 if g < -boundary else 2
    return RegimeFeature(state, g, vol.sigma, vol.window)


def movement_return(quote: Quote | None) -> float | None:
    """Возвращает ln(CLOSE/OPEN) quote при настоящих сделках; иначе None."""
    if (quote is None or quote.filled or quote.volume <= 0
            or not positive(quote.open) or not positive(quote.close)):
        return None
    return log_ratio(quote.close, quote.open)


def classify_movement(value: float, epsilon: float) -> int:
    """Возвращает Up=0, Down=1 или Flat=2 для value и прошлого порога epsilon."""
    return 0 if value > epsilon else 1 if value < -epsilon else 2


def estimate(matrix: Sequence[Sequence[int]], state: int | None, alpha: float):
    """Оценивает три исхода matrix при state со сглаживанием alpha.

    Возвращает (доли либо None, источник, реальный размер выборки).
    Пустая условная строка заменяется общей; пустая история даёт NO_HISTORY.
    """
    row = list(matrix[state]) if state is not None else [0, 0, 0]
    source = "CONDITIONAL_STATE"
    if not sum(row):
        row = [sum(r[c] for r in matrix) for c in range(3)]
        source = "FALLBACK_UNCONDITIONAL"
    count = sum(row)
    if not count:
        return None, "NO_HISTORY", 0
    probabilities = tuple((n + alpha) / (count + 3 * alpha) for n in row)
    if any(p < 0 or not math.isfinite(p) for p in probabilities) or abs(sum(probabilities) - 1) > 1e-12:
        raise ArithmeticError("Некорректная тройка вероятностей.")
    return probabilities, source, count


def read_database(path: Path, series: str) -> list[Quote]:
    """Читает Day и FilledQuotes из path только для чтения, проверяя series.

    Возвращает список Quote. Неверная схема, серия или пустая БД дают ошибку.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"БД не найдена: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "Day" not in tables:
            raise ValueError("В БД отсутствует таблица Day.")
        columns = {r[1].upper() for r in connection.execute("PRAGMA table_info(Day)")}
        required = {"TRADEDATE", "SECID", "LSTTRADE", "OPEN", "CLOSE", "VOLUME"}
        if not required <= columns:
            raise ValueError(f"В Day отсутствуют поля: {sorted(required - columns)}")
        if "Metadata" in tables:
            asset = connection.execute("SELECT VALUE FROM Metadata WHERE KEY='ASSETCODE'").fetchone()
            if asset and asset[0] != series:
                raise ValueError(f"Ожидалась серия {series}, в Metadata указана {asset[0]}.")
        filled = set(connection.execute("SELECT TRADEDATE,SECID FROM FilledQuotes")) if "FilledQuotes" in tables else set()
        rows = connection.execute("SELECT TRADEDATE,SECID,LSTTRADE,OPEN,CLOSE,VOLUME FROM Day ORDER BY TRADEDATE,LSTTRADE,SECID")
        quotes = []
        for day, secid, expiry, opening, close, volume in rows:
            if not secid or volume is None or int(volume) < 0:
                raise ValueError(f"Некорректный SECID или объём: {day}, {secid}.")
            quotes.append(Quote(date.fromisoformat(day), str(secid), date.fromisoformat(expiry),
                                float(opening) if opening is not None else None,
                                float(close) if close is not None else None,
                                int(volume), (day, secid) in filled))
    if not quotes:
        raise ValueError("Таблица Day пуста.")
    return quotes


def normalize_quotes(quotes: Sequence[Quote]) -> dict[tuple[date, date], Quote]:
    """Объединяет quotes по (дата, экспирация), возвращая один бар на ключ.

    Одинаковые алиасы выбираются по SECID, конфликтующие цены вызывают ошибку.
    """
    result = {}
    for quote in sorted(quotes, key=lambda q: (q.day, q.expiry, q.secid)):
        if quote.expiry < quote.day:
            raise ValueError(f"Экспирация {quote.expiry} раньше бара {quote.day}.")
        key = (quote.day, quote.expiry)
        previous = result.get(key)
        if previous is not None:
            if (previous.open, previous.close, previous.volume) != (quote.open, quote.close, quote.volume):
                raise ValueError(f"Конфликт алиасов {previous.secid}/{quote.secid}: {key}.")
            if quote.filled and not previous.filled:
                from dataclasses import replace
                result[key] = replace(previous, filled=True)
        else:
            result[key] = quote
    if not result:
        raise ValueError("Нет котировок для расчёта.")
    return result


def trailing_quotes(bars: dict, calendar: Sequence[date], index: int,
                    expiry: date, maximum: int) -> list[Quote | None]:
    """Возвращает максимум maximum баров expiry до calendar[index] включительно.

    Пропуски остаются None; их обработка не сжимает календарь.
    """
    return [bars.get((calendar[i], expiry))
            for i in range(max(0, index - maximum + 1), index + 1)]


def build_forecasts(quotes: Sequence[Quote], config: Config | None = None,
                    forecast_date: date | None = None,
                    previous_session: date | None = None) -> list[Forecast]:
    """Реконструирует причинные строки из quotes с параметрами config.

    Без forecast_date возвращает всю историю. Для будущей даты требуется
    previous_session, совпадающая с последним баром БД. Возвращает строки
    Forecast; обучение всегда проходит по всей предшествующей истории.
    Факт details["real_points"] равен CLOSE−OPEN независимо от прогрева модели,
    но отсутствует у будущего бара, перенесённых цен и записей без сделок.
    """
    config = config or Config()
    bars = normalize_quotes(quotes)
    calendar = sorted({day for day, expiry in bars})
    last_day = calendar[-1]
    if forecast_date is not None:
        if forecast_date > last_day:
            if previous_session is None or previous_session != last_day:
                raise ValueError(f"STALE_DATA: для будущей даты укажите --previous-session, "
                                 f"совпадающую с последним баром БД {last_day}; "
                                 "это должна быть действительная предыдущая сессия.")
            calendar.append(forecast_date)
        elif forecast_date not in calendar:
            raise ValueError("Целевая историческая дата отсутствует в календаре Day.")
        elif previous_session is not None:
            position = calendar.index(forecast_date)
            expected = calendar[position - 1] if position else None
            if previous_session != expected:
                raise ValueError(f"Неверная предыдущая сессия: ожидалась {expected}.")
    elif previous_session is not None:
        raise ValueError("--previous-session используется вместе с --forecast-date.")

    by_day = {}
    for (day, expiry), quote in bars.items():
        by_day.setdefault(day, {})[expiry] = quote
    known = {}
    matrix_d = [[0] * 3 for _ in range(3)]
    matrix_r = [[0] * 3 for _ in range(3)]
    result = []
    maximum = max(config.length + 1, config.regime_window + 2, config.movement_window)

    for index, day in enumerate(calendar):
        candidates = sorted(expiry for expiry in known if expiry > day)
        expiry = candidates[0] if candidates else None
        current_bars = by_day.get(day, {})
        statuses = ["UNVALIDATED", "HISTORICAL_RECONSTRUCTION" if day <= last_day else "CALENDAR_USER_SUPPLIED"]
        if expiry is None:
            display = [e for e in current_bars if e > day]
            display_expiry = min(display) if display else None
            secid = current_bars[display_expiry].secid if display_expiry else None
            display_quote = current_bars.get(display_expiry)
            details = {"signal_state": None, "movement_source": "NO_PRIOR_CONTRACT",
                       "regime_source": "NO_PRIOR_CONTRACT", "n_D": 0, "n_R": 0,
                       "train_D": sum(map(sum, matrix_d)), "train_R": sum(map(sum, matrix_r)),
                       "real_points": display_quote.close - display_quote.open
                           if movement_return(display_quote) is not None else None,
                       "statuses": "; ".join(statuses + ["NO_PRIOR_CONTRACT"])}
            forecast = Forecast(day, secid, display_expiry, None, None, details)
        else:
            target = current_bars.get(expiry)
            secid = target.secid if target is not None else known[expiry].secid
            if secid != known[expiry].secid:
                statuses.append("DISPLAY_SECID_FROM_TARGET_ROW")
            past_quotes = trailing_quotes(bars, calendar, index - 1, expiry, maximum)
            past_closes = [q.close if q is not None else None for q in past_quotes]
            signal = regime_feature(past_closes, config)
            past_y = [movement_return(q) for q in past_quotes]
            oc = volatility(past_y, config.movement_window)
            epsilon = config.q * oc.sigma if oc.sigma is not None else None

            close_run = []
            for close in reversed(past_closes):
                if not positive(close):
                    break
                close_run.append(close)
            close_run.reverse()
            cc = volatility([log_ratio(b, a) for a, b in zip(close_run, close_run[1:])], config.regime_window)
            target_boundary = config.k * cc.sigma * math.sqrt(config.length) if cc.sigma is not None and len(close_run) >= config.length else None
            anchor = close_run[-config.length] if target_boundary is not None else None
            movement, source_d, n_d = estimate(matrix_d, signal.state, config.alpha)
            regime, source_r, n_r = estimate(matrix_r, signal.state, config.alpha)
            if epsilon is None:
                movement, source_d = None, "INVALID_MOVEMENT_THRESHOLD"
                statuses.append("MOVEMENT_" + oc.reason)
            if target_boundary is None:
                regime, source_r = None, "INVALID_REGIME_THRESHOLD"
                statuses.append("TARGET_" + (cc.reason or "INSUFFICIENT_REGIME_HISTORY"))
            if signal.reason:
                statuses.append("SIGNAL_" + signal.reason)
            if signal.sigma is not None and signal.window < config.regime_window:
                statuses.append("VOL_SHORT_WINDOW")
            if cc.sigma is not None and cc.window < config.regime_window:
                statuses.append("TARGET_VOL_SHORT_WINDOW")
            if oc.sigma is not None and oc.window < config.movement_window:
                statuses.append("OC_VOL_SHORT_WINDOW")
            if movement is not None and n_d < config.n_min:
                statuses.append("LOW_SAMPLE_D")
            if regime is not None and n_r < config.n_min:
                statuses.append("LOW_SAMPLE_R")
            if target is None and day <= last_day:
                statuses.append("MISSING_TARGET_BAR")
            if target is not None and (target.filled or target.volume == 0):
                statuses.append("NO_SESSION_TRADES")

            actual_y = movement_return(target)
            actual_d = classify_movement(actual_y, epsilon) if actual_y is not None and epsilon is not None else None
            actual_r = regime_feature(past_closes + [target.close if target else None], config).state
            details = {
                "signal_day": calendar[index - 1] if index else None,
                "signal_secid": known[expiry].secid,
                "signal_state": signal.state, "signal_g": signal.g,
                "signal_sigma": signal.sigma, "signal_window": signal.window,
                "target_sigma": cc.sigma, "target_window": cc.window,
                "target_boundary": target_boundary, "target_anchor": anchor,
                "oc_sigma": oc.sigma, "oc_window": oc.window, "epsilon": epsilon,
                "lower_movement": math.expm1(-epsilon) if epsilon is not None else None,
                "upper_movement": math.expm1(epsilon) if epsilon is not None else None,
                "lower_close": anchor * math.exp(-target_boundary) if anchor is not None else None,
                "upper_close": anchor * math.exp(target_boundary) if anchor is not None else None,
                "n_D": n_d, "n_R": n_r, "movement_source": source_d, "regime_source": source_r,
                "train_D": sum(map(sum, matrix_d)), "train_R": sum(map(sum, matrix_r)),
                "matrix_D": [row.copy() for row in matrix_d], "matrix_R": [row.copy() for row in matrix_r],
                "actual_y": actual_y, "actual_movement": actual_d, "actual_regime": actual_r,
                "real_points": target.close - target.open if actual_y is not None else None,
                "filled_target": bool(target and target.filled),
                "statuses": "; ".join(statuses),
            }
            forecast = Forecast(day, secid, expiry, movement, regime, details)
            # Прогноз и снимки счётчиков уже зафиксированы; теперь доступен исход.
            if signal.state is not None:
                if actual_d is not None:
                    matrix_d[signal.state][actual_d] += 1
                if actual_r is not None:
                    matrix_r[signal.state][actual_r] += 1

        if forecast_date is None or day == forecast_date:
            result.append(forecast)
        # Метаданные целевого дня не участвуют в выборе его контракта.
        known.update(current_bars)
        if forecast_date is not None and day == forecast_date:
            break
    return result


def file_hash(path: Path) -> str:
    """Возвращает SHA-256 содержимого файла path для идентификации снимка БД."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iso_date(value: str) -> date:
    """Преобразует value формата ГГГГ-ММ-ДД в date либо ошибку argparse."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Нужна дата в формате ГГГГ-ММ-ДД.") from error


def run_cli(series: str, argv: Sequence[str] | None = None) -> int:
    """Запускает расчёт series с аргументами argv; возвращает код 0 или 2.

    Пути по умолчанию относятся к соответствующей БД и outputs в корне проекта.
    Каталог outputs определяется независимо от текущей рабочей папки.
    Основной лист включает Флэт и факт Real=CLOSE−OPEN в пунктах.
    """
    parser = argparse.ArgumentParser(description=f"Вероятности {series} по полному дневному бару Day.", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать описание параметров и завершить работу")
    parser._optionals.title = "Параметры"
    parser.add_argument("--db", type=Path, default=Path(f"C:/data_quote/day2/{series}_day2.db"), help="Исходная SQLite БД")
    parser.add_argument("--output", type=Path, help="Путь XLSX; по умолчанию outputs/СЕРИЯ_probabilities.xlsx")
    parser.add_argument("--start-date", type=iso_date, help="Первая выводимая дата; обучение не сокращается")
    parser.add_argument("--end-date", type=iso_date, help="Последняя выводимая дата")
    parser.add_argument("--forecast-date", type=iso_date, help="Вывести только прогноз этой даты")
    parser.add_argument("--previous-session", type=iso_date, help="Подтверждённая предыдущая торговая дата для будущего прогноза")
    parser.add_argument("--length", type=int, default=20, help="Окно режима L")
    parser.add_argument("--regime-window", type=int, default=60, help="Максимальное окно sigma_CC")
    parser.add_argument("--movement-window", type=int, default=60, help="Максимальное окно sigma_OC")
    parser.add_argument("--k", type=float, default=1.0, help="Коэффициент границы режима")
    parser.add_argument("--q", type=float, default=0.25, help="Коэффициент границы незначительного изменения")
    parser.add_argument("--alpha", type=float, default=0.5, help="Сглаживание на один исход")
    parser.add_argument("--n-min", type=int, default=200, help="Ориентир малой выборки")
    parser.add_argument("--overwrite", action="store_true", help="Разрешить перезапись результата")
    args = parser.parse_args(argv)
    output = args.output or Path(__file__).resolve().parents[1] / "outputs" / f"{series}_probabilities.xlsx"
    try:
        if args.start_date and args.end_date and args.start_date > args.end_date:
            raise ValueError("--start-date позже --end-date.")
        if args.forecast_date and (args.start_date or args.end_date):
            raise ValueError("--forecast-date не совмещается с фильтрами диапазона.")
        if output.suffix.lower() != ".xlsx":
            raise ValueError("Результат должен иметь расширение .xlsx.")
        if output.exists() and not args.overwrite:
            raise FileExistsError(f"Файл существует: {output}. Используйте --overwrite или другой --output.")
        if output.resolve() == args.db.resolve():
            raise ValueError("Результат не может совпадать с исходной БД.")
        config = Config(args.length, args.regime_window, args.movement_window, args.k, args.q, args.alpha, args.n_min)
        snapshot_hash = file_hash(args.db)
        quotes = read_database(args.db, series)
        rows = build_forecasts(quotes, config, args.forecast_date, args.previous_session)
        rows = [r for r in rows if (args.start_date is None or r.day >= args.start_date)
                and (args.end_date is None or r.day <= args.end_date)]
        if not rows:
            raise ValueError("В указанном диапазоне нет строк.")
        if file_hash(args.db) != snapshot_hash:
            raise ValueError("БД изменилась во время расчёта. Повторите на стабильном снимке.")
        from .funds_xlsx import write_workbook
        metadata = {
            "Серия": series, "Версия модели": MODEL_VERSION,
            "Источник": str(args.db.resolve()), "SHA-256 БД": snapshot_hash,
            "Методика": "Стратегия_фондов_методика_таблицы.md, версия 1.0",
            "Профиль": "DB_DAY: полный дневной бар Day, TRADEDATE",
            "Время создания XLSX": datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds"),
            "Первый бар БД": min(q.day for q in quotes), "Последний бар БД": max(q.day for q in quotes),
            "Строк Day": len(quotes), "Экспираций": len({q.expiry for q in quotes}),
            "Перенесённых строк": sum(q.filled for q in quotes), "Строк VOLUME=0": sum(q.volume == 0 for q in quotes),
            "L": config.length, "W_max": config.regime_window, "V_max": config.movement_window,
            "k": config.k, "q": config.q, "alpha": config.alpha, "n_min": config.n_min,
            "ddof": 1, "Сдвиг sigma режима": 1, "Горизонт": "Один следующий бар",
            "История обучения": "Расширяющаяся; один основной контракт на дату; сначала прогноз, затем исход",
            "Идентификатор контракта": "LSTTRADE; разные экспирации не склеиваются",
            "Правило выбора": "Ближайшая известная ранее экспирация строго позже целевой TRADEDATE",
            "Календарь истории": "Уникальные TRADEDATE в Day; отсутствие всех контрактов требует внешнего календаря",
            "Будущая предыдущая сессия": args.previous_session,
            "Ограничение сессии": "Часы агрегации и времена доступности данных отсутствуют в БД; это реконструкция по полным дневным барам",
            "Ограничение истории": "Текущий снимок может содержать поздние исправления. Факт публикации исторических прогнозов до открытия не установлен",
            "Отображаемый SECID": "Алиас выбранной экспирации из целевого бара; для будущей даты — последний известный. Целевой алиас не влияет на расчёт",
            "Режим": "Состояние на закрытии целевого бара по окну L между закрытиями",
            "Вероятности": "Оценки модели; исходные доли на листе Диагностика; отображение округлено до 0.1 процентного пункта с суммой 100.0%",
            "NA": "Пустые ячейки означают отсутствие пригодного расчёта, а не нулевую вероятность",
            "Real": "CLOSE−OPEN выбранного контракта в пунктах; отрицательное значение — падение. Будущий бар и записи без реальных сделок имеют пустое значение",
            "Статус проверки": "UNVALIDATED: калибровка и торговое преимущество не проверялись",
        }
        write_workbook(output, rows, metadata, overwrite=args.overwrite)
        print(f"Создан {output.resolve()}\nСтрок: {len(rows)}; даты: {rows[0].day} — {rows[-1].day}.")
        print(f"Последний бар БД: {max(q.day for q in quotes)}. Профиль DB_DAY; модели UNVALIDATED.")
        return 0
    except (OSError, ValueError, sqlite3.Error, ImportError, ArithmeticError) as error:
        print(f"Ошибка: {error}")
        return 2
