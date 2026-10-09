"""Проверки дохода покупок/продаж по порогам Бычий и Медвежий.

Логика: вручную рассчитанные примеры проверяют включение целых и дробных порогов,
знак результата продажи, комиссию в б.п. от OPEN, нулевые и отсутствующие
Real, общую сумму, конфликты и параметры. Искусственные XLSX/SQLite проверяют
сопоставление цен по ключу, отказ при неверном Real и запуск из другой папки.
Временные файлы создаются внутри tests и автоматически удаляются.

Примеры из папки проекта:
    python -m unittest discover -s tests -p test_real_regime_trades.py -v
    python -m unittest discover -s tests -v
"""

import hashlib
import importlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from test_funds_model import database_fixture

PROJECT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))


def trade_inputs(folder: Path) -> tuple[Path, Path]:
    """Создаёт в folder три бара RTS и книгу сигналов; возвращает пути XLSX и БД."""
    from source.funds_model import Quote

    path, database = folder / "RTS.xlsx", folder / "RTS.db"
    expiry = date(2030, 3, 21)
    quotes = [Quote(date(2026, 1, day), "R", expiry, opening, close, 100)
              for day, opening, close in ((1, 10000, 10100), (2, 20000, 19950),
                                          (3, 15000, 14920))]
    database_fixture(database, quotes)
    book = Workbook()
    sheet = book.active
    sheet.title = "Прогноз"
    sheet.append(["Дата", "SECID", "LSTTRADE", "Бычий", "Медвежий", "Real"])
    sheet.append([quotes[0].day, "R", expiry, .6, .1, 100])
    sheet.append([quotes[1].day, "R", expiry, .8, .1, -50])
    sheet.append([quotes[2].day, "R", expiry, .1, .7, -80])
    parameters = book.create_sheet("Параметры")
    parameters.append(["Параметр", "Значение"])
    parameters.append(["Серия", "RTS"])
    parameters.append(["Источник", str(database)])
    book.save(path)
    book.close()
    return path, database


class RealRegimeTradesTest(unittest.TestCase):
    """Проверяет доход в пунктах на один контракт и комиссию за полный круг."""

    def module(self):
        """Возвращает модуль расчёта, проверив наличие реализации скрипта."""
        self.assertIsNotNone(importlib.util.find_spec("research_real_regime_trades"),
                             "Скрипт расчёта дохода ещё не реализован.")
        return importlib.import_module("research_real_regime_trades")

    def data(self) -> pd.DataFrame:
        """Возвращает примеры сигналов с нулём, пропуском Real и разными ценами OPEN."""
        return pd.DataFrame({
            "Бычий": [.6, .8, .1, .1, .3, .6, .7, None, .5],
            "Медвежий": [.1, .1, .7, .9, .69, .1, .1, .7, .5],
            "Real": [100, -50, -80, 20, 999, 0, None, -40, 777],
            "OPEN": [10000, 20000, 15000, 25000, 10000, 12000, None, 5000, 10000],
        })

    def test_default_thresholds_short_sign_and_commission(self):
        """Проверяет границы, знак продажи, комиссию, нулевую сделку и итог; результата нет."""
        result = self.module().analyze_regime_trades(self.data())
        self.assertEqual(result["Количество сделок"].tolist(), [3, 3, 6])
        self.assertEqual(result["Доход без комиссии"].tolist(), [50, 100, 150])
        self.assertEqual(result["Сигналы без Real"].tolist(), [1, 0, 1])
        for actual, expected in zip(result["Комиссия"], [12.6, 13.5, 26.1]):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(result["Доход с комиссией"], [37.4, 86.5, 123.9]):
            self.assertAlmostEqual(actual, expected)

    def test_custom_thresholds_and_zero_commission(self):
        """Проверяет настраиваемые пороги, включая равенство, и нулевую комиссию; результата нет."""
        result = self.module().analyze_regime_trades(
            self.data(), bull_threshold=80, bear_threshold=90, commission_bps=0)
        self.assertEqual(result["Количество сделок"].tolist(), [1, 1, 2])
        self.assertEqual(result["Доход с комиссией"].tolist(), [-50, -20, -70])
        self.assertEqual(result["Комиссия"].tolist(), [0, 0, 0])

    def test_fractional_threshold_includes_equality(self):
        """Проверяет равенство дробному порогу обоих направлений и исключение меньшего; результата нет."""
        data = pd.DataFrame({
            "Бычий": [567 / 1000, .1, 566 / 1000],
            "Медвежий": [.1, 567 / 1000, .1],
            "Real": [100, -80, 999],
            "OPEN": [10000, 15000, 10000],
        })
        result = self.module().analyze_regime_trades(data, bull_threshold=56.7, bear_threshold=56.7)
        self.assertEqual(result["Количество сделок"].tolist(), [1, 1, 2])
        self.assertEqual(result["Доход без комиссии"].tolist(), [100, 80, 180])

    def test_conflicts_and_empty_data(self):
        """Проверяет пропуск совпавших сигналов и нулевой итог пустой истории; результата нет."""
        module = self.module()
        conflict = pd.DataFrame({"Бычий": [.6], "Медвежий": [.4], "Real": [10], "OPEN": [100]})
        result = module.analyze_regime_trades(conflict, bull_threshold=60, bear_threshold=40)
        self.assertEqual(result["Количество сделок"].tolist(), [0, 0, 0])
        self.assertEqual(result.attrs["conflicts"], 1)
        empty = pd.DataFrame(columns=["Бычий", "Медвежий", "Real", "OPEN"])
        result = module.analyze_regime_trades(empty)
        self.assertEqual(result.iloc[:, 1:].to_numpy().sum(), 0)

    def test_invalid_parameters_and_missing_open(self):
        """Проверяет недопустимые пороги, комиссию и цену открытия сделки; результата нет."""
        module = self.module()
        for arguments in ({"bull_threshold": -1}, {"bear_threshold": 101},
                          {"commission_bps": -3}, {"commission_bps": float("inf")}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                module.analyze_regime_trades(self.data(), **arguments)
        data = self.data()
        data.loc[0, "OPEN"] = None
        with self.assertRaisesRegex(ValueError, "OPEN"):
            module.analyze_regime_trades(data)

    def test_load_prices_and_reject_mismatched_real(self):
        """Проверяет OPEN из БД и отказ при несоответствии Real ценам; результата нет."""
        module = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            path, database = trade_inputs(Path(directory))
            data, used_database = module.load_regime_trade_data(path)
            self.assertEqual(used_database, database)
            self.assertEqual(data["OPEN"].tolist(), [10000, 20000, 15000])
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute("UPDATE Day SET CLOSE=CLOSE+1 WHERE TRADEDATE='2026-01-01'")
            with self.assertRaisesRegex(ValueError, "Real"):
                module.load_regime_trade_data(path)

    def test_cli_from_another_directory_preserves_inputs(self):
        """Проверяет доход и параметры CLI из другой папки без изменения входов; результата нет."""
        self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            root = Path(directory)
            path, database = trade_inputs(root)
            before = [hashlib.sha256(item.read_bytes()).hexdigest() for item in (path, database)]
            result = subprocess.run(
                [sys.executable, str(PROJECT / "research_real_regime_trades.py"),
                 "--input", str(path), "--bull-threshold", "60", "--bear-threshold", "70",
                 "--commission-bps", "3"],
                cwd=root, capture_output=True, text=True, encoding="utf-8",
                env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Всего", result.stdout)
            self.assertIn("116.50", result.stdout)
            self.assertEqual(before, [hashlib.sha256(item.read_bytes()).hexdigest()
                                      for item in (path, database)])


if __name__ == "__main__":
    unittest.main()
