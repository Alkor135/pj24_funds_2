"""Проверки группировки Real по вероятностям Бычий и Медвежий.

Логика: проверяет вручную рассчитанные количества и суммы Real, колонку
«разница» как сложение сумм со знаками, порядок колонок, границы 0%, 10%,
90%, 100% и пропуски. Данные содержат только вероятности Бычий/Медвежий.
Проверяет запуск нового скрипта из другой папки на выбранном листе XLSX
и сохранность исходной книги. Временные файлы находятся внутри tests.

Примеры из папки проекта:
    python -m unittest discover -s tests -p test_real_regime_groups.py -v
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


class RealRegimeGroupsTest(unittest.TestCase):
    """Проверяет анализ Бычий/Медвежий и чтение этих колонок из XLSX."""

    def analyze(self, data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Вызывает анализ data; возвращает два DF, проверив наличие нового скрипта."""
        self.assertIsNotNone(importlib.util.find_spec("research_real_regime_groups"),
                             "Скрипт группировки Бычий/Медвежий ещё не реализован.")
        module = importlib.import_module("research_real_regime_groups")
        return module.analyze_regime_groups(data)

    def test_selected_columns_boundaries_and_signed_totals(self):
        """Проверяет нужные вероятности, границы, порядок и сложение сумм; результата нет."""
        data = pd.DataFrame({
            "Бычий": [0, .1, .1, .9, 1, None, .4, .5],
            "Медвежий": [1, .9, 0, .1, 0, .5, None, .4],
            "Real": [4, 10, -3, -20, 7, 9, 0, None],
        })
        df_bull, df_bear = self.analyze(data)
        self.assertEqual(df_bull.columns.tolist(), [
            "Группа", "Количество Real > 0", "Количество Real < 0",
            "Сумма Real > 0", "Сумма Real < 0", "разница",
        ])
        self.assertEqual(df_bear.columns.tolist(), [
            "Группа", "Количество Real < 0", "Количество Real > 0",
            "Сумма Real < 0", "Сумма Real > 0", "разница",
        ])
        self.assertEqual(df_bull.shape, (10, 6))
        self.assertEqual(df_bear.shape, (10, 6))
        self.assertEqual(df_bull.iloc[:, 1:].values.tolist(), [
            [1, 0, 4, 0, 4], [1, 1, 10, -3, 7], [0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0],
            [1, 1, 7, -20, -13],
        ])
        self.assertEqual(df_bear.iloc[:, 1:].values.tolist(), [
            [1, 1, -3, 7, 4], [1, 0, -20, 0, -20], [0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 1, 0, 9, 9],
            [0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0],
            [0, 2, 0, 14, 14],
        ])

    def test_missing_regime_column_is_rejected(self):
        """Проверяет, что отсутствие Медвежий сообщает имя нужной колонки; результата нет."""
        with self.assertRaisesRegex(ValueError, "Медвежий"):
            self.analyze(pd.DataFrame({"Бычий": [.5], "Real": [10]}))

    def test_cli_reads_regime_columns_and_preserves_workbook(self):
        """Проверяет вывод двух DF из выбранного листа без изменения XLSX; результата нет."""
        script = PROJECT / "research_real_regime_groups.py"
        self.assertTrue(script.is_file(), "Скрипт Бычий/Медвежий ещё не реализован.")
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            root = Path(directory)
            path = root / "input.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "Данные"
            sheet.append(["Real", "Дата", "Медвежий", "Бычий"])
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
            self.assertIn("Бычий — df_bull:", result.stdout)
            self.assertIn("Медвежий — df_bear:", result.stdout)
            self.assertIn("-17.00", result.stdout)
            self.assertIn("23.00", result.stdout)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
