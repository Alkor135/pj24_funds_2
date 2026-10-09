"""Проверки группировки Real по фиксированным интервалам вероятности.

Логика: проверяет вручную рассчитанные количества и суммы для Рост/Падение,
а также колонку «разница» как сумму положительного и отрицательного Real.
Проверяет порядок колонок, границы от 0 до 100%, пропуски, нули и неверные
данные. Запускает скрипт на искусственном XLSX из другой папки и проверяет
сохранность книги.
Временные книги создаются внутри tests и автоматически удаляются.

Примеры из папки проекта:
    python -m unittest discover -s tests -p test_real_groups.py -v
    python -m unittest discover -s tests -v
"""

import hashlib
import importlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

PROJECT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))


class RealGroupsTest(unittest.TestCase):
    """Проверяет результаты анализа двух направлений и запуск на книге XLSX."""

    def analyze(self, data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Вызывает анализ data; возвращает два DF, проверив наличие реализации."""
        self.assertIsNotNone(importlib.util.find_spec("research_real_groups"),
                             "Скрипт группировки Real ещё не реализован.")
        module = importlib.import_module("research_real_groups")
        return module.analyze_real_groups(data)

    def test_boundaries_counts_and_signed_sums(self):
        """Проверяет порядок, количества и сложение сумм со знаками на границах; результата нет."""
        data = pd.DataFrame({
            "Рост": [0, .0999, .1, .1999, .2, .3, .4, .5, .6, .7, .8, .9, 1,
                     None, .5, .6],
            "Падение": [1, .9, .8, .7, .6, .5, .4, .3, .2, .1, 0, .0999, .1999,
                        .2, None, .6],
            "Real": [2, -3, 5, -7, 11, 13, 17, 19, 23, 29, 31, 37, -41,
                     43, 0, None],
        })
        df_growth, df_decline = self.analyze(data)
        self.assertEqual(df_growth.columns.tolist(), [
            "Группа", "Количество Real > 0", "Количество Real < 0",
            "Сумма Real > 0", "Сумма Real < 0", "разница",
        ])
        self.assertEqual(df_decline.columns.tolist(), [
            "Группа", "Количество Real < 0", "Количество Real > 0",
            "Сумма Real < 0", "Сумма Real > 0", "разница",
        ])
        self.assertEqual(df_growth.shape, (10, 6))
        self.assertEqual(df_decline.shape, (10, 6))
        self.assertEqual(df_growth.iloc[:, 1:].values.tolist(), [
            [1, 1, 2, -3, -1], [1, 1, 5, -7, -2], [1, 0, 11, 0, 11],
            [1, 0, 13, 0, 13], [1, 0, 17, 0, 17], [1, 0, 19, 0, 19],
            [1, 0, 23, 0, 23], [1, 0, 29, 0, 29], [1, 0, 31, 0, 31],
            [1, 1, 37, -41, -4],
        ])
        self.assertEqual(df_decline.iloc[:, 1:].values.tolist(), [
            [0, 2, 0, 68, 68], [1, 1, -41, 29, -12], [0, 2, 0, 66, 66],
            [0, 1, 0, 19, 19], [0, 1, 0, 17, 17], [0, 1, 0, 13, 13],
            [0, 1, 0, 11, 11], [1, 0, -7, 0, -7], [0, 1, 0, 5, 5],
            [1, 1, -3, 2, -1],
        ])

    def test_empty_groups_and_missing_values(self):
        """Проверяет десять пустых групп с нулевыми показателями и разницей; результата нет."""
        for rows in ([], [[None, .5, 0], [.1, None, None], [None, None, 100]]):
            with self.subTest(rows=rows):
                data = pd.DataFrame(rows, columns=["Рост", "Падение", "Real"])
                for result in self.analyze(data):
                    self.assertEqual(result.shape, (10, 6))
                    self.assertEqual(result.iloc[:, 1:].to_numpy().sum(), 0)

    def test_invalid_values_are_rejected(self):
        """Проверяет отказ при выходе вероятности из 0–1 и неверном Real; результата нет."""
        for column, value in (("Рост", -.01), ("Падение", 1.01),
                              ("Real", float("inf")), ("Real", "ошибка")):
            with self.subTest(column=column, value=value):
                data = pd.DataFrame({"Рост": [.5], "Падение": [.5], "Real": [value]
                                     if column == "Real" else [10]})
                if column != "Real":
                    data[column] = value
                with self.assertRaises(ValueError):
                    self.analyze(data)

    def test_missing_column_is_rejected(self):
        """Проверяет явный отказ при отсутствии необходимой колонки; результата нет."""
        with self.assertRaisesRegex(ValueError, "Падение"):
            self.analyze(pd.DataFrame({"Рост": [.5], "Real": [10]}))

    def test_cli_from_another_directory_preserves_workbook(self):
        """Проверяет печать двух DF из выбранного листа без изменения XLSX; результата нет."""
        script = PROJECT / "research_real_groups.py"
        self.assertTrue(script.is_file(), "Скрипт группировки Real ещё не реализован.")
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            root = Path(directory)
            path = root / "input.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "Данные"
            sheet.append(["Real", "Дата", "Падение", "Рост"])
            sheet.append([-17, "2026-01-01", .5, .5])
            sheet.append([23, "2026-01-02", .05, .05])
            book.save(path)
            book.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = subprocess.run(
                [sys.executable, str(script), "--input", str(path), "--sheet", "Данные"],
                cwd=root, capture_output=True, text=True, encoding="utf-8",
                env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("df_growth", result.stdout)
            self.assertIn("df_decline", result.stdout)
            self.assertIn("-17.00", result.stdout)
            self.assertIn("23.00", result.stdout)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
