"""Последовательная проверка, вероятностные метрики и блочные интервалы Real.

Логика: каждый проверочный блок получает правила только из предшествующей
истории. Две вероятностные модели сравниваются с причинными базовыми частотами.
Блочное повторное формирование сохраняет пары сигналов, фактов и доступности.
Модуль пакета source используется корневым research_real.py;
исходные книги не изменяются.

Примеры из папки проекта:
    python research_real.py --cost-bps 3 --bootstrap-repeats 2000 --seed 42
    python research_real.py --min-test-trades 30
"""

from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np

from .real_data import MOVEMENTS, STATES, Dataset, ResearchConfig, eligible
from .real_rules import combined_signals, select_rules
from .real_stats import daily_results, strategy_metrics


def score_probabilities(probabilities, classes) -> dict:
    """Возвращает Brier 0–2, LogLoss, точность и матрицу ошибок probabilities/classes; пустым — NA."""
    if not classes:
        return {'n':0,'brier':None,'logloss':None,'accuracy':None,'ties':0,'confusion':[[0]*4 for _ in range(3)]}
    p = np.asarray(probabilities,dtype=float)
    actual = np.asarray(classes,dtype=int)
    truth = np.eye(3)[actual]
    maximum = p.max(axis=1)
    ties = np.sum(p==maximum[:,None],axis=1)>1
    prediction = np.argmax(p,axis=1)
    prediction[ties] = 3
    confusion = np.zeros((3,4),dtype=int)
    np.add.at(confusion,(actual,prediction),1)
    bounded = np.maximum(p,1e-12)
    bounded /= bounded.sum(axis=1,keepdims=True)
    return {'n':len(actual),'brier':float(np.mean(np.sum((p-truth)**2,axis=1))),
            'logloss':float(np.mean(-np.log(bounded[np.arange(len(actual)),actual]))),
            'accuracy':float(np.mean(prediction==actual)),'ties':int(ties.sum()),
            'confusion':confusion.tolist()}


def unconditional(matrix, alpha: float) -> tuple | None:
    """Возвращает сглаженные общие частоты из прошлой matrix и alpha; без истории — None."""
    counts = np.asarray(matrix,dtype=float).sum(axis=0)
    total = counts.sum()
    return tuple((counts+alpha)/(total+3*alpha)) if total>0 else None


def probability_analysis(dataset: Dataset, periods) -> dict:
    """Возвращает метрики и калибровку двух моделей dataset по periods на одинаковых датах сравнений."""
    alpha = float(dataset.metadata.get('alpha',0.5))
    metrics, calibration, coverage = [], [], []
    for period, dates in periods.items():
        days = set(dates)
        rows = [r for r in dataset.records if r.day in days]
        for target,labels in (('movement',MOVEMENTS),('regime',STATES)):
            usable, bases = [], []
            forecast_count = 0
            for row in rows:
                p = row.p_d if target=='movement' else row.p_r
                actual = row.actual_d if target=='movement' else row.actual_r
                matrix = row.matrix_d if target=='movement' else row.matrix_r
                base = unconditional(matrix,alpha)
                forecast_count += int(p is not None)
                if p is not None and actual is not None and base is not None and (target=='movement' or row.state in STATES):
                    usable.append(row)
                    bases.append(base)
            coverage.append({'period':period,'target':target,'rows':len(rows),'forecasts':forecast_count,
                             'evaluated':len(usable),'excluded':len(rows)-len(usable)})
            actuals = [r.actual_d if target=='movement' else r.actual_r for r in usable]
            predictions = [r.p_d if target=='movement' else r.p_r for r in usable]
            majority = [tuple(1.0 if i==int(np.argmax(p)) else 0.0 for i in range(3)) for p in bases]
            models = {'model':predictions,'unconditional':bases,'majority':majority}
            if target=='regime':
                models['persistence'] = [tuple(1.0 if STATES[i]==r.state else 0.0 for i in range(3)) for r in usable]
            for name,probabilities in models.items():
                metrics.append({'period':period,'target':target,'model':name,**score_probabilities(probabilities,actuals)})
            for c,label in enumerate(labels):
                for b in range(5):
                    members = [(p[c],int(a==c)) for p,a in zip(predictions,actuals)
                               if min(4,int(p[c]*5))==b]
                    calibration.append({'period':period,'target':target,'class':label,'bin':b,
                        'lower':b/5,'upper':(b+1)/5,'n':len(members),
                        'predicted':float(np.mean([p for p,a in members])) if members else None,
                        'observed':float(np.mean([a for p,a in members])) if members else None})
    return {'metrics':metrics,'calibration':calibration,'coverage':coverage}


def walk_forward(dataset: Dataset, calendar, config: ResearchConfig) -> dict:
    """Возвращает четыре причинных выбора и проверочных блока dataset в первых 80% calendar/config."""
    folds, decisions = [], []
    n = len(calendar)
    for i in range(4,8):
        start, end = math.floor(i*n/10), math.floor((i+1)*n/10)
        if end<=start or start<5:
            folds.append({'fold':i-3,'train_end':None,'selection_end':None,
                          'start':calendar[start] if start<n and end>start else None,
                          'end':calendar[end-1] if end>start else None,
                          'trials':0,'long':None,'short':None,'status':'Недостаточно истории',
                          **strategy_metrics([],[],config.cost_bps)})
            continue
        split = math.floor(0.8*start)
        training_days, validation_days, test_days = set(calendar[:split]),set(calendar[split:start]),set(calendar[start:end])
        training = [r for r in dataset.records if r.day in training_days]
        validation = [r for r in dataset.records if r.day in validation_days]
        testing = [r for r in dataset.records if r.day in test_days]
        selected = select_rules(training,validation,config)
        sides = combined_signals(testing,selected['long'],selected['short'],config)
        folds.append({'fold':i-3,'train_end':calendar[split-1], 'selection_end':calendar[start-1],
                      'start':calendar[start],'end':calendar[end-1], 'trials':selected['trials'],
                      'long':asdict(selected['long']) if selected['long'] else None,
                      'short':asdict(selected['short']) if selected['short'] else None,
                      'status':'Проверка выполнена' if selected['long'] or selected['short'] else 'Правило не отобрано',
                      **strategy_metrics(testing,sides,config.cost_bps,[eligible(r,config) for r in testing])})
        decisions.extend({'date':r.day,'fold':i-3,'side':s,
                          'result':v,'available':eligible(r,config)}
                         for r,s,v in zip(testing,sides,daily_results(testing,sides,config.cost_bps)))
    return {'folds':folds,'decisions':decisions}


def percentile_interval(values) -> tuple:
    """Возвращает 2.5-й и 97.5-й процентили конечных values; пустым — (None,None)."""
    clean = np.asarray(values,dtype=float)
    clean = clean[np.isfinite(clean)]
    return tuple(float(v) for v in np.quantile(clean,(0.025,0.975))) if len(clean) else (None,None)


def bootstrap_intervals(records, sides, config: ResearchConfig, baseline_sides=None, eligible_mask=None) -> list[dict]:
    """Возвращает блочные интервалы records/sides/config и разницы с baseline_sides на eligible_mask."""
    n = len(records)
    known = np.asarray([r.r_oc is not None and eligible(r,config) for r in records],dtype=bool)
    if eligible_mask is not None:
        known &= np.asarray(eligible_mask,dtype=bool)
    daily = np.asarray([0.0 if v is None else v for v in daily_results(records,sides,config.cost_bps)])
    daily[~known] = 0
    trade = (np.asarray(sides)!=0)&known
    baseline_sides = [1]*n if baseline_sides is None else baseline_sides
    baseline = np.asarray([0.0 if v is None else v for v in daily_results(records,baseline_sides,config.cost_bps)])
    baseline[~known] = 0
    rng = np.random.default_rng(config.seed)
    result = []
    for requested in (5,20,60):
        block = min(requested,n) if n else 0
        means, per_days, deltas = [], [], []
        for repeat in range(config.bootstrap_repeats if n else 0):
            starts = rng.integers(0,n-block+1,size=math.ceil(n/block))
            indices = (starts[:,None]+np.arange(block)).ravel()[:n]
            count = int(trade[indices].sum())
            available = int(known[indices].sum())
            means.append(float(daily[indices].sum()/count) if count else math.nan)
            per_days.append(float(daily[indices].sum()/available) if available else math.nan)
            deltas.append(float((daily[indices]-baseline[indices]).sum()/available) if available else math.nan)
        low,high = percentile_interval(means)
        day_low,day_high = percentile_interval(per_days)
        delta_low,delta_high = percentile_interval(deltas)
        result.append({'block':requested,'effective_block':block,'mean_low':low,'mean_high':high,
                       'per_day_low':day_low,'per_day_high':day_high,
                       'delta_long_low':delta_low,'delta_long_high':delta_high,
                       'valid_repeats':sum(math.isfinite(v) for v in means),
                       'adequate_blocks':n>=2*requested,
                       'trades':int(trade.sum()),'repeats':config.bootstrap_repeats})
    return result


def research_status(metrics: dict, intervals: list, config: ResearchConfig, selected=True) -> str:
    """Возвращает исследовательский статус metrics/intervals/config, учитывая наличие selected."""
    if not selected:
        return 'Правило не отобрано'
    if metrics['trades']<config.min_test_trades:
        return 'Недостаточно сделок'
    if metrics['mean'] is None or metrics['mean']<=0:
        return 'Правило не прошло тест'
    if config.bootstrap_repeats>=1000 and len(intervals)==3 and all(
            i['mean_low'] is not None and i['mean_low']>0 and i['adequate_blocks']
            and i['valid_repeats']>=1000 for i in intervals):
        return 'Положительный результат устойчив к выбранной блочной проверке'
    return 'Положительный результат, убедительность недостаточна'
