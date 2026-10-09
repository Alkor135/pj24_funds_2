r"""Количество и суммы положительных/отрицательных Real по Рост и Падение.

Логика: читает колонки Рост, Падение и Real выбранного листа XLSX. Вероятности
хранятся как доли от 0 до 1 и разбиваются на десять фиксированных интервалов:
[0%; 10%), [10%; 20%), …, [90%; 100%]. Создаёт df_growth и df_decline;
каждый содержит группу, два количества, две суммы Real в исходных пунктах
и колонку «разница». В df_growth сначала идут положительные показатели,
в df_decline — отрицательные. Колонка «разница» в обоих DF содержит сумму
двух колонок сумм: Сумма Real > 0 + Сумма Real < 0 с сохранением знаков.
Отрицательные суммы сохраняют минус. Нулевые и пустые Real не учитываются;
пустая вероятность исключает строку только из соответствующей таблицы.
Все десять групп выводятся, даже если в них нет наблюдений. Анализируется
вся таблица без фильтров истории, расходов и разбиения на временные участки.
Результаты печатаются в консоль; исходная книга не изменяется.
Общие функции принимают probability_columns — пару имён вероятностей;
по умолчанию Рост/Падение. Их также использует research_real_regime_groups.py
для анализа Бычий/Медвежий с теми же правилами группировки и расчёта.

По умолчанию: outputs/MIX_probabilities.xlsx рядом со скриптом, лист Прогноз.
Явно заданный относительный путь отсчитывается от текущей рабочей папки.
Зависимости: pandas, numpy и openpyxl из requirements.txt.

Примеры из папки проекта:
    .venv\Scripts\python.exe research_real_groups.py
    python research_real_groups.py --input outputs/RTS_probabilities.xlsx
    python research_real_groups.py --input C:/data/table.xlsx --sheet Прогноз
    python research_real_groups.py --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent
DEFAULT_PROBABILITY_COLUMNS = ("Рост", "Падение")


def analyze_real_groups(
    data: pd.DataFrame,
    *,
    probability_columns: tuple[str, str] = DEFAULT_PROBABILITY_COLUMNS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Возвращает два DF с количествами, суммами и разницей по 10 группам.

    data должен содержать вероятности probability_columns в долях 0–1 и Real
    в пунктах. probability_columns задаёт пару колонок (по умолчанию Рост
    и Падение). DF возвращаются в порядке этой пары: по умолчанию это
    df_growth/df_decline.
    Пропуски разрешены; нули Real не относятся ни к одному знаку. Границы
    10%, …, 90% входят в следующий интервал, 100% — в последний. Пустые группы
    получают нулевые количества, суммы и разницу. В первом DF положительные
    количество/сумма стоят перед отрицательными, во втором — наоборот.
    Последняя колонка «разница» равна первой сумме плюс второй сумме
    с сохранением их знаков. Исходный DF не изменяется; неверные
    колонки, нечисловые/бесконечные значения и доли вне 0–1 вызывают ValueError.
    """
    required_columns = (*probability_columns, "Real")
    missing = [column for column in required_columns if column not in data.columns]
    if missing:
        raise ValueError("Отсутствуют обязательные колонки: " + ", ".join(missing))

    numeric = pd.DataFrame(index=data.index)
    for column in required_columns:
        try:
            values = pd.to_numeric(data[column], errors="raise")
        except (TypeError, ValueError) as error:
            raise ValueError(f"В колонке «{column}» есть нечисловые значения.") from error
        known = values.dropna()
        if not np.isfinite(known.to_numpy(dtype=float)).all():
            raise ValueError(f"В колонке «{column}» есть бесконечные значения.")
        if column != "Real" and not known.between(0, 1).all():
            raise ValueError(f"Вероятности в колонке «{column}» должны быть долями от 0 до 1.")
        numeric[column] = values

    edges = np.array([index / 10 for index in range(11)])
    # Последняя правая граница сдвигается на один шаг float, чтобы включить 100%.
    edges[-1] = np.nextafter(1.0, np.inf)
    labels = [f"[{index * 10}%; {(index + 1) * 10}%{']' if index == 9 else ')'}"
              for index in range(10)]
    positive = numeric["Real"].where(numeric["Real"] > 0)
    negative = numeric["Real"].where(numeric["Real"] < 0)
    results = []
    for index, column in enumerate(probability_columns):
        groups = pd.DataFrame({
            "Группа": pd.cut(numeric[column], bins=edges, labels=labels, right=False),
            "positive": positive,
            "negative": negative,
        })
        result = groups.groupby("Группа", observed=False, sort=True).agg(**{
            "Количество Real > 0": ("positive", "count"),
            "Количество Real < 0": ("negative", "count"),
            "Сумма Real > 0": ("positive", "sum"),
            "Сумма Real < 0": ("negative", "sum"),
        }).reset_index()
        if index == 1:
            result = result[["Группа", "Количество Real < 0", "Количество Real > 0",
                             "Сумма Real < 0", "Сумма Real > 0"]]
        result["разница"] = result.iloc[:, -2] + result.iloc[:, -1]
        results.append(result)
    return results[0], results[1]


def load_real_groups(
    path: Path,
    sheet: str = "Прогноз",
    *,
    probability_columns: tuple[str, str] = DEFAULT_PROBABILITY_COLUMNS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Читает XLSX path, лист sheet; возвращает два DF для пары probability_columns.

    probability_columns задаёт пару колонок вероятностей (по умолчанию
    Рост/Падение). DF содержат количества, суммы и колонку «разница».
    Использует отображённые вероятности листа, сохранённые числовыми долями,
    и исходный Real. Книгу читает без изменений. Ошибки файла, листа, колонок
    и значений передаются вызывающему коду.
    """
    data = pd.read_excel(path, sheet_name=sheet, usecols=[*probability_columns, "Real"],
                         engine="openpyxl")
    return analyze_real_groups(data, probability_columns=probability_columns)


def run_cli(argv: list[str] | None = None) -> int:
    """Читает параметры argv и печатает два DF; возвращает 0 при успехе, 2 при ошибке.

    При argv=None использует аргументы командной строки. --input выбирает
    исходный XLSX (по умолчанию MIX в outputs проекта), --sheet выбирает лист
    (по умолчанию Прогноз). Суммы и разница в консоли показаны с двумя знаками
    после точки.
    """
    parser = argparse.ArgumentParser(
        description="Количество и суммы Real по десяти интервалам Рост/Падение.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help",
                        help="Показать параметры и завершить работу")
    parser._optionals.title = "Параметры"
    parser.add_argument("--input", type=Path, default=PROJECT / "outputs/MIX_probabilities.xlsx",
                        help="Исходный XLSX; по умолчанию outputs/MIX_probabilities.xlsx проекта")
    parser.add_argument("--sheet", default="Прогноз", help="Лист XLSX; по умолчанию Прогноз")
    args = parser.parse_args(argv)
    try:
        df_growth, df_decline = load_real_groups(args.input, args.sheet)
    except (OSError, ValueError) as error:
        print(f"Ошибка чтения или анализа: {error}", file=sys.stderr)
        return 2
    print(f"Файл: {args.input.resolve()}\nЛист: {args.sheet}")
    print("Интервалы включают нижнюю границу; 100% входят в последнюю группу.")
    print("Нулевые/пустые Real исключены; суммы указаны в пунктах.\n")
    print("Рост — df_growth:")
    print(df_growth.to_string(index=False, float_format="{:.2f}".format))
    print("\nПадение — df_decline:")
    print(df_decline.to_string(index=False, float_format="{:.2f}".format))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
