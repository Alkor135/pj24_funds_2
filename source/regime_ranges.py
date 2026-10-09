"""Подбор диапазонов вероятностей и дневная симуляция в пунктах.

Логика: перебирает все 210 непрерывных диапазонов с границами через 5%
и отключение направления; 211 × 211 пар оцениваются отдельно для каждого
инструмента. Максимизируется совокупный доход после комиссии на всей истории.
Конфликты покупки/продажи пропускаются. Нижняя граница включена, верхняя
исключена, кроме 100%. Вход OPEN, выход CLOSE того же дня, один контракт.
Дни без исполнения сохраняются в непрерывном календаре. Годовые показатели
рассчитываются дополнительно и не обнуляют полную накопительную кривую.

Модуль вызывается корневым скриптом; расходы задаются на полный круг в б.п.
Примеры из папки проекта:
    python research_real_regime_ranges.py
    python research_real_regime_ranges.py --only RTS --commission-bps 3
    python -m unittest discover -s tests -p test_regime_ranges.py -v
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral

import numpy as np
import pandas as pd

STEP_PCT = 5


@dataclass(frozen=True)
class ProbabilityRange:
    """Хранит нижнюю lower и верхнюю upper границы в целых процентах."""

    lower: int
    upper: int

    def __post_init__(self):
        """Проверяет упорядоченные границы 0–100% с шагом 5%; результата нет."""
        if (any(not isinstance(value, Integral) or isinstance(value, bool)
                for value in (self.lower, self.upper))
                or not 0 <= self.lower < self.upper <= 100
                or self.lower % STEP_PCT or self.upper % STEP_PCT):
            raise ValueError("Границы должны быть целыми кратными 5%: 0 <= нижняя < верхняя <= 100.")

    def label(self) -> str:
        """Возвращает обозначение включённых и исключённых границ диапазона."""
        closing = "]" if self.upper == 100 else ")"
        return f"[{self.lower}%; {self.upper}%{closing}"


def prepare_data(data: pd.DataFrame, commission_bps: float) -> pd.DataFrame:
    """Проверяет data и расходы commission_bps; возвращает копию по возрастанию дат.

    Обязательные колонки: Дата, Бычий, Медвежий, Real, OPEN. Вероятности —
    доли 0–1, Real и OPEN — пункты. Пустой Real разрешён и не заменяется
    фактом с нулевым результатом. Для известных фактов цены должны быть
    положительными. Даты уникальны; неверные данные вызывают ValueError.
    """
    if not math.isfinite(commission_bps) or commission_bps < 0:
        raise ValueError("Комиссия на круг должна быть конечной и неотрицательной.")
    required = ("Дата", "Бычий", "Медвежий", "Real", "OPEN")
    missing = [name for name in required if name not in data]
    if missing:
        raise ValueError("Отсутствуют колонки: " + ", ".join(missing))
    result = data.copy(deep=True)
    try:
        result["Дата"] = pd.to_datetime(result["Дата"], errors="raise").dt.normalize()
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Колонка Дата содержит неверные даты.") from error
    if result["Дата"].isna().any() or result["Дата"].duplicated().any():
        raise ValueError("Требуются непустые уникальные даты: один контракт на день.")
    for name in required[1:]:
        try:
            result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Колонка {name} содержит нечисловые значения.") from error
        known = result[name].dropna()
        if not np.isfinite(known.to_numpy()).all():
            raise ValueError(f"Колонка {name} содержит бесконечные значения.")
        if name in ("Бычий", "Медвежий") and not known.between(0, 1).all():
            raise ValueError(f"Вероятности {name} должны быть долями 0–1.")
    if (result[["Бычий", "Медвежий"]].dropna().sum(axis=1) > 1 + 1e-10).any():
        raise ValueError("Сумма вероятностей Бычий и Медвежий превышает 100%.")
    known = result["Real"].notna()
    prices = result.loc[known, "OPEN"]
    if (prices.isna() | (prices <= 0)).any():
        raise ValueError("Для известного Real требуется положительная цена OPEN.")
    if ((prices + result.loc[known, "Real"]) <= 0).any():
        raise ValueError("Цена CLOSE = OPEN + Real должна быть положительной.")
    return result.sort_values("Дата").reset_index(drop=True)


def probability_bins(values: pd.Series) -> np.ndarray:
    """Возвращает номера интервалов 0–19 для долей values; пропуск обозначает −1.

    Использует прямое сравнение с долями границ; 100% относится к интервалу 19.
    Вызывается после проверки диапазона исходных значений.
    """
    raw = values.to_numpy(dtype=float)
    edges = np.arange(0, 101, STEP_PCT) / 100
    bins = np.minimum(np.searchsorted(edges, raw, side="right") - 1, 19)
    return np.where(np.isnan(raw), -1, bins)


def range_signal(bins: np.ndarray, interval: ProbabilityRange | None) -> np.ndarray:
    """Возвращает маску попадания bins в interval; None отключает направление."""
    if interval is None:
        return np.zeros(len(bins), dtype=bool)
    return (bins >= interval.lower // STEP_PCT) & (bins < interval.upper // STEP_PCT)


def optimize_ranges(data: pd.DataFrame, commission_bps: float = 3) -> dict:
    """Возвращает лучшую пару диапазонов и таблицу всех 44 521 пар по data.

    commission_bps — расходы на круг от OPEN. Оптимум считается совместно,
    поскольку пересечения сигналов исключают сделки обоих направлений.
    Префиксные суммы по 20 × 20 группам дают точный результат полного перебора
    без повторной симуляции каждой пары. Если торговля не улучшает нулевой
    результат, направление можно отключить. При равном доходе с точностью
    1e-8 пункта выбирается меньше сделок, затем меньшая суммарная ширина,
    затем меньшие границы покупки и продажи. Исходные данные не изменяются.
    """
    frame = prepare_data(data, commission_bps)
    ranges = [None] + [ProbabilityRange(low, high)
                       for low in range(0, 100, STEP_PCT)
                       for high in range(low + STEP_PCT, 101, STEP_PCT)]
    lower = np.array([item.lower // STEP_PCT if item else 0 for item in ranges])
    upper = np.array([item.upper // STEP_PCT if item else 0 for item in ranges])
    width = (upper - lower) * STEP_PCT
    bull = probability_bins(frame["Бычий"])
    bear = probability_bins(frame["Медвежий"])
    known = frame["Real"].notna().to_numpy()
    real = frame["Real"].fillna(0).to_numpy()
    fee = np.where(known, frame["OPEN"].fillna(0).to_numpy() * commission_bps / 10000, 0)
    profits, counts = [], []
    for bins, side in ((bull, 1), (bear, -1)):
        available = (bins >= 0) & known
        totals = np.bincount(bins[available], weights=(side * real - fee)[available], minlength=20)
        numbers = np.bincount(bins[available], minlength=20)
        prefix = np.r_[0, totals.cumsum()]
        count_prefix = np.r_[0, numbers.cumsum()]
        profits.append(prefix[upper] - prefix[lower])
        counts.append(count_prefix[upper] - count_prefix[lower])
    overlap = (bull >= 0) & (bear >= 0)
    keys = bull[overlap] * 20 + bear[overlap]
    intersections = []
    for weights in (fee[overlap], known[overlap].astype(int), np.ones(overlap.sum())):
        histogram = np.bincount(keys, weights=weights, minlength=400).reshape(20, 20)
        prefix = np.pad(histogram.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))
        rectangles = (prefix[upper[:, None], upper[None, :]]
                      - prefix[lower[:, None], upper[None, :]]
                      - prefix[upper[:, None], lower[None, :]]
                      + prefix[lower[:, None], lower[None, :]])
        intersections.append(rectangles)
    # В пересечении +Real и −Real взаимно уничтожаются; отменяются обе комиссии.
    net = profits[0][:, None] + profits[1][None, :] + 2 * intersections[0]
    trades = np.rint(counts[0][:, None] + counts[1][None, :] - 2 * intersections[1]).astype(int)
    conflicts = np.rint(intersections[2]).astype(int)
    bull_id, bear_id = np.indices(net.shape)
    flat_bull, flat_bear = bull_id.ravel(), bear_id.ravel()
    order = np.lexsort((upper[flat_bear], lower[flat_bear], upper[flat_bull], lower[flat_bull],
                        width[flat_bull] + width[flat_bear], trades.ravel(), -net.ravel().round(8)))
    best = int(order[0])
    table = pd.DataFrame({
        "bull_lower": [ranges[index].lower if ranges[index] else None for index in flat_bull],
        "bull_upper": [ranges[index].upper if ranges[index] else None for index in flat_bull],
        "bear_lower": [ranges[index].lower if ranges[index] else None for index in flat_bear],
        "bear_upper": [ranges[index].upper if ranges[index] else None for index in flat_bear],
        "net_profit": net.ravel(), "trades": trades.ravel(), "conflicts": conflicts.ravel(),
    }).iloc[order].reset_index(drop=True)
    return {"bull_range": ranges[flat_bull[best]], "bear_range": ranges[flat_bear[best]],
            "net_profit": float(net.ravel()[best]), "pairs_evaluated": len(order), "candidates": table}


def simulate_ranges(data: pd.DataFrame, bull_range: ProbabilityRange | None,
                    bear_range: ProbabilityRange | None, commission_bps: float = 3) -> pd.DataFrame:
    """Возвращает непрерывный дневной журнал по data и двум выбранным диапазонам.

    bull_range/bear_range задают покупку/продажу; None отключает сторону.
    commission_bps — расходы на круг от OPEN. Конфликты пропускаются,
    сигнал без Real отмечается missing_fact и не считается исполненной
    сделкой. Нулевой Real исполняется с комиссией. Кривые cumulative_net,
    cumulative_long, cumulative_short не обнуляются на границах годов.
    """
    daily = prepare_data(data, commission_bps)
    buy = range_signal(probability_bins(daily["Бычий"]), bull_range)
    sell = range_signal(probability_bins(daily["Медвежий"]), bear_range)
    daily["conflict"] = buy & sell
    daily["side"] = np.where(buy & ~sell, 1, np.where(sell & ~buy, -1, 0))
    daily["executed"] = daily["side"].ne(0) & daily["Real"].notna()
    daily["missing_fact"] = daily["side"].ne(0) & daily["Real"].isna()
    daily["CLOSE"] = daily["OPEN"] + daily["Real"]
    daily["gross_profit"] = np.where(daily["executed"], daily["Real"] * daily["side"], 0)
    daily["commission"] = np.where(daily["executed"], daily["OPEN"] * commission_bps / 10000, 0)
    daily["net_profit"] = daily["gross_profit"] - daily["commission"]
    daily["cumulative_net"] = daily["net_profit"].cumsum()
    daily["cumulative_long"] = daily["net_profit"].where(daily["side"].eq(1), 0).cumsum()
    daily["cumulative_short"] = daily["net_profit"].where(daily["side"].eq(-1), 0).cumsum()
    return daily


def trade_metrics(daily: pd.DataFrame, side: int = 0) -> dict:
    """Возвращает показатели daily: side=0 — все сделки, 1 — покупки, −1 — продажи.

    Доходы и просадка — в пунктах; проценты капитала не вычисляются без модели
    капитала. Profit factor и доля побед считаются по результатам после комиссии.
    Просадка учитывает начальный ноль; неизвестные факты исключены из сделок.
    Если убытков нет при наличии прибыли, profit_factor равен +inf;
    для отсутствующих сделок неопределённые показатели равны None.
    """
    if side not in (-1, 0, 1):
        raise ValueError("Направление должно быть −1, 0 или 1.")
    direction = daily["side"].ne(0) if side == 0 else daily["side"].eq(side)
    executed = direction & daily["executed"]
    results = daily.loc[executed, "net_profit"].to_numpy(dtype=float)
    changes = daily["net_profit"].where(executed, 0).to_numpy(dtype=float)
    curve = np.r_[0, np.cumsum(changes)]
    drawdown = float((np.maximum.accumulate(curve) - curve).max())
    wins, losses = results[results > 0], results[results < 0]
    winning = float(wins.sum())
    losing = float(-losses.sum())
    net = float(results.sum())
    return {
        "days": len(daily), "signals": int(direction.sum()), "trades": len(results),
        "missing_facts": int((direction & daily["missing_fact"]).sum()),
        "conflicts": int(daily["conflict"].sum()),
        "gross_profit": float(daily.loc[executed, "gross_profit"].sum()),
        "commission": float(daily.loc[executed, "commission"].sum()), "net_profit": net,
        "wins": len(wins), "losses": len(losses), "breakeven": int((results == 0).sum()),
        "win_rate": len(wins) / len(results) if len(results) else None,
        "profit_factor": winning / losing if losing else (math.inf if winning else None),
        "max_drawdown": drawdown, "recovery_factor": net / drawdown if drawdown else None,
        "mean_profit": float(results.mean()) if len(results) else None,
        "median_profit": float(np.median(results)) if len(results) else None,
        "best_trade": float(results.max()) if len(results) else None,
        "worst_trade": float(results.min()) if len(results) else None,
        "average_win": float(wins.mean()) if len(wins) else None,
        "average_loss": float(losses.mean()) if len(losses) else None,
    }


def yearly_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    """Возвращает таблицу показателей daily по каждому году и трём направлениям.

    Даже год без сделок присутствует. Годовой P&L — результат только данного
    года, годовая просадка считается от нуля на его начале. Полные кривые
    симуляции не изменяются. При пустом календаре возвращает пустую таблицу.
    """
    rows = []
    for year, group in daily.groupby(daily["Дата"].dt.year, sort=True):
        for name, side in (("combined", 0), ("long", 1), ("short", -1)):
            rows.append({"year": int(year), "direction": name, **trade_metrics(group, side)})
    return pd.DataFrame(rows)
