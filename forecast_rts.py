r"""Формирование XLSX с дневными вероятностями RTS из RTS_day2.db.

Логика: общий модуль source.funds_model читает БД только для чтения, объединяет
переименования по LSTTRADE и выбирает один основной контракт на дату.
Две причинные модели оценивают движение OPEN → CLOSE и режим на закрытии
полного дневного бара Day. XLSX содержит десять колонок, включая Флэт и
Real=CLOSE−OPEN в пунктах, диагностику и параметры. У будущих или синтетических
баров Real пустой. Требуется openpyxl из requirements.txt.

Примеры из папки проекта:
    .\.venv\Scripts\python.exe forecast_rts.py --overwrite
    python forecast_rts.py
    python forecast_rts.py --db C:/data_quote/day2/RTS_day2.db --output outputs/RTS.xlsx
    python forecast_rts.py --start-date 2025-01-01 --end-date 2026-10-05
    python forecast_rts.py --forecast-date 2026-10-06 --previous-session 2026-10-05
    python forecast_rts.py --overwrite
    python forecast_rts.py --help

Пути по умолчанию: C:/data_quote/day2/RTS_day2.db и
outputs/RTS_probabilities.xlsx рядом со скриптом. Исторические строки
реконструируются по текущему снимку БД, статус моделей UNVALIDATED.
"""

from source.funds_model import run_cli


def main() -> int:
    """Запускает расчёт RTS с параметрами командной строки; возвращает код выхода."""
    return run_cli("RTS")


if __name__ == "__main__":
    raise SystemExit(main())
