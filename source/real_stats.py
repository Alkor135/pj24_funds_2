"""Описательные зависимости и результаты дневных торговых сценариев.

Логика: границы групп строятся только по обучению, корреляции — без финального
теста. Сигнал умножается на простую доходность OPEN → CLOSE, расходы списываются
один раз на круг. Пропуски фактов сохраняются как None. Кривая — сумма условных
доходностей, а не доходность капитала. Модуль пакета source используется
корневым скриптом research_real.py.

Примеры из папки проекта:
    python research_real.py --cost-bps 3
    python research_real.py --min-history 200 --min-test-trades 30
"""

from __future__ import annotations

import math
from bisect import bisect_left

import numpy as np

from .real_data import FEATURE_NAMES, STATES, Record, ResearchConfig, eligible


def quantile_cuts(values, probabilities=(0.2,0.4,0.6,0.8)) -> tuple:
    """Возвращает уникальные границы квантилей probabilities конечных values; пустым — ()."""
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return tuple(sorted(set(float(v) for v in np.quantile(clean, probabilities)))) if clean else ()


def ranks(values) -> np.ndarray:
    """Возвращает средние ранги элементов values с одинаковыми рангами для совпадений."""
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind='stable')
    result = np.empty(len(values))
    start = 0
    while start < len(values):
        end = start+1
        while end < len(values) and values[order[end]] == values[order[start]]:
            end += 1
        result[order[start:end]] = (start+end-1)/2+1
        start = end
    return result


def correlation(x, y, spearman=False) -> float | None:
    """Возвращает корреляцию x/y, ранговую при spearman; константам и малой выборке — None."""
    if len(x) < 2:
        return None
    a, b = (ranks(x),ranks(y)) if spearman else (np.asarray(x,dtype=float),np.asarray(y,dtype=float))
    a, b = a-a.mean(), b-b.mean()
    scale = np.linalg.norm(a)*np.linalg.norm(b)
    return float(np.dot(a,b)/scale) if scale>0 else None


def describe(values) -> dict:
    """Возвращает число, среднее, медиану и характеристики выигрышей/проигрышей values без None."""
    a = np.asarray([v for v in values if v is not None], dtype=float)
    positive, negative = a[a>0], a[a<0]
    return {'n':len(a), 'mean':float(a.mean()) if len(a) else None,
            'median':float(np.median(a)) if len(a) else None,
            'win_rate':float(np.mean(a>0)) if len(a) else None,
            'mean_win':float(positive.mean()) if len(positive) else None,
            'mean_loss':float(negative.mean()) if len(negative) else None}


def daily_results(records: list[Record], sides: list[int], cost_bps: float) -> list:
    """Возвращает дневные результаты records/sides за вычетом cost_bps на круг; без факта — None."""
    if len(records) != len(sides) or any(s not in (-1,0,1) for s in sides):
        raise ValueError('Число сигналов должно совпадать с записями; направления -1/0/1.')
    return [None if row.r_oc is None else side*row.r_oc-abs(side)*cost_bps/10000
            for row,side in zip(records,sides)]


def strategy_metrics(records, sides, cost_bps, eligible_mask=None) -> dict:
    """Возвращает метрики records/sides при cost_bps на доступных днях eligible_mask, включая NA."""
    mask = [True]*len(records) if eligible_mask is None else list(eligible_mask)
    if len(mask) != len(records):
        raise ValueError('Длина маски доступности не соответствует календарю.')
    values = daily_results(records,sides,cost_bps)
    daily = [v for v,m in zip(values,mask) if m and v is not None]
    trades = [v for v,s,m in zip(values,sides,mask) if m and s!=0 and v is not None]
    signals = sum(m and s!=0 for s,m in zip(sides,mask))
    summary = describe(trades)
    total = sum(daily)
    peak, cumulative, drawdown = 0.0, 0.0, 0.0
    for v in daily:
        cumulative += v
        peak = max(peak,cumulative)
        drawdown = max(drawdown,peak-cumulative)
    gains = sum(v for v in trades if v>0)
    losses = -sum(v for v in trades if v<0)
    gross_points = [s*r.real_points for r,s,m in zip(records,sides,mask)
                    if m and s!=0 and r.real_points is not None]
    return {'signals':signals,'trades':len(trades),'missing_results':signals-len(trades),
            'eligible_days':sum(mask),'known_days':len(daily),
            'coverage':len(daily)/sum(mask) if sum(mask) else None,
            **{k:v for k,v in summary.items() if k!='n'},
            'total':total if daily else None,'per_day':total/len(daily) if daily else None,
            'max_drawdown':drawdown if daily else None,
            'profit_factor':gains/losses if losses else (math.inf if gains else None),
            'gross_points_total':sum(gross_points) if gross_points else None}


def interval_label(index: int, cuts: tuple) -> str:
    """Возвращает подпись интервала index с верхней включённой границей по cuts."""
    lower = '-∞' if index==0 else f'{cuts[index-1]:.8g}'
    upper = '+∞' if index==len(cuts) else f'{cuts[index]:.8g}'
    return f'({lower}; {upper}]'


def conditional_analysis(records, periods, config: ResearchConfig) -> dict:
    """Возвращает корреляции обучения и условные таблицы обучения/выбора records по periods/config."""
    train_days = set(periods['train'])
    train = [r for r in records if r.day in train_days and eligible(r,config)]
    cuts = {f:quantile_cuts([r.features.get(f) for r in train]) for f in FEATURE_NAMES}
    correlations = []
    for feature in FEATURE_NAMES:
        for target in ('real_points','r_oc','sign','absolute'):
            pairs = []
            for row in train:
                x = row.features.get(feature)
                y = row.real_points if target=='real_points' else row.r_oc
                if x is None or y is None:
                    continue
                if target=='sign':
                    y = float(np.sign(y))
                elif target=='absolute':
                    y = abs(y)
                pairs.append((x,y))
            x, y = zip(*pairs) if pairs else ((),())
            correlations.append({'feature':feature,'target':target,'n':len(pairs),
                                 'pearson':correlation(x,y),'spearman':correlation(x,y,True)})
    flat_median = quantile_cuts([r.features.get('p_flat') for r in train],(0.5,))
    groups = []
    for period in ('train','validation'):
        days = set(periods[period])
        rows = [r for r in records if r.day in days and eligible(r,config)]
        for feature in (*FEATURE_NAMES, 'state', 'edge_state', 'edge_flat'):
            buckets = {}
            for row in rows:
                if feature=='state':
                    if not eligible(row,config,True):
                        continue
                    label = row.state
                elif feature in ('edge_state','edge_flat'):
                    value = row.features.get('edge_direction')
                    if value is None:
                        continue
                    label = interval_label(bisect_left(cuts['edge_direction'],value),cuts['edge_direction'])
                    if feature=='edge_state':
                        if not eligible(row,config,True):
                            continue
                        label += ' / '+row.state
                    else:
                        flat = row.features.get('p_flat')
                        if not flat_median or flat is None:
                            continue
                        label += ' / '+('низкая Флэт' if flat<=flat_median[0] else 'высокая Флэт')
                else:
                    value = row.features.get(feature)
                    if value is None or (feature in ('edge_regime','p_side') and not eligible(row,config,True)):
                        continue
                    label = interval_label(bisect_left(cuts[feature],value),cuts[feature])
                buckets.setdefault(label,[]).append(row)
            for label, members in sorted(buckets.items()):
                for unit, values in (('points',[r.real_points for r in members]),
                                     ('return',[r.r_oc for r in members])):
                    summary = describe(values)
                    groups.append({'period':period,'feature':feature,'group':label,'unit':unit,
                                   **summary,'small_group':summary['n']<30})
    return {'cuts':cuts,'flat_median':flat_median[0] if flat_median else None,
            'correlations':correlations,'groups':groups}
