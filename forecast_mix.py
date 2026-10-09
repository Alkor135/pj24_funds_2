r"""Формирование XLSX с дневными вероятностями MIX из MIX_day2.db.

Логика: общий модуль source.funds_model читает БД только для чтения, объединяет
переименования по LSTTRADE и выбирает один основной контракт на дату.
Две причинные модели оценивают движение OPEN → CLOSE и режим на закрытии
полного дневного бара Day. XLSX включает Флэт и Real=CLOSE−OPEN в пунктах.
Перенесённые цены исключаются из движения, но их CLOSE сохраняются в режиме.
У будущих или синтетических баров Real пустой. Требуется openpyxl из requirements.txt.

Примеры из папки проекта:
    .venv\Scripts\python.exe forecast_mix.py --overwrite
    python forecast_mix.py
    python forecast_mix.py --db C:/data_quote/day2/MIX_day2.db --output outputs/MIX.xlsx
    python forecast_mix.py --start-date 2025-01-01 --end-date 2026-10-05
    python forecast_mix.py --forecast-date 2026-10-06 --previous-session 2026-10-05
    python forecast_mix.py --q 0.25 --k 1.0 --alpha 0.5 --overwrite
    python forecast_mix.py --help

Пути по умолчанию: C:/data_quote/day2/MIX_day2.db и
outputs/MIX_probabilities.xlsx рядом со скриптом. Исторические строки
реконструируются по текущему снимку БД, статус моделей UNVALIDATED.
"""

from source.funds_model import run_cli


def main() -> int:
    """Запускает расчёт MIX с параметрами командной строки; возвращает код выхода."""
    return run_cli("MIX")


if __name__ == "__main__":
    raise SystemExit(main())
