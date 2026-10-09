# Ресурсные модули

`source` — пакет Python с общими модулями проекта. Для запуска используются
`forecast_mix.py`, `forecast_rts.py` и `research_real.py` в корне проекта.
Модули внутри пакета импортируют друг друга относительными импортами;
точки запуска и тесты используют `source.<имя_модуля>`.

| Модуль | Назначение и логика | Параметры точки запуска |
|---|---|---|
| `funds_model.py` | Чтение SQLite только для чтения, объединение истории по LSTTRADE, причинные дневные вероятности, обработка CLI | `--db`, `--output`, `--start-date`, `--end-date`, `--forecast-date`, `--previous-session`, `--length`, `--regime-window`, `--movement-window`, `--k`, `--q`, `--alpha`, `--n-min`, `--overwrite` |
| `funds_xlsx.py` | Сохранение и оформление XLSX, округление процентов с сохранением суммы, защита от перезаписи | `--output`, `--overwrite` прогнозного скрипта |
| `real_data.py` | Чтение и проверка исходных XLSX, отделение признаков от фактов, разбиение общего календаря 60/20/20 | `--mix`, `--rts`, `--min-history` исследования |
| `real_stats.py` | Условные зависимости и торговые результаты, границы по обучению, расходы на полный круг | `--cost-bps`, `--cost-scenarios-bps` исследования |
| `real_rules.py` | Построение до 102 правил, независимый отбор покупки и продажи, пропуск конфликтов | `--min-train-trades`, `--min-validation-trades` исследования |
| `real_validation.py` | Последовательные проверки, метрики вероятностей и блочные интервалы | `--min-test-trades`, `--bootstrap-repeats`, `--seed` исследования |
| `real_reports.py` | Экспорт CSV/JSON/PNG и русского Markdown с постоянными именами | `--output-dir`, `--report` исследования |
| `regime_ranges.py` | Полный перебор 44 521 пар диапазонов с шагом 5%, симуляция по всему календарю, показатели отдельно по сторонам и годам | `--commission-bps` скрипта `research_real_regime_ranges.py` |
| `regime_ranges_report.py` | Автономный HTML с непрерывными SVG-кривыми, годовым столбчатым PnL совокупности/покупок/продаж после комиссии, полными/годовыми показателями, журналом, JSON и выгрузкой CSV из браузера | `--output` скрипта `research_real_regime_ranges.py` |
| `__init__.py` | Объявление пакета без запуска расчётов при импорте | Параметров нет |

Примеры выполняются из корня `C:/Users/Alkor/VSCode/pj24_funds_2`:

```powershell
.\.venv\Scripts\python.exe forecast_mix.py --overwrite
.\.venv\Scripts\python.exe forecast_rts.py --output outputs/RTS_check.xlsx
.\.venv\Scripts\python.exe research_real.py --cost-bps 3 --bootstrap-repeats 2000 --seed 42
.\.venv\Scripts\python.exe research_real.py --mix outputs/MIX_probabilities.xlsx --rts outputs/RTS_probabilities.xlsx --output-dir outputs/real_research
.\.venv\Scripts\python.exe forecast_mix.py --help
.\.venv\Scripts\python.exe research_real.py --help
.\.venv\Scripts\python.exe research_real_regime_ranges.py --only RTS --commission-bps 3
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

По умолчанию прогнозные XLSX и артефакты исследования находятся в `outputs`
корня проекта независимо от рабочей папки запуска. Явно заданные относительные
пути CLI по-прежнему отсчитываются от текущей рабочей папки. Зависимости,
значения параметров по умолчанию и полная методика описаны в корневом README.
