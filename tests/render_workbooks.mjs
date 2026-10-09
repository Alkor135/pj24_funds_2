/**
 * Назначение: визуальная проверка сохранённых XLSX через Artifact Tool.
 * Логика: импортирует оба результата только для чтения и сохраняет изображения
 * первых строк каждого листа в tests/previews. Основной лист включает Флэт
 * и численный Real в пунктах. Исходные XLSX не изменяет.
 * Параметры: первый аргумент — папка проекта, по умолчанию родитель tests;
 * второй — необязательный каталог node_modules встроенной среды Codex.
 * Результат: восемь PNG для просмотра оформления, включая последние прогнозы.
 *
 * Запуск из папки проекта с доступным пакетом @oai/artifact-tool:
 * node tests/render_workbooks.mjs
 * node tests/render_workbooks.mjs C:/Users/Alkor/VSCode/pj24_funds_2
 * node tests/render_workbooks.mjs C:/Users/Alkor/VSCode/pj24_funds_2 C:/Users/Alkor/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules
 * В среде Codex используется встроенный Node.js и каталог пакетов,
 * полученный через load_workspace_dependencies; он открывается только для чтения.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const packageLoader = process.argv[3]
  ? createRequire(path.join(path.resolve(process.argv[3]), "..", "preview-loader.cjs"))
  : createRequire(import.meta.url);
const { FileBlob, SpreadsheetFile } = await import(
  pathToFileURL(packageLoader.resolve("@oai/artifact-tool")).href
);

const testsDirectory = path.dirname(fileURLToPath(import.meta.url));
const project = path.resolve(process.argv[2] ?? path.join(testsDirectory, ".."));
const output = path.join(testsDirectory, "previews");
await fs.mkdir(output, { recursive: true });
for (const series of ["RTS", "MIX"]) {
  const workbook = await SpreadsheetFile.importXlsx(
    await FileBlob.load(path.join(project, "outputs", `${series}_probabilities.xlsx`))
  );
  workbook.recalculate();
  for (const [sheetName, range, label] of [
    ["Прогноз", "A1:J9", "forecast"],
    ["Диагностика", "A1:P6", "diagnostics"],
    ["Параметры", "A1:B10", "parameters"],
  ]) {
    const preview = await workbook.render({ sheetName, range, scale: 1.5, format: "png" });
    await fs.writeFile(path.join(output, `${series}_${label}.png`),
      new Uint8Array(await preview.arrayBuffer()));
  }
  const lastRow = workbook.worksheets.getItem("Прогноз").getUsedRange().values.length;
  const latest = await workbook.render({ sheetName: "Прогноз",
    range: `A${lastRow - 5}:J${lastRow}`, scale: 1.5, format: "png" });
  await fs.writeFile(path.join(output, `${series}_latest.png`),
    new Uint8Array(await latest.arrayBuffer()));
  console.log(`${series}: сохранены четыре изображения листов.`);
}
