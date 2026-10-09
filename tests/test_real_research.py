"""Проверки исследования Real без изменения рабочих котировок и книг.

Логика: искусственные XLSX и последовательности проверяют чтение, качество,
отсутствие будущих данных, расходы на круг, правила и отчёты. Временные файлы
создаются только внутри tests и удаляются после каждого теста.
Расчётные и ресурсные модули импортируются из пакета source.

Запуск из папки проекта:
    python -m unittest discover -s tests -p test_real_research.py -v
    python -m unittest discover -s tests -v
"""

import copy
import math
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


TEST_ROOT = Path(__file__).resolve().parent


def workbook_fixture(path, series='MIX', count=90, windows=60):
    """Создаёт книгу path серии series с count барами и максимумом windows; результата нет."""
    from source.funds_model import Config, Quote, build_forecasts
    from source.funds_xlsx import write_workbook
    quotes = []
    previous = 1000.0
    for i in range(count):
        opening = previous * math.exp(0.002 * math.cos(i))
        closing = opening * math.exp(0.012 * math.sin(i * 0.7) + 0.001)
        quotes.append(Quote(date(2020, 1, 1) + timedelta(days=i), 'X',
                            date(2030, 1, 1), opening, closing, 100, False))
        previous = closing
    write_workbook(path, build_forecasts(quotes,Config(regime_window=windows,movement_window=windows)),
                   {'Серия': series, 'L': 20, 'alpha': 0.5, 'W_max':windows,'V_max':windows})


def record(index=0, r=0.01, edge=0.1, state='Боковой', flat=0.2):
    """Возвращает Record дня index с результатом r, перевесом edge, режимом state и flat."""
    from source.real_data import Record
    return Record(day=date(2020, 1, 1) + timedelta(days=index), secid='X',
                  expiry=date(2030, 1, 1),
                  features={'edge_direction': edge, 'p_flat': flat,
                            'edge_regime': 0.1, 'p_side': 0.7,
                            'sigma_oc': 0.01, 'g': edge, 'z': edge * 2},
                  p_d=((1-flat+edge)/2, (1-flat-edge)/2, flat),
                  p_r=(0.2, 0.1, 0.7), state=state, n_d=500, n_r=500,
                  source_d='CONDITIONAL_STATE', source_r='CONDITIONAL_STATE',
                  r_oc=r, real_points=None if r is None else r*1000,
                  actual_d=None if r is None else (0 if r>0.002 else 1 if r<-0.002 else 2),
                  actual_r=2, matrix_d=((3, 2, 1), (1, 2, 3), (2, 1, 3)),
                  matrix_r=((5, 0, 1), (0, 5, 1), (1, 1, 4)))


class DataTest(unittest.TestCase):
    """Проверяет ввод данных, причинные фильтры и календарь исследования."""

    def test_config_defaults_and_invalid_cost(self):
        """Основные расходы равны 3 б.п.; отрицательные расходы отклоняются."""
        from source.real_data import ResearchConfig
        config = ResearchConfig()
        self.assertEqual(config.cost_bps, 3)
        self.assertEqual(config.scenarios, (0, 2, 3, 5, 10))
        with self.assertRaises(ValueError):
            ResearchConfig(cost_bps=-1)
        with self.assertRaises(ValueError):
            ResearchConfig(seed=-1)

    def test_load_and_missing_forecast(self):
        """Чтение сохраняет первый факт без вероятностей и правильную доходность."""
        from source.real_data import load_dataset
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            path = Path(folder)/'MIX.xlsx'
            workbook_fixture(path)
            dataset = load_dataset(path, 'MIX')
            self.assertEqual(len(dataset.records), 90)
            self.assertIsNone(dataset.records[0].p_d)
            self.assertIsNotNone(dataset.records[0].real_points)
            self.assertAlmostEqual(dataset.records[-1].r_oc,
                                   math.expm1(0.012*math.sin(89*0.7)+0.001), 12)
            self.assertEqual(len(dataset.sha256), 64)

    def test_invalid_probability_and_duplicate_key(self):
        """Частично пустая тройка, неверная сумма и повтор ключа вызывают ошибку."""
        from openpyxl import load_workbook
        from source.real_data import load_dataset
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            for defect in ('partial', 'sum', 'duplicate', 'mismatch', 'header', 'sign',
                           'matrix_missing','matrix_count','negative_window','string_window'):
                path = Path(folder)/(defect+'.xlsx')
                workbook_fixture(path)
                book = load_workbook(path)
                if defect == 'partial':
                    book['Диагностика']['D90'] = None
                elif defect == 'sum':
                    book['Диагностика']['D90'] = 0.99
                elif defect == 'duplicate':
                    book['Прогноз'].append([c.value for c in book['Прогноз'][90]])
                elif defect == 'mismatch':
                    book['Диагностика']['B90'] = 'OTHER'
                elif defect=='sign':
                    book['Прогноз']['J90'] = -book['Прогноз']['J90'].value
                elif defect=='matrix_missing':
                    book['Диагностика']['AG90'] = None
                elif defect=='matrix_count':
                    book['Диагностика']['Q90'] = 99999
                elif defect=='negative_window':
                    book['Диагностика']['AC90'] = -999
                elif defect=='string_window':
                    book['Диагностика']['AC90'] = 'wrong'
                else:
                    book['Прогноз']['J1'] = 'wrong'
                book.save(path)
                book.close()
                with self.subTest(defect=defect):
                    with self.assertRaises(ValueError, msg=defect):
                        load_dataset(path, 'MIX')

    def test_calendar_before_outcome_filter(self):
        """Общий календарь 10 дней разбивается 6/2/2 независимо от отсутствия факта."""
        from source.real_data import Dataset, split_calendar
        rows = [record(i, None if i==4 else 0.01) for i in range(10)]
        periods = split_calendar([Dataset('MIX', rows), Dataset('RTS', rows[::2])])
        self.assertEqual([len(periods[p]) for p in ('train','validation','test')], [6,2,2])
        self.assertEqual(periods['test'][0], rows[8].day)

    def test_signal_eligibility_does_not_use_outcome(self):
        """Пропуск факта и послесессионный статус не запрещают причинно доступный сигнал."""
        from source.real_data import ResearchConfig, eligible
        row = replace(record(), r_oc=None, real_points=None,
                      statuses=('NO_SESSION_TRADES',))
        self.assertTrue(eligible(row, ResearchConfig()))
        self.assertFalse(eligible(replace(row, n_d=199), ResearchConfig()))
        self.assertFalse(eligible(replace(row, n_r=199), ResearchConfig(), True))

    def test_custom_full_window(self):
        """Полнота окон определяется по W_max/V_max книги, а не по постоянному числу 60."""
        from source.real_data import load_dataset
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            short=Path(folder)/'short.xlsx'
            long=Path(folder)/'long.xlsx'
            workbook_fixture(short,windows=20)
            workbook_fixture(long,windows=120)
            self.assertFalse(load_dataset(short,'MIX').records[-1].short_window)
            self.assertTrue(load_dataset(long,'MIX').records[-1].short_window)


class StatisticsTest(unittest.TestCase):
    """Проверяет расходы, пропуски и описательные зависимости."""

    def test_cost_short_zero_and_missing(self):
        """Расходы списываются один раз на круг; шорт, пропуск и NA различаются."""
        from source.real_stats import daily_results, strategy_metrics
        rows = [record(i, r) for i,r in enumerate((0.01, 0.01, 0.01, None))]
        values = daily_results(rows, [1,-1,0,1], 3)
        self.assertAlmostEqual(values[0], 0.0097)
        self.assertAlmostEqual(values[1], -0.0103)
        self.assertEqual(values[2], 0)
        self.assertIsNone(values[3])
        metrics = strategy_metrics(rows,[1,-1,0,1],3)
        self.assertEqual(metrics['signals'],3)
        self.assertEqual(metrics['trades'],2)
        self.assertEqual(metrics['missing_results'],1)
        self.assertAlmostEqual(metrics['total'],-0.0006)
        self.assertAlmostEqual(metrics['max_drawdown'],0.0103)
        first_loss = strategy_metrics([record(r=-0.01)],[1],0)
        self.assertAlmostEqual(first_loss['max_drawdown'],0.01)

    def test_correlation_ties_and_constant(self):
        """Ранговая корреляция корректна при совпадениях и не определена для константы."""
        from source.real_stats import correlation, quantile_cuts
        self.assertAlmostEqual(correlation([1,1,3],[2,2,6],True),1)
        self.assertIsNone(correlation([1,1,1],[2,3,4]))
        self.assertEqual(quantile_cuts([1,1,1],(0.2,0.4,0.6,0.8)),(1.0,))

    def test_conditional_analysis_does_not_read_test_outcomes(self):
        """Условные таблицы и их границы не меняются после замены будущих фактов."""
        from source.real_data import Dataset, ResearchConfig, split_calendar
        from source.real_stats import conditional_analysis
        rows=[record(i,(-1 if i%2 else 1)*0.01,edge=i/100) for i in range(20)]
        periods=split_calendar([Dataset('MIX',rows)])
        before=conditional_analysis(rows,periods,ResearchConfig())
        altered=[replace(r,r_oc=0.9,real_points=999) if r.day in periods['test'] else r for r in rows]
        self.assertEqual(before,conditional_analysis(altered,periods,ResearchConfig()))


class RulesTest(unittest.TestCase):
    """Проверяет ограниченный перебор и раздельный отбор направлений без будущего."""

    def test_candidates_and_low_positive_edge_short(self):
        """Не более 102 правил; продажа допустима при низком положительном edge."""
        from source.real_data import ResearchConfig
        from source.real_rules import Rule, generate_rules, rule_signals
        rows=[record(i,edge=i/100,state=('Бычий','Медвежий','Боковой')[i%3]) for i in range(40)]
        self.assertLessEqual(len(generate_rules(rows,ResearchConfig())),102)
        rule=Rule('short',-1,'<=',0.1)
        self.assertEqual(rule_signals([record(edge=0.05),record(edge=0.2)],rule,ResearchConfig()),[-1,0])

    def test_conflict_and_missing_fact(self):
        """Конфликт покупка/продажа даёт пропуск; без факта сигнал сохраняется."""
        from source.real_data import ResearchConfig
        from source.real_rules import Rule, combined_signals, rule_signals
        long=Rule('long',1,'>=',0.05)
        short=Rule('short',-1,'<=',0.2)
        row=record(r=None,edge=0.1)
        self.assertEqual(rule_signals([row],long,ResearchConfig()),[1])
        self.assertEqual(combined_signals([row],long,short,ResearchConfig()),[0])

    def test_selection_without_profitable_rules(self):
        """Недостаток подходящих сделок возвращает отсутствие правил, а не случайного победителя."""
        from source.real_data import ResearchConfig
        from source.real_rules import select_rules
        config=ResearchConfig(min_train_trades=2,min_validation_trades=2)
        selected=select_rules([record(i,r=0) for i in range(10)],
                              [record(i+10,r=0) for i in range(5)],config)
        self.assertIsNone(selected['long'])
        self.assertIsNone(selected['short'])


class ValidationTest(unittest.TestCase):
    """Проверяет временную причинность, вероятностные оценки и блочную неопределённость."""

    def test_brier_and_common_probability_dates(self):
        """Идеальный Brier равен нулю, равномерный — 2/3; модели сравниваются на общих датах."""
        from source.real_data import Dataset, split_calendar
        from source.real_validation import score_probabilities, probability_analysis
        self.assertEqual(score_probabilities([(1,0,0)],[0])['brier'],0)
        self.assertAlmostEqual(score_probabilities([(1/3,)*3],[0])['brier'],2/3)
        rows=[record(i) for i in range(10)]
        rows[7]=replace(rows[7],actual_d=None)
        analysis=probability_analysis(Dataset('MIX',rows,{'alpha':0.5}),split_calendar([Dataset('MIX',rows)]))
        groups=[m for m in analysis['metrics'] if m['period']=='validation' and m['target']=='movement']
        self.assertEqual({m['n'] for m in groups},{1})

    def test_bootstrap_reproducible_and_missing_pairs(self):
        """Блочная проверка воспроизводима; пропуски фактов не становятся сделками с нулём."""
        from source.real_data import ResearchConfig
        from source.real_validation import bootstrap_intervals
        rows=[record(i,None if i%7==0 else 0.01) for i in range(80)]
        cfg=ResearchConfig(bootstrap_repeats=100)
        a=bootstrap_intervals(rows,[1]*80,cfg)
        self.assertEqual(a,bootstrap_intervals(rows,[1]*80,cfg))
        self.assertAlmostEqual(a[0]['mean_low'],0.0097)
        self.assertAlmostEqual(a[0]['mean_high'],0.0097)

    def test_walk_forward_future_does_not_change_earlier_decisions(self):
        """Изменение будущих результатов не меняет решения первого проверочного блока."""
        from source.real_data import Dataset, ResearchConfig
        from source.real_validation import walk_forward
        rows=[record(i,edge=(i%10)/100) for i in range(100)]
        cfg=ResearchConfig(min_train_trades=2,min_validation_trades=2,bootstrap_repeats=10)
        calendar=[r.day for r in rows]
        a=walk_forward(Dataset('MIX',rows),calendar,cfg)
        changed=[replace(r,r_oc=-0.9) if i>=50 else r for i,r in enumerate(rows)]
        b=walk_forward(Dataset('MIX',changed),calendar,cfg)
        self.assertEqual(a['folds'][0],b['folds'][0])
        self.assertEqual(a['decisions'][:10],b['decisions'][:10])

    def test_one_bootstrap_repeat_cannot_validate(self):
        """Один удачный повтор не может создать статус устойчивого положительного результата."""
        from source.real_data import ResearchConfig
        from source.real_stats import strategy_metrics
        from source.real_validation import bootstrap_intervals, research_status
        rows=[record(i,0.01 if i<119 else -0.6) for i in range(120)]
        cfg=ResearchConfig(bootstrap_repeats=1)
        intervals=bootstrap_intervals(rows,[1]*120,cfg)
        status=research_status(strategy_metrics(rows,[1]*120,3),intervals,cfg)
        self.assertNotIn('устойчив',status)

    def test_short_calendar_has_four_explicit_fold_statuses(self):
        """Малый календарь сохраняет четыре блока с объяснением отсутствия проверки."""
        from source.real_data import Dataset, ResearchConfig
        from source.real_validation import walk_forward
        rows=[record(i) for i in range(5)]
        result=walk_forward(Dataset('MIX',rows),[r.day for r in rows],ResearchConfig())
        self.assertEqual(len(result['folds']),4)
        self.assertTrue(all(f['status']=='Недостаточно истории' for f in result['folds']))


class IntegrationTest(unittest.TestCase):
    """Проверяет воспроизводимый полный запуск, артефакты и защиту исходных книг."""

    def test_full_report_and_default_cost(self):
        """Полный запуск пишет CSV/JSON/PNG/Markdown, сохраняет исходники и стоимость 3 б.п."""
        from research_real import run_cli
        from source.real_data import file_hash
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            root=Path(folder)
            mix,rts=root/'MIX.xlsx',root/'RTS.xlsx'
            workbook_fixture(mix,'MIX',120)
            workbook_fixture(rts,'RTS',120)
            before=(file_hash(mix),file_hash(rts))
            output=root/'result'
            report=root/'report.md'
            with redirect_stdout(io.StringIO()):
                code=run_cli(['--mix',str(mix),'--rts',str(rts),'--output-dir',str(output),
                              '--report',str(report),'--bootstrap-repeats','20'])
            self.assertEqual(code,0)
            summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['config']['cost_bps'],3)
            self.assertTrue(report.is_file())
            self.assertTrue((output/'MIX'/'decisions.csv').is_file())
            self.assertTrue((output/'RTS'/'curves.png').is_file())
            self.assertEqual(before,(file_hash(mix),file_hash(rts)))
            self.assertTrue((output/'MIX'/'decisions.csv').read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_cli_invalid_parameters_and_source_report(self):
        """Неверные параметры и попытка использовать исходную книгу как отчёт отклоняются."""
        from research_real import run_cli
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            root=Path(folder)
            mix,rts=root/'MIX.xlsx',root/'RTS.xlsx'
            workbook_fixture(mix,'MIX')
            workbook_fixture(rts,'RTS')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_cli(['--cost-bps','-1']),2)
                self.assertEqual(run_cli(['--mix',str(mix),'--rts',str(rts),
                                          '--report',str(mix),'--output-dir',str(root/'out')]),2)

    def test_pipeline_holdout_cannot_change_selection_or_signals(self):
        """Замена фактов теста меняет оценки, но не выбор правил и дневные решения."""
        from source.real_data import Dataset, ResearchConfig, split_calendar
        from research_real import analyze_dataset
        rows=[record(i,r=0.01 if i%10>=5 else -0.003,edge=(i%10)/100) for i in range(150)]
        cfg=ResearchConfig(min_train_trades=5,min_validation_trades=3,bootstrap_repeats=10)
        periods=split_calendar([Dataset('MIX',rows)])
        a=analyze_dataset(Dataset('MIX',rows,{'alpha':0.5}),periods,cfg)
        altered=[replace(r,r_oc=-0.9,real_points=-999,actual_d=1,actual_r=1)
                 if r.day in periods['test'] else r for r in rows]
        b=analyze_dataset(Dataset('MIX',altered,{'alpha':0.5}),periods,cfg)
        self.assertEqual(a['selection'],b['selection'])
        self.assertEqual([d['side'] for d in a['decisions']],[d['side'] for d in b['decisions']])
        self.assertEqual(a['conditional'],b['conditional'])

    def test_regime_rule_does_not_change_other_direction(self):
        """Режимный фильтр продажи не запрещает покупку, которая не использует режим."""
        from source.real_data import Dataset, ResearchConfig, split_calendar
        from source.real_rules import Rule
        from research_real import analyze_dataset
        rows=[replace(record(i,edge=0.1),n_r=0) for i in range(20)]
        selection={'long':Rule('L',1,'>=',0.05),'short':Rule('S',-1,state='Медвежий'),
                   'candidates':[],'trials':0}
        with patch('research_real.select_rules',return_value=selection):
            result=analyze_dataset(Dataset('MIX',rows,{'alpha':0.5}),
                                   split_calendar([Dataset('MIX',rows)]),ResearchConfig(bootstrap_repeats=10))
        self.assertTrue(all(d['long_side']==1 for d in result['decisions']))
        self.assertTrue(all(d['short_side']==0 for d in result['decisions']))

    def test_csv_mixed_units(self):
        """Статистика пунктов и долей имеет общий заголовок с явной единицей каждой строки."""
        from source.real_reports import write_csv
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            path=Path(folder)/'mixed.csv'
            write_csv(path,[{'unit':'points','mean':100},{'unit':'return','mean':0.01}])
            header=path.read_text(encoding='utf-8-sig').splitlines()[0]
            self.assertIn('Средний результат, единица строки',header)

    def test_independent_verifier(self):
        """Отдельная проверка обнаруживает подмену результата в журнале решений."""
        from verify_real_research import verify_summary
        from research_real import run_cli
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            root=Path(folder)
            mix,rts=root/'MIX.xlsx',root/'RTS.xlsx'
            workbook_fixture(mix,'MIX',80)
            workbook_fixture(rts,'RTS',80)
            output=root/'out'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_cli(['--mix',str(mix),'--rts',str(rts),'--output-dir',str(output),
                                          '--report',str(root/'report.md'),'--bootstrap-repeats','10']),0)
            self.assertEqual(verify_summary(output/'summary.json')['series'],2)
            data=json.loads((output/'summary.json').read_text(encoding='utf-8'))
            data['results'][0]['decisions'][-1]['result_combined_3bps']=99
            (output/'summary.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            with self.assertRaises(AssertionError):
                verify_summary(output/'summary.json')


if __name__ == '__main__':
    unittest.main()
