"""Экспорт исследования Real в CSV, JSON, PNG и русский документ Markdown.

Логика: сохраняет результаты с единицами, все кандидаты на разработке,
фиксированные правила на тесте и журнал решений. CSV содержит BOM для Excel,
NA остаётся пустым. Постоянные имена перезаписываются; исходные книги защищены.
Графики создаёт Matplotlib; подписи размеров групп разнесены для читаемости.
Модуль пакета source используется корневым скриптом research_real.py.

Примеры из папки проекта:
    python research_real.py --cost-bps 3
    python research_real.py --output-dir outputs/real_research --report C:/reports/research.md
"""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .real_data import FEATURE_NAMES, PERIOD_NAMES, VERSION, file_hash
from .real_rules import rule_label
from .real_stats import interval_label

STRATEGIES = {'long':'Выбранная покупка','short':'Выбранная продажа','combined':'Совместные правила',
              'always_long':'Ежедневная покупка','always_short':'Ежедневная продажа',
              'edge_sign':'Знак перевеса','flat':'Без сделок'}
MODELS = {'model':'Модель','unconditional':'Общие частоты','majority':'Историческое большинство','persistence':'Сохранение режима'}
HEADERS = {'period':'Участок','strategy':'Стратегия','cost_bps':'Расходы на круг, б.п.',
           'signals':'Число сигналов','trades':'Оценённые сделки','missing_results':'Сигналы без результата',
           'eligible_days':'Доступные дни','known_days':'Дни с результатом','coverage':'Покрытие, доля',
           'mean':'Средний результат, доля','median':'Медиана, доля','win_rate':'Прибыльные, доля',
           'mean_win':'Средний выигрыш, доля','mean_loss':'Средний проигрыш, доля',
           'total':'Сумма результатов, доля','per_day':'На доступный день, доля',
           'max_drawdown':'Просадка суммы результатов, доля','profit_factor':'Коэффициент прибыли',
           'gross_points_total':'Грубый итог, пункты','year':'Год','date':'Дата','secid':'SECID',
           'expiry':'LSTTRADE','available':'Признаки доступны до открытия','state':'Наблюдаемый режим',
           'side':'Общее направление','long_side':'Сигнал покупки','short_side':'Сигнал продажи',
           'reason':'Причина решения','r_oc':'Результат OPEN → CLOSE, доля','real_points':'Real, пункты',
           'gross_points':'Грубый результат правил, пункты','long_rule':'Правило покупки','short_rule':'Правило продажи',
           'short_window':'Укороченное прошлое окно','feature':'Признак','target':'Цель','n':'Число наблюдений',
           'pearson':'Корреляция Пирсона','spearman':'Корреляция Спирмена','group':'Группа','unit':'Единица',
           'small_group':'Малая группа','model':'Прогноз','brier':'Brier 0–2','logloss':'LogLoss',
           'accuracy':'Точность, доля','ties':'Нет единственного лидера','confusion':'Матрица ошибок',
           'class':'Класс','bin':'Номер группы','lower':'Нижняя граница, доля','upper':'Верхняя граница, доля',
           'predicted':'Средний прогноз, доля','observed':'Фактическая частота, доля',
           'block':'Длина блока, дни','effective_block':'Фактическая длина блока, дни',
           'mean_low':'Среднее на сделку: нижняя 95%, доля','mean_high':'Среднее на сделку: верхняя 95%, доля',
           'per_day_low':'На день: нижняя 95%, доля','per_day_high':'На день: верхняя 95%, доля',
           'delta_long_low':'Разница с покупкой на день: нижняя 95%, доля',
           'delta_long_high':'Разница с покупкой на день: верхняя 95%, доля',
           'adequate_blocks':'Достаточно календарных блоков','valid_repeats':'Повторы с оценкой',
           'repeats':'Число повторов','rows':'Всего строк','with_real':'Строк с Real',
           'with_return':'Строк с доходностью','with_forecast':'Строк с прогнозом движения',
           'eligible':'Пригодны для сравнения','short_windows':'Строк с короткими окнами','low_sample':'Малая история',
           'forecasts':'Прогнозов','evaluated':'Оценено на общих датах','excluded':'Не оценено',
           'fold':'Проверочный блок','train_end':'Конец обучения','selection_end':'Конец выбора',
           'start':'Начало проверки','end':'Конец проверки','trials':'Число кандидатов',
           'long':'Покупка','short':'Продажа','result':'Результат, доля',
           'rule_id':'Идентификатор правила','label':'Условие','side_rule':'Направление правила',
           'admitted':'Прошло условия выбора','status':'Итог проверки', 'threshold':'Граница',
           **{key:label+', доля' for key,label in FEATURE_NAMES.items()}}


def serializable(value):
    """Возвращает JSON-совместимое представление value, сохраняя None и обозначая бесконечность."""
    if is_dataclass(value):
        return serializable(asdict(value))
    if isinstance(value,dict):
        return {str(k):serializable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [serializable(v) for v in value]
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    if isinstance(value,Path):
        return str(value)
    if isinstance(value,float) and not math.isfinite(value):
        return '∞' if value>0 else '-∞' if value<0 else None
    return value


def column_label(key: str) -> str:
    """Возвращает русскую подпись CSV-колонки key, включая динамические расходы и сигналы."""
    if key.startswith('result_'):
        strategy,cost = key[7:].rsplit('_',1)
        return f'{STRATEGIES[strategy]}: результат при {cost[:-3]} б.п., доля'
    if key.startswith('side_') and key[5:] in STRATEGIES:
        return STRATEGIES[key[5:]]+': направление'
    if key=='z':
        return 'Нормированное движение режима, безразмерное'
    if key in FEATURE_NAMES:
        return HEADERS[key]
    if key.startswith('p_'):
        labels={'up':'Рост','down':'Падение','flat_raw':'Флэт исходная','bull':'Бычий','bear':'Медвежий','side_raw':'Боковой исходная'}
        return labels.get(key[2:],key)+', доля'
    if key.startswith(('train_','validation_','stress_')):
        prefix,metric=key.split('_',1)
        return {'train':'Обучение','validation':'Выбор','stress':'Стресс на выборе'}[prefix]+': '+HEADERS.get(metric,metric)
    return HEADERS.get(key,key)


def csv_value(value, key: str, unit=None):
    """Возвращает значение value колонки key для CSV, переводя подписи и сохраняя числовые единицы unit."""
    if key=='period':
        return PERIOD_NAMES.get(value,value)
    if key=='strategy':
        return STRATEGIES.get(value,value)
    if key=='feature':
        return FEATURE_NAMES.get(value,{'state':'Наблюдаемый режим','edge_state':'Перевес × режим','edge_flat':'Перевес × Флэт'}.get(value,value))
    if key=='model':
        return MODELS.get(value,value)
    if key=='target':
        return {'movement':'Движение','regime':'Режим','real_points':'Real, пункты','r_oc':'Результат OPEN → CLOSE, доля',
                'sign':'Знак результата','absolute':'Абсолютный результат, доля'}.get(value,value)
    if key=='unit':
        return {'points':'Пункты','return':'Доля'}.get(value,value)
    if isinstance(value,(dict,list,tuple)) or is_dataclass(value):
        return json.dumps(serializable(value),ensure_ascii=False,allow_nan=False)
    return serializable(value)


def write_csv(path: Path, rows: list[dict]) -> None:
    """Перезаписывает CSV path строками rows в UTF-8 с BOM; неизвестные числа остаются пустыми."""
    keys = list(dict.fromkeys(k for row in rows for k in row))
    mixed_units = any('unit' in row for row in rows)
    unit_labels = {'mean':'Средний результат','median':'Медиана',
                   'mean_win':'Средний выигрыш','mean_loss':'Средний проигрыш'}
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow([unit_labels[k]+', единица строки' if mixed_units and k in unit_labels
                         else column_label(k) for k in keys])
        for row in rows:
            writer.writerow([csv_value(row.get(k),k,row.get('unit')) for k in keys])


def number(value, factor=1, digits=3) -> str:
    """Форматирует value с масштабом factor и digits знаками; None выводит как NA."""
    if value is None:
        return 'NA'
    return f'{value*factor:.{digits}f}' if math.isfinite(value) else '∞'


def plot_outputs(result: dict, folder: Path, config) -> None:
    """Создаёт PNG result в folder с расходами config и разнесёнными подписями размеров групп."""
    os.environ.setdefault('MPLCONFIGDIR',str(folder/'.matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
                         'axes.spines.right':False,'figure.facecolor':'white'})
    fig,axes=plt.subplots(1,2,figsize=(12,4.6),layout='constrained')
    cuts=result['conditional']['cuts']['edge_direction']
    labels=[interval_label(i,cuts) for i in range(len(cuts)+1)]
    for ax,period in zip(axes,('train','validation')):
        groups={g['group']:g for g in result['conditional']['groups']
                if g['period']==period and g['feature']=='edge_direction' and g['unit']=='return'}
        means=[groups.get(label,{}).get('mean') for label in labels]
        ax.bar(range(len(labels)),[np.nan if v is None else v*100 for v in means],color='#3978a8')
        for i,label in enumerate(labels):
            if means[i] is not None:
                ax.annotate(f'n={groups[label]["n"]}',(i,means[i]*100),xytext=(0,6 if means[i]>=0 else -14),
                            textcoords='offset points',ha='center',fontsize=9)
        ax.axhline(0,color='#555555',linewidth=0.8)
        ax.set_xticks(range(len(labels)),[str(i+1) for i in range(len(labels))])
        ax.set_xlabel('Группа перевеса направления по квантилям обучения')
        ax.set_ylabel('Среднее движение OPEN → CLOSE, %')
        ax.set_title(PERIOD_NAMES[period])
        ax.margins(y=0.16)
    fig.suptitle(result['series']+': условное движение по силе перевеса')
    fig.savefig(folder/'conditional.png',dpi=160)
    plt.close(fig)
    fig,ax=plt.subplots(figsize=(11,5.2),layout='constrained')
    test=[d for d in result['decisions'] if d['period']=='test']
    for strategy in ('combined','long','short','always_long','always_short','edge_sign','flat'):
        if strategy in ('long','short') and result['selection'][strategy] is None:
            continue
        total,curve=0.0,[]
        for day in test:
            value=day[f'result_{strategy}_{config.cost_bps:g}bps']
            if value is not None:
                total += value*100
            curve.append(total if value is not None else np.nan)
        ax.plot([d['date'] for d in test],curve,label=STRATEGIES[strategy],linewidth=2 if strategy=='combined' else 1.1)
    ax.axhline(0,color='#555555',linewidth=0.7)
    ax.set_title(f'{result["series"]}: окончательный тест, расходы {config.cost_bps:g} б.п. на круг')
    ax.set_ylabel('Сумма условных дневных результатов, п.п.')
    ax.set_xlabel('Дата; единичная условная экспозиция, без модели капитала')
    ax.legend(loc='best',fontsize=8)
    ax.grid(alpha=0.2)
    fig.savefig(folder/'curves.png',dpi=160)
    plt.close(fig)
    for target,title in (('movement','Движение'),('regime','Режим')):
        fig,ax=plt.subplots(figsize=(6.5,5.4),layout='constrained')
        ax.plot([0,1],[0,1],color='#999999',linestyle='--',label='Идеальная калибровка')
        rows=[c for c in result['probabilities']['calibration'] if c['period']=='test' and c['target']==target and c['n']]
        for class_index,label in enumerate(dict.fromkeys(c['class'] for c in rows)):
            members=[c for c in rows if c['class']==label]
            line,=ax.plot([c['predicted'] for c in members],[c['observed'] for c in members],marker='o',label=label)
            for member_index,c in enumerate(members):
                dx,dy,align=((-7,-14,'right'),(7,8,'left'),(7,18,'left'))[class_index%3]
                if member_index%2:
                    dy=(-28,22,-14)[class_index%3]
                if c['observed']<0.08 and dy<0:
                    dy=30
                if c['predicted']<0.08 and dx<0:
                    dx,align=7,'left'
                if c['observed']>0.97 and dy>0:
                    dy=-dy
                ax.annotate(str(c['n']),(c['predicted'],c['observed']),xytext=(dx,dy),
                            textcoords='offset points',ha=align,fontsize=8,color=line.get_color(),
                            arrowprops={'arrowstyle':'-','color':line.get_color(),'lw':0.5,'alpha':0.6},
                            bbox={'facecolor':'white','alpha':0.75,'edgecolor':'none','pad':0.6})
        ax.set(xlim=(0,1),ylim=(0,1),xlabel='Средняя прогнозная вероятность',ylabel='Фактическая частота',
               title=f'{result["series"]}: {title.lower()}, калибровка на тесте')
        ax.legend(fontsize=9)
        ax.grid(alpha=0.2)
        fig.savefig(folder/f'calibration_{target}.png',dpi=160)
        plt.close(fig)


def markdown_report(results, datasets, periods, config, output_dir: Path) -> str:
    """Возвращает русский документ по results/datasets/periods/config со ссылками в output_dir."""
    lines=['# Исследование Real MIX и RTS','',
           f'Основные расходы: **{config.cost_bps:g} б.п. на круг**. Сценарии: '+', '.join(f'{c:g}' for c in config.scenarios)+' б.п.',
           '', 'Результат относится к условному исполнению по OPEN и CLOSE полного дневного бара Day. '
           'Проценты ниже относятся к изменению цены и сумме условных результатов при единичной экспозиции. '
           'Доходность капитала, плечо и гарантийное обеспечение не моделируются.', '', '## Разделение истории','',
           '| Участок | Начало | Конец | Общих дат |','|---|---|---|---:|']
    for name,dates in periods.items():
        lines.append(f'| {PERIOD_NAMES[name]} | {dates[0]} | {dates[-1]} | {len(dates)} |')
    lines += ['', 'Все пороги получены из обучения. Правила отобраны на участке выбора и зафиксированы до теста. '
           'Отдельная последовательная проверка использует четыре следующих блока только в первых 80% истории. '
           'Расходы и календарь сравнения одинаковы у всех стратегий; нужный режим проверяется внутри отдельного правила.', '']
    if config.bootstrap_repeats<1000:
        lines += ['Число блочных повторов меньше 1000: интервалы служат диагностикой и не обосновывают статистическое заключение.', '']
    for result,dataset in zip(results,datasets):
        series=result['series']
        folder=output_dir/series
        lines += [f'## {series}','',f'Рассмотрено кандидатов: **{result["selection"]["trials"]}**. '
                  f'Совместный результат: **{result["statuses"]["combined"]}**.', '',
                  f'- {rule_label(result["selection"]["long"]).rstrip(".")}.',
                  f'- {rule_label(result["selection"]["short"]).rstrip(".")}.', '',
                  f'Для входа также нужны условный прогноз и не менее {config.min_history} прошлых примеров. '
                  'При режимном условии проверяется и история режима. При конфликте направлений день пропускается.', '',
                  '### Окончательный тест','',
                  '| Стратегия | Сделок | Среднее, б.п. | Прибыльных, % | Сумма, п.п. | Просадка, п.п. |',
                  '|---|---:|---:|---:|---:|---:|']
        primary=[m for m in result['summaries'] if m['period']=='test' and m['cost_bps']==config.cost_bps]
        for m in primary:
            lines.append(f'| {STRATEGIES[m["strategy"]]} | {m["trades"]} | {number(m["mean"],10000)} | '
                         f'{number(m["win_rate"],100,1)} | {number(m["total"],100)} | {number(m["max_drawdown"],100)} |')
        lines += ['', '### Неопределённость результата','',
                  '95% интервалы среднего на сделку; повторно выбираются последовательные блоки дней. '
                  'Разница с ежедневной покупкой оценивается на доступный день, включая дни вне рынка.', '',
                  '| Стратегия | Блок, дней | Среднее на сделку: 95%, б.п. | Разница с покупкой на день: 95%, б.п. |',
                  '|---|---:|---|---|']
        for row in result['intervals']:
            if result['statuses'][row['strategy']]=='Правило не отобрано':
                continue
            lines.append(f'| {STRATEGIES[row["strategy"]]} | {row["block"]} | '
                         f'{number(row["mean_low"],10000)} … {number(row["mean_high"],10000)} | '
                         f'{number(row["delta_long_low"],10000)} … {number(row["delta_long_high"],10000)} |')
        for strategy in ('long','short','combined'):
            lines += ['', f'{STRATEGIES[strategy]}: **{result["statuses"][strategy]}**.']
        lines += ['', '### Качество вероятностей на тесте','',
                  '| Цель | Прогноз | Дней | Brier 0–2 | LogLoss | Точность, % |',
                  '|---|---|---:|---:|---:|---:|']
        for m in result['probabilities']['metrics']:
            if m['period']=='test':
                lines.append(f'| {"Движение" if m["target"]=="movement" else "Режим"} | {MODELS[m["model"]]} | '
                             f'{m["n"]} | {number(m["brier"],digits=5)} | {number(m["logloss"],digits=5)} | {number(m["accuracy"],100,1)} |')
        lines += ['', 'Метрики вероятностей используют свои общие пригодные даты и не ограничены торговым фильтром. '
                  'Числа на графиках калибровки показывают размеры групп. Высокая точность режима требует сравнения с сохранением прежнего состояния.', '',
                  '### Покрытие данных','', '| Участок | Строк | С Real | С доходностью | С прогнозом | Пригодны для сравнения |',
                  '|---|---:|---:|---:|---:|---:|']
        for a in result['audit']:
            lines.append(f'| {PERIOD_NAMES[a["period"]]} | {a["rows"]} | {a["with_real"]} | {a["with_return"]} | {a["with_forecast"]} | {a["eligible"]} |')
        missing=sum(d['available'] and d['side']!=0 and d['r_oc'] is None for d in result['decisions'])
        lines += ['', f'Сигналов совместного правила без результата: {missing}. '
                  'Начальная строка может иметь Real без логарифмической доходности; она сохраняется в аудите и не участвует в торговом тесте.', '',
                  '### Последовательная проверка','',
                  '| Блок | Период проверки | Сделок | Среднее, б.п. | Сумма, п.п. |',
                  '|---|---|---:|---:|---:|']
        for f in result['walk_forward']['folds']:
            lines.append(f'| {f["fold"]} | {f["start"]} — {f["end"]} | {f["trades"]} | {number(f["mean"],10000)} | {number(f["total"],100)} |')
        lines += ['', f'![Кривые окончательного теста]({(folder/"curves.png").as_posix()})','',
                  f'![Условные результаты]({(folder/"conditional.png").as_posix()})','',
                  f'![Калибровка движения]({(folder/"calibration_movement.png").as_posix()})','',
                  f'![Калибровка режима]({(folder/"calibration_regime.png").as_posix()})','',
                  '### Файлы для проверки','']
        for name,label in [('decisions.csv','Журнал всех решений'),('candidates.csv','Все проверенные кандидаты'),
            ('conditional.csv','Условные результаты'),('correlations.csv','Корреляции обучения'),
            ('performance.csv','Сценарии расходов'),('yearly.csv','Результаты по годам'),
            ('window_sensitivity.csv','Проверка полных окон'),('probabilities.csv','Вероятностные метрики'),
            ('calibration.csv','Калибровка'),('bootstrap.csv','Блочные интервалы'),
            ('walk_forward.csv','Последовательная проверка'),('coverage.csv','Аудит покрытия')]:
            lines.append(f'- [{label}]({(folder/name).as_posix()})')
        lines += ['', f'Источник: [{series}]({dataset.path.as_posix() if dataset.path else ""}). SHA-256: `{dataset.sha256}`.', '']
    lines += ['## Ограничения и дальнейшее использование','',
        '- Прогнозы реконструированы по текущему снимку БД; первоначальные времена публикации и поздние исправления не восстановлены.',
        '- Исполнение по OPEN/CLOSE условное. Точные расходы и исполнимые цены требуют отдельной проверки.',
        '- Поиск ограничен и раскрыт, но неоднократное изменение исследования после просмотра теста создаёт риск подгонки.',
        '- Положительный результат и блочные интервалы относятся к историческому исследованию; перед использованием нужны новые заранее сохранённые прогнозы.',
        '- Если правило не прошло тест, его условия не следует менять по этому тесту. Расширения оцениваются как новое исследование.', '',
        f'Версия: {VERSION}. Seed: {config.seed}. Блочных повторов: {config.bootstrap_repeats}.', '']
    return '\n'.join(lines)


def write_outputs(results, datasets, periods, config, output_dir: Path, report_path: Path) -> None:
    """Перезаписывает артефакты results в output_dir и report_path, проверяя исходные datasets и periods/config."""
    for dataset in datasets:
        if dataset.path and file_hash(dataset.path)!=dataset.sha256:
            raise ValueError('Исходная книга изменилась во время исследования.')
    output_dir.mkdir(parents=True,exist_ok=True)
    report_path.parent.mkdir(parents=True,exist_ok=True)
    for result in results:
        folder=output_dir/result['series']
        folder.mkdir(parents=True,exist_ok=True)
        candidates=[]
        for entry in result['selection']['candidates']:
            candidates.append({'rule_id':entry['rule'].id,'side_rule':entry['rule'].side,
                               'label':entry['label'],'admitted':entry['admitted'],
                               **{f'{p}_{k}':v for p in ('train','validation','stress') for k,v in entry[p].items()}})
        selected=[{'strategy':key,'rule_id':result['selection'][key].id if result['selection'][key] else None,
                   'label':rule_label(result['selection'][key]),'status':result['statuses'][key]} for key in ('long','short')]
        boundaries=[{'feature':f,'threshold':v} for f,cuts in result['conditional']['cuts'].items() for v in cuts]
        tables={'coverage':result['audit'],'group_boundaries':boundaries,'correlations':result['conditional']['correlations'],
                'conditional':result['conditional']['groups'],'candidates':candidates,'selected_rules':selected,
                'performance':result['summaries'],'yearly':result['yearly'],'window_sensitivity':result['window_sensitivity'],
                'bootstrap':result['intervals'],'probabilities':result['probabilities']['metrics'],
                'probability_coverage':result['probabilities']['coverage'],'calibration':result['probabilities']['calibration'],
                'walk_forward':result['walk_forward']['folds'],'walk_forward_decisions':result['walk_forward']['decisions'],
                'decisions':result['decisions']}
        for name,rows in tables.items():
            write_csv(folder/(name+'.csv'),rows)
        plot_outputs(result,folder,config)
    summary={'version':VERSION,'created_at':datetime.now(timezone(timedelta(hours=3))).isoformat(timespec='seconds'),
             'config':asdict(config),'scenarios':config.scenarios,'periods':periods,
             'sources':[{'series':d.series,'path':d.path,'sha256':d.sha256,'metadata':d.metadata} for d in datasets],
             'results':results,'units':{'returns':'доля изменения цены','total':'сумма условных результатов',
                                      'cost_bps':'расходы на полный вход и выход'}}
    (output_dir/'summary.json').write_text(json.dumps(serializable(summary),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    report_path.write_text(markdown_report(results,datasets,periods,config,output_dir),encoding='utf-8')
