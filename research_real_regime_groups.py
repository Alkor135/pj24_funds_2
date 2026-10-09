r"""Количество и суммы положительных/отрицательных Real по Бычий и Медвежий.

Логика: читает колонки Бычий, Медвежий и Real выбранного листа XLSX.
Вероятности хранятся как доли от 0 до 1 и разбиваются на десять интервалов:
[0%; 10%), [10%; 20%), …, [90%; 100%]. Общие расчётные функции импортируются
из research_real_groups.py. Создаёт df_bull и df_bear с группой, двумя
количествами, двумя суммами Real и последней колонкой «разница».
В df_bull положительные показатели идут первыми; в df_bear — отрицательные.
«разница» равна сумме двух колонок сумм Real с сохранением знаков.
Нулевые и пустые Real исключаются. Пустая вероятность исключает строку
только из соответствующего DF. Все десять групп выводятся, включая пустые.
Анализируется вся таблица без фильтра истории, расходов и разделения периодов.
Оба DF печатаются в консоль; исходная книга не изменяется.

По умолчанию: outputs/MIX_probabilities.xlsx рядом со скриптом, лист Прогноз.
Явно заданный относительный путь отсчитывается от текущей рабочей папки.
Зависимости: pandas, numpy и openpyxl из requirements.txt.

Примеры из папки проекта:
    .venv\Scripts\python.exe research_real_regime_groups.py
    python research_real_regime_groups.py --input outputs/RTS_probabilities.xlsx
    python research_real_regime_groups.py --input C:/data/table.xlsx --sheet Прогноз
    python research_real_regime_groups.py --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from research_real_groups import analyze_real_groups, load_real_groups

PROJECT = Path(__file__).resolve().parent
PROBABILITY_COLUMNS = ("Бычий", "Медвежий")


def analyze_regime_groups(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Возвращает df_bull/df_bear по вероятностям Бычий/Медвежий из data.

    data должен содержать числовые Бычий/Медвежий в долях 0–1 и Real в пунктах.
    Результат — два DF по десяти фиксированным интервалам: количества Real
    обоих знаков, их суммы и сумма этих сумм в колонке «разница».
    В df_bear отрицательные показатели идут первыми. Пропуски и нули Real
    исключаются, пустые группы получают нули. Исходный DF не изменяется;
    неверные колонки или значения вызывают ValueError.
    """
    return analyze_real_groups(data, probability_columns=PROBABILITY_COLUMNS)


def load_regime_groups(path: Path, sheet: str = "Прогноз") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Читает лист sheet из XLSX path; возвращает df_bull и df_bear с анализом Real.

    Использует числовые доли Бычий/Медвежий выбранного листа и исходный Real.
    Книгу не изменяет; ошибки чтения и значений передаёт вызывающему коду.
    """
    return load_real_groups(path, sheet, probability_columns=PROBABILITY_COLUMNS)


def run_cli(argv: list[str] | None = None) -> int:
    """Читает параметры argv и печатает df_bull/df_bear; возвращает 0 или код ошибки 2.

    При argv=None использует аргументы командной строки. --input выбирает
    XLSX (по умолчанию MIX в outputs проекта), --sheet выбирает лист
    (по умолчанию Прогноз). Суммы и «разница» показаны с двумя знаками после точки.
    """
    parser = argparse.ArgumentParser(
        description="Количество и суммы Real по десяти интервалам Бычий/Медвежий.",
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
        df_bull, df_bear = load_regime_groups(args.input, args.sheet)
    except (OSError, ValueError) as error:
        print(f"Ошибка чтения или анализа: {error}", file=sys.stderr)
        return 2
    print(f"Файл: {args.input.resolve()}\nЛист: {args.sheet}")
    print("Интервалы включают нижнюю границу; 100% входят в последнюю группу.")
    print("Нулевые/пустые Real исключены; суммы указаны в пунктах.\n")
    print("Бычий — df_bull:")
    print(df_bull.to_string(index=False, float_format="{:.2f}".format))
    print("\nМедвежий — df_bear:")
    print(df_bear.to_string(index=False, float_format="{:.2f}".format))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
