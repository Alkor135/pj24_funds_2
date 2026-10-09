r"""Исследование зависимости Real от дневных вероятностей MIX и RTS.

Логика: читает исходные XLSX, фиксирует общий календарь 60/20/20, строит
условные таблицы на обучении и выборе, перебирает до 102 правил на серию.
Правила выбираются до окончательного теста; тест оценивает их с расходами
3 б.п. на круг и блочной неопределённостью. Дополнительно проверяются
вероятности и четыре последовательных участка. Исходники не изменяются.
Ресурсные модули импортируются из пакета source. Результаты: CSV/JSON/PNG
в outputs/real_research корня проекта, документ в папке Obsidian.

Примеры из папки проекта:
    .venv\Scripts\python.exe research_real.py
    python research_real.py --cost-bps 3 --bootstrap-repeats 2000 --seed 42
    python research_real.py --cost-scenarios-bps 0 2 3 5 10
    python research_real.py --mix outputs/MIX_probabilities.xlsx --rts outputs/RTS_probabilities.xlsx
    python research_real.py --output-dir outputs/real_research --report C:/reports/research.md
    python research_real.py --help

Расходы трактуются как доля условного номинала за вход и выход вместе.
Кривая представляет сумму условных результатов OPEN → CLOSE, не доходность
капитала или ГО. Готовый отчёт не является прогнозом сегодняшней сессии.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from source.real_data import Dataset, ResearchConfig, eligible, load_dataset, split_calendar
from source.real_rules import combined_signals, rule_signals, select_rules
from source.real_stats import conditional_analysis, daily_results, strategy_metrics
from source.real_validation import bootstrap_intervals, probability_analysis, research_status, walk_forward

PROJECT = Path(__file__).resolve().parent
DOCUMENTS = Path('C:/Users/Alkor/gd/Obsidian_Alkor/02_Projects_Проекты/Алготрейдинг/pj24_funds_2')
STRATEGY_NAMES = {'long':'Выбранная покупка','short':'Выбранная продажа','combined':'Совместные правила',
                  'always_long':'Ежедневная покупка','always_short':'Ежедневная продажа',
                  'edge_sign':'Знак перевеса','flat':'Без сделок'}


def analyze_dataset(dataset: Dataset, periods, config: ResearchConfig) -> dict:
    """Возвращает полное исследование dataset по periods/config, отделяя выбор от фактов теста."""
    dates = {key:set(value) for key,value in periods.items()}
    rows = dataset.records
    train = [r for r in rows if r.day in dates['train']]
    validation = [r for r in rows if r.day in dates['validation']]
    selection = select_rules(train,validation,config)
    # Общие даты сравнения задаёт прогноз движения; режим проверяет само правило.
    mask = [eligible(r,config) for r in rows]
    longs = rule_signals(rows,selection['long'],config)
    shorts = rule_signals(rows,selection['short'],config)
    combined = combined_signals(rows,selection['long'],selection['short'],config)
    sides = {'long':longs,'short':shorts,'combined':combined,
             'always_long':[1]*len(rows),'always_short':[-1]*len(rows),
             'edge_sign':[1 if (r.features.get('edge_direction') or 0)>0 else
                          -1 if (r.features.get('edge_direction') or 0)<0 else 0 for r in rows],
             'flat':[0]*len(rows)}
    sides = {key:[s if m else 0 for s,m in zip(signals,mask)] for key,signals in sides.items()}
    summaries, yearly, window_sensitivity = [], [], []
    for period,day_set in dates.items():
        indices = [i for i,r in enumerate(rows) if r.day in day_set]
        current = [rows[i] for i in indices]
        current_mask = [mask[i] for i in indices]
        for strategy,signals in sides.items():
            current_sides = [signals[i] for i in indices]
            for cost in config.scenarios:
                summaries.append({'period':period,'strategy':strategy,'cost_bps':cost,
                                  **strategy_metrics(current,current_sides,cost,current_mask)})
                for year in sorted({r.day.year for r in current}):
                    y_indices = [i for i,r in enumerate(current) if r.day.year==year]
                    yearly.append({'period':period,'year':year,'strategy':strategy,'cost_bps':cost,
                        **strategy_metrics([current[i] for i in y_indices],[current_sides[i] for i in y_indices],cost,
                                           [current_mask[i] for i in y_indices])})
            full_window_mask = [m and not r.short_window for r,m in zip(current,current_mask)]
            window_sensitivity.append({'period':period,'strategy':strategy,'cost_bps':config.cost_bps,
                **strategy_metrics(current,current_sides,config.cost_bps,full_window_mask)})
    intervals, statuses = [], {}
    test_indices = [i for i,r in enumerate(rows) if r.day in dates['test']]
    test_rows = [rows[i] for i in test_indices]
    for strategy in ('long','short','combined'):
        chosen = selection[strategy] is not None if strategy!='combined' else bool(selection['long'] or selection['short'])
        current_sides = [sides[strategy][i] for i in test_indices]
        current_intervals = bootstrap_intervals(test_rows if chosen else [],current_sides if chosen else [],config,
                          eligible_mask=[mask[i] for i in test_indices] if chosen else [])
        intervals.extend({'strategy':strategy,**i} for i in current_intervals)
        metrics = next(m for m in summaries if m['strategy']==strategy and m['period']=='test' and m['cost_bps']==config.cost_bps)
        statuses[strategy] = research_status(metrics,current_intervals,config,chosen)
    period_by_day = {day:key for key,days in dates.items() for day in days}
    decisions = []
    all_results = {(strategy,cost):daily_results(rows,signals,cost)
                   for strategy,signals in sides.items() for cost in config.scenarios}
    for i,row in enumerate(rows):
        conflict = bool(longs[i] and shorts[i] and mask[i])
        reason = 'Недостаточно пригодных признаков' if not mask[i] else 'Конфликт сигналов' if conflict else \
                 'Условие входа' if sides['combined'][i] else 'Нет сигнала'
        decisions.append({'date':row.day,'secid':row.secid,'expiry':row.expiry,'period':period_by_day[row.day],
            'available':mask[i],**row.features,'n_D':row.n_d,'n_R':row.n_r,'state':row.state,
            **{f'p_{name}':row.p_d[j] if row.p_d else None for j,name in enumerate(('up','down','flat_raw'))},
            **{f'p_{name}':row.p_r[j] if row.p_r else None for j,name in enumerate(('bull','bear','side_raw'))},
            'long_rule':selection['long'].id if selection['long'] else None,
            'short_rule':selection['short'].id if selection['short'] else None,
            'long_side':sides['long'][i],'short_side':sides['short'][i],'side':sides['combined'][i],
            'reason':reason,'r_oc':row.r_oc,'real_points':row.real_points,
            'gross_points':sides['combined'][i]*row.real_points if row.real_points is not None and mask[i] else None,
            'short_window':row.short_window,
            **{f'side_{strategy}':signals[i] for strategy,signals in sides.items()},
            **{f'result_{strategy}_{cost:g}bps':values[i] if mask[i] else None
               for (strategy,cost),values in all_results.items()}})
    audit = [{'period':period,'rows':sum(r.day in day_set for r in rows),
              'with_real':sum(r.day in day_set and r.real_points is not None for r in rows),
              'with_return':sum(r.day in day_set and r.r_oc is not None for r in rows),
              'with_forecast':sum(r.day in day_set and r.p_d is not None for r in rows),
              'eligible':sum(r.day in day_set and m for r,m in zip(rows,mask)),
              'short_windows':sum(r.day in day_set and r.short_window for r in rows),
              'low_sample':sum(r.day in day_set and r.n_d<config.min_history for r in rows)}
             for period,day_set in dates.items()]
    calendar = sorted({day for days in periods.values() for day in days})
    return {'series':dataset.series,'selection':selection,'conditional':conditional_analysis(rows,periods,config),
            'summaries':summaries,'yearly':yearly,'window_sensitivity':window_sensitivity,
            'intervals':intervals,'statuses':statuses,'decisions':decisions,'audit':audit,
            'status_counts':dict(Counter(s for r in rows for s in r.statuses)),
            'probabilities':probability_analysis(dataset,periods),
            'walk_forward':walk_forward(dataset,calendar,config)}


def run_cli(argv=None) -> int:
    """Выполняет исследование с аргументами argv; возвращает 0 при успехе или 2 при ошибке."""
    parser = argparse.ArgumentParser(description='Исследование Real MIX/RTS с независимым окончательным тестом.',add_help=False)
    parser.add_argument('-h','--help',action='help',help='Показать параметры и завершить работу')
    parser._optionals.title = 'Параметры'
    parser.add_argument('--mix',type=Path,default=PROJECT/'outputs/MIX_probabilities.xlsx',help='Исходная книга MIX')
    parser.add_argument('--rts',type=Path,default=PROJECT/'outputs/RTS_probabilities.xlsx',help='Исходная книга RTS')
    parser.add_argument('--output-dir',type=Path,default=PROJECT/'outputs/real_research',help='Папка CSV/JSON/PNG')
    parser.add_argument('--report',type=Path,default=DOCUMENTS/'Исследование_Real_результаты.md',help='Путь итогового Markdown')
    parser.add_argument('--cost-bps',type=float,default=3,help='Основные расходы на круг, б.п.; по умолчанию 3')
    parser.add_argument('--cost-scenarios-bps',type=float,nargs='+',default=[0,2,3,5,10],help='Сценарии расходов на круг')
    for name,default,description in (('min-history',200,'Минимум прошлых примеров в условной строке'),
        ('min-train-trades',60,'Минимум сделок обучения'),('min-validation-trades',30,'Минимум сделок выбора'),
        ('min-test-trades',30,'Минимум сделок окончательного теста'),('bootstrap-repeats',2000,'Число блочных повторов'),
        ('seed',42,'Начальное значение генератора случайных чисел')):
        parser.add_argument('--'+name,type=int,default=default,help=description)
    args = parser.parse_args(argv)
    try:
        config = ResearchConfig(args.cost_bps,tuple(args.cost_scenarios_bps),args.min_history,args.min_train_trades,
                                args.min_validation_trades,args.min_test_trades,args.bootstrap_repeats,args.seed)
        inputs = {args.mix.resolve(),args.rts.resolve()}
        if len(inputs)!=2 or args.report.resolve() in inputs or args.output_dir.resolve() in inputs:
            raise ValueError('Входы должны быть разными; выходные пути не могут совпадать с исходными книгами.')
        if args.report.suffix.lower()!='.md' or (args.output_dir.exists() and not args.output_dir.is_dir()):
            raise ValueError('Отчёт должен иметь расширение .md, а каталог результатов должен быть папкой.')
        datasets = [load_dataset(args.mix,'MIX'),load_dataset(args.rts,'RTS')]
        periods = split_calendar(datasets)
        results = []
        for dataset in datasets:
            print(f'{dataset.series}: {len(dataset.records)} строк; анализ и отбор правил...')
            results.append(analyze_dataset(dataset,periods,config))
        from source.real_reports import write_outputs
        write_outputs(results,datasets,periods,config,args.output_dir.resolve(),args.report.resolve())
        print(f'Основные расходы: {config.cost_bps:g} б.п. на круг.')
        for result in results:
            print(f'{result["series"]}: {result["statuses"]["combined"]}.')
        print(f'Отчёт: {args.report.resolve()}')
        print(f'Артефакты: {args.output_dir.resolve()}')
        return 0
    except (OSError,ValueError,OverflowError) as error:
        print(f'Ошибка исследования: {error}')
        return 2


if __name__=='__main__':
    raise SystemExit(run_cli())
