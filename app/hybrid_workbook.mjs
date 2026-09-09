import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { inflateRawSync } from "node:zlib";

function argumentsMap(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    if (!key?.startsWith("--") || values[index + 1] === undefined) {
      throw new Error(`Некорректный аргумент: ${key ?? "<пусто>"}`);
    }
    result[key.slice(2)] = values[index + 1];
  }
  return result;
}

const args = argumentsMap(process.argv.slice(2));
if (args["audit-xlsx"]) {
  if (!args.verification) throw new Error("Не указан --verification для проверки XLSX");
  const expectedCharts = args["expected-chart-count"] === undefined
    ? null
    : Number(args["expected-chart-count"]);
  if (expectedCharts !== null && (!Number.isInteger(expectedCharts) || expectedCharts < 0)) {
    throw new Error("--expected-chart-count должен быть неотрицательным целым числом");
  }
  const audit = inspectXlsxPackage(
    await fs.readFile(path.resolve(args["audit-xlsx"])),
    expectedCharts ?? 0,
  );
  const externalLinks = [...audit.external_link_parts, ...audit.external_chart_references];
  const chartsValid = expectedCharts === null || audit.charts_use_numeric_mach_axis;
  const verificationPath = path.resolve(args.verification);
  await fs.mkdir(path.dirname(verificationPath), { recursive: true });
  await fs.writeFile(verificationPath, JSON.stringify({
    schema: "repairmach.hybrid-workbook-verification/1.0",
    build_status: externalLinks.length || !chartsValid ? "failed" : "passed",
    audit_only: true,
    workbook: path.basename(args["audit-xlsx"]),
    external_links_detected: externalLinks.length > 0,
    ...audit,
  }, null, 2), "utf8");
  if (externalLinks.length) {
    throw new Error(`В Excel обнаружены внешние ссылки (${externalLinks.length})`);
  }
  if (!chartsValid) throw new Error("Диаграммы Mach не используют числовую X-ось");
  process.exit(0);
}
for (const name of ["input", "output", "verification", "previews", "node-modules"]) {
  if (!args[name]) throw new Error(`Не указан --${name}`);
}

const outputPath = path.resolve(args.output);
const verificationPath = path.resolve(args.verification);
const previewsDir = path.resolve(args.previews);
const transactionId = `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const outputParts = path.parse(outputPath);
const verificationParts = path.parse(verificationPath);
const stagedOutputPath = path.join(
  outputParts.dir,
  `.${outputParts.name}.tmp-${transactionId}${outputParts.ext || ".xlsx"}`,
);
const stagedVerificationPath = path.join(
  verificationParts.dir,
  `.${verificationParts.name}.tmp-${transactionId}${verificationParts.ext || ".json"}`,
);
const stagedPreviewsDir = path.join(
  path.dirname(previewsDir),
  `.${path.basename(previewsDir)}.tmp-${transactionId}`,
);
const outputInspectionPath = `${outputPath}.inspect.ndjson`;
const stagedOutputInspectionPath = `${stagedOutputPath}.inspect.ndjson`;

async function removePublishedOutputs() {
  await fs.rm(outputPath, { force: true });
  await fs.rm(outputInspectionPath, { force: true });
  await fs.rm(verificationPath, { force: true });
  await fs.rm(previewsDir, { recursive: true, force: true });
}

async function removeStagedOutputs() {
  await fs.rm(stagedOutputPath, { force: true });
  await fs.rm(stagedOutputInspectionPath, { force: true });
  await fs.rm(stagedVerificationPath, { force: true });
  await fs.rm(stagedPreviewsDir, { recursive: true, force: true });
}

async function rollbackPublication() {
  await Promise.allSettled([
    fs.rm(outputPath, { force: true }),
    fs.rm(outputInspectionPath, { force: true }),
    fs.rm(verificationPath, { force: true }),
    fs.rm(previewsDir, { recursive: true, force: true }),
    fs.rm(stagedOutputPath, { force: true }),
    fs.rm(stagedOutputInspectionPath, { force: true }),
    fs.rm(stagedVerificationPath, { force: true }),
    fs.rm(stagedPreviewsDir, { recursive: true, force: true }),
  ]);
}

function injectTestFailure(stage) {
  if (process.env.REPAIRMACH_WORKBOOK_TEST_FAIL_STAGE === stage) {
    throw new Error(`Тестовый сбой атомарной публикации: ${stage}`);
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(path.dirname(verificationPath), { recursive: true });
await fs.mkdir(path.dirname(previewsDir), { recursive: true });
// A result from an earlier run must never look current while a new build is in
// progress.  All public artifacts are therefore absent until the transaction
// below has passed export, package audit, re-import and every PNG render.
await removePublishedOutputs();
await removeStagedOutputs();

const require = createRequire(import.meta.url);
const artifactEntry = require.resolve("@oai/artifact-tool", { paths: [path.resolve(args["node-modules"])] });
const { FileBlob, SpreadsheetFile, Workbook } = await import(pathToFileURL(artifactEntry).href);
const jszipEntry = require.resolve("jszip", { paths: [path.resolve(args["node-modules"])] });
const jszipModule = await import(pathToFileURL(jszipEntry).href);
const JSZip = jszipModule.default ?? jszipModule;
const bundle = JSON.parse(await fs.readFile(path.resolve(args.input), "utf8"));
if (bundle.schema !== "repairmach.hybrid-series/1.0") {
  throw new Error("Неизвестная схема гибридного результата");
}

const workbook = Workbook.create();
const summarySheet = workbook.worksheets.add("Сводка");
const adhSheet = workbook.worksheets.add("АДХ");
const cyaSheet = workbook.worksheets.add("Cyα");
const semiSheet = workbook.worksheets.add("Полуэмпирика");
const sourceSheet = workbook.worksheets.add("Источники");
const methodSheet = workbook.worksheets.add("Методика");

const fontFamily = "Arial";
const colors = {
  ink: "#202020",
  gray: "#666666",
  light: "#F2F2F2",
  line: "#B7B7B7",
  dark: "#3F3F3F",
  good: "#E2F0D9",
  warn: "#FFF2CC",
  bad: "#F4CCCC",
};

function lastColumn(index) {
  let value = index + 1;
  let text = "";
  while (value > 0) {
    value -= 1;
    text = String.fromCharCode(65 + (value % 26)) + text;
    value = Math.floor(value / 26);
  }
  return text;
}

function setTitle(sheet, text, subtitle, endColumn) {
  sheet.showGridLines = false;
  sheet.mergeCells(`A1:${endColumn}1`);
  sheet.mergeCells(`A2:${endColumn}2`);
  sheet.getRange(`A1:${endColumn}1`).format.borders = {
    bottom: { style: "medium", color: colors.dark },
  };
  sheet.getRange("A1").values = [[text]];
  sheet.getRange("A1").format.font = { name: fontFamily, size: 15, bold: true, color: colors.ink };
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${endColumn}2`).format = {
    font: { name: fontFamily, size: 10, italic: true, color: colors.gray },
    wrapText: true,
  };
  sheet.getRange("1:1").format.rowHeight = 28;
  sheet.getRange("2:2").format.rowHeight = 34;
}

function flattenObject(value, prefix = "") {
  const rows = [];
  if (Array.isArray(value)) {
    value.forEach((item, index) => rows.push(...flattenObject(item, `${prefix}[${index}]`)));
    return rows;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      rows.push(...flattenObject(item, prefix ? `${prefix}.${key}` : key));
    }
    return rows;
  }
  rows.push([prefix, value === null ? "null" : value]);
  return rows;
}

function styleHeader(range) {
  range.format = {
    fill: colors.dark,
    font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#FFFFFF" },
  };
  range.format.rowHeight = 30;
}

function styleBody(range) {
  range.format = {
    font: { name: fontFamily, size: 10, color: colors.ink },
    verticalAlignment: "center",
    borders: { bottom: { style: "hair", color: colors.line } },
  };
}

function styleStatus(range) {
  range.conditionalFormats.add("containsText", {
    text: "complete",
    format: { fill: colors.good, font: { color: "#375623" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "incomplete",
    format: { fill: colors.bad, font: { color: "#9C0006", bold: true } },
  });
}

function addMachChart(sheet, range, title, position, colorsForSeries, yTitle, xFormula) {
  // Mach is a physical numeric variable.  A scatter chart preserves the true
  // distance between, for example, M=0.8 and M=1.2 instead of treating Mach
  // values as equally spaced categories.
  const chart = sheet.charts.add("scatter", range);
  chart.title = title;
  chart.titleTextStyle.fontSize = 12;
  chart.titleTextStyle.typeface = fontFamily;
  chart.hasLegend = true;
  chart.legend = { position: "top", textStyle: { typeface: fontFamily, fontSize: 9 } };
  chart.xAxis = {
    axisType: "valueAxis",
    numberFormatCode: "0.0",
    numberFormatSourceLinked: false,
    textStyle: { typeface: fontFamily, fontSize: 9 },
  };
  chart.yAxis = {
    numberFormatCode: "0.0000",
    numberFormatSourceLinked: false,
    textStyle: { typeface: fontFamily, fontSize: 9 },
  };
  chart.xAxis.title.text = "Число M";
  chart.yAxis.title.text = yTitle;
  chart.series.items.forEach((series, index) => {
    // artifact-tool's scatter fast path may otherwise export an empty numLit
    // for X.  Bind every series explicitly to the numeric Mach cells.
    series.categoryFormula = xFormula;
    const color = colorsForSeries[index] ?? colors.gray;
    series.fill = { type: "solid", color };
    series.line = { fill: color, width: index === 0 ? 2.25 : 1.75, style: index === 0 ? "solid" : "dashed" };
  });
  chart.setPosition(position[0], position[1]);
  return chart;
}

function inspectionRecords(ndjson) {
  const records = [];
  for (const line of String(ndjson ?? "").split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      records.push(JSON.parse(line));
    } catch (error) {
      throw new Error(`Не удалось разобрать результат проверки Excel: ${error.message}`);
    }
  }
  if (!records.length) throw new Error("Проверка Excel не вернула результата");
  return records;
}

function matchedInspectionRecords(ndjson) {
  return inspectionRecords(ndjson).filter(record => {
    if (record?.kind !== "notice") return true;
    return !/matched\s+0\s+entr(?:y|ies)|0\s+entries/i.test(String(record.message ?? ""));
  });
}

function collectFormulaStrings(value, key = "", formulas = []) {
  if (Array.isArray(value)) {
    for (const item of value) collectFormulaStrings(item, key, formulas);
  } else if (value && typeof value === "object") {
    for (const [childKey, childValue] of Object.entries(value)) {
      collectFormulaStrings(childValue, childKey, formulas);
    }
  } else if (typeof value === "string" && /formula/i.test(key) && value.startsWith("=")) {
    formulas.push(value);
  }
  return formulas;
}

function externalFormulaReferences(ndjson) {
  const formulas = inspectionRecords(ndjson).flatMap(record => collectFormulaStrings(record));
  return formulas.filter(formula => /\[[^\]]+\]|(?:https?|file):\/\/|\\\\/i.test(formula));
}

function zipEntries(buffer) {
  const bytes = Buffer.isBuffer(buffer) ? buffer : Buffer.from(buffer);
  const minimumEocdSize = 22;
  const searchStart = Math.max(0, bytes.length - minimumEocdSize - 0xFFFF);
  let eocdOffset = -1;
  for (let offset = bytes.length - minimumEocdSize; offset >= searchStart; offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054B50) {
      eocdOffset = offset;
      break;
    }
  }
  if (eocdOffset < 0) throw new Error("XLSX не содержит корректного ZIP-каталога");

  const entryCount = bytes.readUInt16LE(eocdOffset + 10);
  const centralOffset = bytes.readUInt32LE(eocdOffset + 16);
  if (entryCount === 0xFFFF || centralOffset === 0xFFFFFFFF) {
    throw new Error("ZIP64 не поддерживается проверкой внешних ссылок");
  }

  const entries = new Map();
  let cursor = centralOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (cursor + 46 > bytes.length || bytes.readUInt32LE(cursor) !== 0x02014B50) {
      throw new Error("Повреждён центральный каталог XLSX");
    }
    const method = bytes.readUInt16LE(cursor + 10);
    const compressedSize = bytes.readUInt32LE(cursor + 20);
    const uncompressedSize = bytes.readUInt32LE(cursor + 24);
    const fileNameLength = bytes.readUInt16LE(cursor + 28);
    const extraLength = bytes.readUInt16LE(cursor + 30);
    const commentLength = bytes.readUInt16LE(cursor + 32);
    const localOffset = bytes.readUInt32LE(cursor + 42);
    const fileName = bytes.subarray(cursor + 46, cursor + 46 + fileNameLength).toString("utf8");
    if (localOffset + 30 > bytes.length || bytes.readUInt32LE(localOffset) !== 0x04034B50) {
      throw new Error(`Повреждена запись XLSX: ${fileName}`);
    }
    const localNameLength = bytes.readUInt16LE(localOffset + 26);
    const localExtraLength = bytes.readUInt16LE(localOffset + 28);
    const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    const dataEnd = dataStart + compressedSize;
    if (dataEnd > bytes.length) throw new Error(`Обрезана запись XLSX: ${fileName}`);
    const compressed = bytes.subarray(dataStart, dataEnd);
    let content;
    if (method === 0) content = Buffer.from(compressed);
    else if (method === 8) content = inflateRawSync(compressed);
    else throw new Error(`Неподдерживаемое ZIP-сжатие ${method}: ${fileName}`);
    if (content.length !== uncompressedSize) {
      throw new Error(`Размер записи XLSX не совпал: ${fileName}`);
    }
    entries.set(fileName.replace(/\\/g, "/"), content);
    cursor += 46 + fileNameLength + extraLength + commentLength;
  }
  return entries;
}

function decodeXmlText(value) {
  return value
    .replace(/&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&gt;/g, ">")
    .replace(/&lt;/g, "<")
    .replace(/&amp;/g, "&");
}

async function bindScatterChartsToMachCells(outputPath, xFormula) {
  const archive = await JSZip.loadAsync(await fs.readFile(outputPath));
  const chartNames = Object.keys(archive.files)
    .filter(name => /^xl\/(?:drawings\/)?charts\/chart\d+\.xml$/i.test(name));
  let changedCharts = 0;
  const formula = xFormula.replace(/^=/, "");
  for (const name of chartNames) {
    const entry = archive.file(name);
    if (!entry) continue;
    let xml = await entry.async("string");
    if (!/<c:scatterChart\b/.test(xml)) continue;
    let changedBindings = 0;
    xml = xml.replace(/<c:xVal>[\s\S]*?<\/c:xVal>/g, () => {
      changedBindings += 1;
      return `<c:xVal><c:numRef><c:f>${formula}</c:f></c:numRef></c:xVal>`;
    });
    if (!changedBindings) throw new Error(`Диаграмма ${name} не содержит X-значений`);
    xml = xml.replace(/<c:scatterStyle\s+val=["']marker["']\s*\/>/g, '<c:scatterStyle val="lineMarker" />');
    archive.file(name, xml);
    changedCharts += 1;
  }
  if (changedCharts !== chartNames.length) {
    throw new Error(`Не все диаграммы распознаны как XY: ${changedCharts} из ${chartNames.length}`);
  }
  const repaired = await archive.generateAsync({
    type: "nodebuffer",
    compression: "DEFLATE",
    compressionOptions: { level: 6 },
  });
  await fs.writeFile(outputPath, repaired);
}

function inspectXlsxPackage(buffer, expectedChartCount, expectedXFormula = null) {
  const entries = zipEntries(buffer);
  const externalLinkParts = [];
  const chartParts = [];
  const externalChartReferences = [];
  let scatterChartCount = 0;
  let lineChartCount = 0;
  let categoryAxisCount = 0;
  let valueAxisCount = 0;
  let seriesCount = 0;
  let xValueBindingCount = 0;
  let yValueBindingCount = 0;
  const xValueFormulas = [];

  for (const [name, content] of entries) {
    if (name.startsWith("xl/externalLinks/")) externalLinkParts.push(name);
    if (name.endsWith(".rels")) {
      const relationText = content.toString("utf8");
      if (/externalLinkPath|\/externalLinks\/|TargetMode=["']External["']/i.test(relationText)) {
        externalLinkParts.push(name);
      }
    }
    if (!/^xl\/(?:drawings\/)?charts\/chart\d+\.xml$/i.test(name)) continue;
    chartParts.push(name);
    const xml = content.toString("utf8");
    scatterChartCount += (xml.match(/<c:scatterChart\b/g) ?? []).length;
    lineChartCount += (xml.match(/<c:lineChart\b/g) ?? []).length;
    categoryAxisCount += (xml.match(/<c:catAx\b/g) ?? []).length;
    valueAxisCount += (xml.match(/<c:valAx\b/g) ?? []).length;
    seriesCount += (xml.match(/<c:ser\b/g) ?? []).length;
    for (const match of xml.matchAll(/<c:xVal>\s*<c:numRef>\s*<c:f>([^<]+)<\/c:f>/g)) {
      xValueBindingCount += 1;
      xValueFormulas.push(decodeXmlText(match[1]));
    }
    yValueBindingCount += (xml.match(/<c:yVal>\s*<c:numRef>\s*<c:f>[^<]+<\/c:f>/g) ?? []).length;
    for (const match of xml.matchAll(/<c:f>([\s\S]*?)<\/c:f>/g)) {
      const formula = decodeXmlText(match[1]);
      if (/\[[^\]]+\]|(?:https?|file):\/\/|\\\\/i.test(formula) || !formula.includes("!")) {
        externalChartReferences.push(`${name}: ${formula}`);
      }
    }
  }

  const uniqueExternalParts = [...new Set(externalLinkParts)].sort();
  const normalizedExpectedXFormula = expectedXFormula?.replace(/^=/, "") ?? null;
  const xBindingsMatchMachCells = normalizedExpectedXFormula === null
    || (xValueFormulas.length === seriesCount
      && xValueFormulas.every(formula => formula === normalizedExpectedXFormula));
  const chartsUseNumericMachAxis = chartParts.length === expectedChartCount
    && scatterChartCount === expectedChartCount
    && lineChartCount === 0
    && categoryAxisCount === 0
    && valueAxisCount >= expectedChartCount * 2
    && xValueBindingCount === seriesCount
    && yValueBindingCount === seriesCount
    && xBindingsMatchMachCells;
  return {
    external_link_parts: uniqueExternalParts,
    external_chart_references: externalChartReferences,
    chart_parts: chartParts.sort(),
    scatter_chart_count: scatterChartCount,
    line_chart_count: lineChartCount,
    category_axis_count: categoryAxisCount,
    value_axis_count: valueAxisCount,
    series_count: seriesCount,
    x_value_binding_count: xValueBindingCount,
    y_value_binding_count: yValueBindingCount,
    x_value_formulas: xValueFormulas,
    x_bindings_match_mach_cells: xBindingsMatchMachCells,
    charts_use_numeric_mach_axis: chartsUseNumericMachAxis,
    charts_use_internal_cells: externalChartReferences.length === 0,
  };
}

const rows = bundle.rows ?? [];
const cyAlpha = bundle.cy_alpha ?? [];
const summaryByMach = new Map();
for (const row of rows) {
  const key = Number(row.Mach).toFixed(9);
  if (!summaryByMach.has(key)) summaryByMach.set(key, { mach: Number(row.Mach), points: [] });
  summaryByMach.get(key).points.push(row);
}
const machSummary = [...summaryByMach.values()].sort((left, right) => left.mach - right.mach);

try {

// Raw aerodynamic points.  The total is intentionally recalculated by Excel.
setTitle(
  adhSheet,
  "Гибридные аэродинамические коэффициенты",
  "Каждая строка хранит вклад решателя и полуэмпирики. Cx итог вычисляется формулой внутри книги.",
  "P",
);
const adhHeaders = [
  "M", "α, °", "Cy", "CY", "Cx давление/волна", "Cx индуктивное", "Cx вязкое",
  "Cx полуэмп.", "Cx итог (Excel)", "Cx итог (JSON)", "Разность", "Статус",
  "Невязка MachLine", "VSPAERO", "MachLine", "Parasite Drag",
];
adhSheet.getRange(`A4:P${4 + rows.length}`).values = [
  adhHeaders,
  ...rows.map(row => [
    row.Mach, row.alpha_deg, row.Cy, row.CY, row.Cx_pressure_wave, row.Cx_induced,
    row.Cx_viscous, row.Cx_semiempirical, null, row.Cx_total, null, row.status,
    row.quality?.machline_residual_norm ?? null, row.sources?.vspaero ?? "",
    row.sources?.machline ?? "", row.sources?.parasite_drag ?? "",
  ]),
];
const adhFirst = 5;
const adhLast = Math.max(adhFirst, adhFirst + rows.length - 1);
if (rows.length) {
  adhSheet.getRange(`I${adhFirst}`).formulas = [[`=IF(E${adhFirst}="","",SUM(E${adhFirst}:H${adhFirst}))`]];
  adhSheet.getRange(`I${adhFirst}:I${adhLast}`).fillDown();
  adhSheet.getRange(`K${adhFirst}`).formulas = [[`=IF(OR(I${adhFirst}="",J${adhFirst}=""),"",I${adhFirst}-J${adhFirst})`]];
  adhSheet.getRange(`K${adhFirst}:K${adhLast}`).fillDown();
  styleBody(adhSheet.getRange(`A${adhFirst}:P${adhLast}`));
  adhSheet.getRange(`A${adhFirst}:B${adhLast}`).format.numberFormat = "0.000";
  adhSheet.getRange(`C${adhFirst}:K${adhLast}`).format.numberFormat = "0.000000";
  adhSheet.getRange(`M${adhFirst}:M${adhLast}`).format.numberFormat = "0.00E+00";
  styleStatus(adhSheet.getRange(`L${adhFirst}:L${adhLast}`));
}
styleHeader(adhSheet.getRange("A4:P4"));
adhSheet.freezePanes.freezeRows(4);
adhSheet.getRange("A:B").format.columnWidth = 10;
adhSheet.getRange("C:K").format.columnWidth = 16;
adhSheet.getRange("L:L").format.columnWidth = 14;
adhSheet.getRange("M:M").format.columnWidth = 18;
adhSheet.getRange("N:P").format.columnWidth = 34;
adhSheet.getRange(`N${adhFirst}:P${adhLast}`).format.wrapText = true;

// Cy-alpha derivation with both direct and final values.
setTitle(
  cyaSheet,
  "Производная подъёмной силы Cyα",
  "Прямое значение определяется только по рассчитанным углам. Гибридный столбец содержит заранее объявленную методику.",
  "K",
);
const cyaHeaders = [
  "M", "α нач., °", "α кон., °", "Cy нач.", "Cy кон.", "Cyα прямой (Excel)",
  "Cyα итог", "Приращение методики", "Версия методики", "Статус", "Источник",
];
cyaSheet.getRange(`A4:K${4 + cyAlpha.length}`).values = [
  cyaHeaders,
  ...cyAlpha.map(row => [
    row.Mach, row.alpha_start_deg, row.alpha_end_deg, row.Cy_start, row.Cy_end, null,
    row.Cy_alpha_final_per_deg, null, row.method_version, row.status, row.source ?? "direct VSPAERO",
  ]),
];
const cyaFirst = 5;
const cyaLast = Math.max(cyaFirst, cyaFirst + cyAlpha.length - 1);
if (cyAlpha.length) {
  cyaSheet.getRange(`F${cyaFirst}`).formulas = [[`=(E${cyaFirst}-D${cyaFirst})/(C${cyaFirst}-B${cyaFirst})`]];
  cyaSheet.getRange(`F${cyaFirst}:F${cyaLast}`).fillDown();
  cyaSheet.getRange(`H${cyaFirst}`).formulas = [[`=G${cyaFirst}-F${cyaFirst}`]];
  cyaSheet.getRange(`H${cyaFirst}:H${cyaLast}`).fillDown();
  styleBody(cyaSheet.getRange(`A${cyaFirst}:K${cyaLast}`));
  cyaSheet.getRange(`A${cyaFirst}:C${cyaLast}`).format.numberFormat = "0.000";
  cyaSheet.getRange(`D${cyaFirst}:H${cyaLast}`).format.numberFormat = "0.000000";
  styleStatus(cyaSheet.getRange(`J${cyaFirst}:J${cyaLast}`));
}
styleHeader(cyaSheet.getRange("A4:K4"));
cyaSheet.freezePanes.freezeRows(4);
cyaSheet.getRange("A:C").format.columnWidth = 12;
cyaSheet.getRange("D:H").format.columnWidth = 18;
cyaSheet.getRange("I:I").format.columnWidth = 32;
cyaSheet.getRange("J:J").format.columnWidth = 14;
cyaSheet.getRange("K:K").format.columnWidth = 28;

// Summary table and native charts whose sources are cells in this workbook.
setTitle(
  summarySheet,
  "RepairMach 9.1 — автоматический гибридный расчёт",
  `Методика ${bundle.method_version}. Эталонные данные в расчёте не использовались. Статус: ${bundle.status}.`,
  "H",
);
const geometryCertificate = bundle.geometry_certificate ?? null;
summarySheet.getRange("A4:B10").values = [
  ["Показатель", "Значение"],
  ["Статус", bundle.status],
  ["Точек АДХ", bundle.summary?.points ?? 0],
  ["Полных точек", bundle.summary?.complete_points ?? 0],
  ["Неполных точек", bundle.summary?.incomplete_points ?? 0],
  ["Точек Cyα", bundle.summary?.cy_alpha_points ?? 0],
  ["Ошибок / предупреждений", `${bundle.summary?.errors ?? 0} / ${bundle.summary?.warnings ?? 0}`],
];
styleHeader(summarySheet.getRange("A4:B4"));
styleBody(summarySheet.getRange("A5:B10"));
styleStatus(summarySheet.getRange("B5:B5"));
for (const row of [11, 12, 13]) {
  summarySheet.mergeCells(`A${row}:C${row}`);
  summarySheet.mergeCells(`D${row}:H${row}`);
}
summarySheet.getRange("A11:A13").values = [
  ["Сертификат геометрии"],
  ["Вердикт геометрии"],
  ["Гибридная замена обязательна"],
];
summarySheet.getRange("D11:D13").values = [
  [geometryCertificate?.certificate_id ?? "не приложен"],
  [geometryCertificate?.verdict ?? "—"],
  [geometryCertificate?.flags?.hybrid_substitution_required ?? "—"],
];
styleHeader(summarySheet.getRange("A11:C13"));
styleBody(summarySheet.getRange("D11:H13"));

const summaryHeaders = ["M", "Cx0", "Cy(0°)", "Cy(1°)", "Cy(5°)", "Cyα прямой", "Cyα итог", "Статус"];
summarySheet.getRange(`A16:H${16 + machSummary.length}`).values = [summaryHeaders, ...machSummary.map(item => {
  const at = alpha => item.points.find(row => Math.abs(Number(row.alpha_deg) - alpha) <= 1e-9);
  const alpha0 = at(0);
  const cya = cyAlpha.find(row => Math.abs(Number(row.Mach) - item.mach) <= 1e-9);
  return [
    item.mach, alpha0?.Cx_total ?? null, alpha0?.Cy ?? null, at(1)?.Cy ?? null,
    at(5)?.Cy ?? null, cya?.Cy_alpha_direct_per_deg ?? null,
    cya?.Cy_alpha_final_per_deg ?? null,
    item.points.every(row => row.status === "complete") && cya?.status === "complete" ? "complete" : "incomplete",
  ];
})];
const summaryFirst = 17;
const summaryLast = Math.max(summaryFirst, summaryFirst + machSummary.length - 1);
styleHeader(summarySheet.getRange("A16:H16"));
if (machSummary.length) {
  styleBody(summarySheet.getRange(`A${summaryFirst}:H${summaryLast}`));
  summarySheet.getRange(`A${summaryFirst}:A${summaryLast}`).format.numberFormat = "0.000";
  summarySheet.getRange(`B${summaryFirst}:G${summaryLast}`).format.numberFormat = "0.000000";
  styleStatus(summarySheet.getRange(`H${summaryFirst}:H${summaryLast}`));
}
summarySheet.getRange("A:A").format.columnWidth = 14;
summarySheet.getRange("B:G").format.columnWidth = 17;
summarySheet.getRange("H:H").format.columnWidth = 15;
summarySheet.freezePanes.freezeRows(16);

let chartCount = 0;
let machAxisFormula = null;
if (machSummary.length) {
  machAxisFormula = `='Сводка'!$A$${summaryFirst}:$A$${summaryLast}`;
  addMachChart(summarySheet, summarySheet.getRange(`A16:B${summaryLast}`), "Cx0(M)", ["J4", "Q20"], ["#202020"], "Cx0", machAxisFormula);
  addMachChart(
    summarySheet,
    [
      summarySheet.getRange(`A16:A${summaryLast}`),
      summarySheet.getRange(`C16:C${summaryLast}`),
      summarySheet.getRange(`D16:D${summaryLast}`),
      summarySheet.getRange(`E16:E${summaryLast}`),
    ],
    "Cy(M) при фиксированных α",
    ["J22", "Q38"],
    ["#202020", "#666666", "#A6A6A6"],
    "Cy",
    machAxisFormula,
  );
  addMachChart(
    summarySheet,
    [
      summarySheet.getRange(`A16:A${summaryLast}`),
      summarySheet.getRange(`F16:F${summaryLast}`),
      summarySheet.getRange(`G16:G${summaryLast}`),
    ],
    "Cyα(M)",
    ["J40", "Q56"],
    ["#7F7F7F", "#202020"],
    "Cyα, 1/°",
    machAxisFormula,
  );
  chartCount = 3;
}

// Semiempirical terms are flattened instead of hidden inside a total.
setTitle(
  semiSheet,
  "Полуэмпирические добавки",
  "Вклад применяется только в объявленной области Mach. Изменение таблицы методики требует нового слепого пакета.",
  "G",
);
const semiRows = [];
for (const row of rows) {
  for (const term of row.semiempirical_terms ?? []) {
    semiRows.push([row.Mach, row.alpha_deg, term.id, term.label, term.cd, term.model, term.provenance]);
  }
}
semiSheet.getRange(`A4:G${4 + Math.max(1, semiRows.length)}`).values = [
  ["M", "α, °", "ID", "Элемент", "ΔCx", "Модель", "Источник методики"],
  ...(semiRows.length ? semiRows : [[null, null, "—", "Активные добавки отсутствуют", 0, "—", "config/hybrid_method.json"]]),
];
styleHeader(semiSheet.getRange("A4:G4"));
styleBody(semiSheet.getRange(`A5:G${4 + Math.max(1, semiRows.length)}`));
semiSheet.getRange(`A5:B${4 + Math.max(1, semiRows.length)}`).format.numberFormat = "0.000";
semiSheet.getRange(`E5:E${4 + Math.max(1, semiRows.length)}`).format.numberFormat = "0.000000";
semiSheet.getRange("A:B").format.columnWidth = 10;
semiSheet.getRange("C:C").format.columnWidth = 22;
semiSheet.getRange("D:D").format.columnWidth = 34;
semiSheet.getRange("E:F").format.columnWidth = 16;
semiSheet.getRange("G:G").format.columnWidth = 62;
semiSheet.getRange("G:G").format.wrapText = true;
semiSheet.freezePanes.freezeRows(4);

// Source fingerprints make the workbook independently auditable.
setTitle(sourceSheet, "Источники расчёта", "Имена и SHA-256 исходных результатов; внешних ссылок Excel нет.", "D");
sourceSheet.getRange(`A4:D${4 + Math.max(1, bundle.sources?.length ?? 0)}`).values = [
  ["Роль", "Файл", "Размер, байт", "SHA-256"],
  ...((bundle.sources?.length ?? 0) ? bundle.sources.map(item => [item.role, item.file_name, item.size_bytes, item.sha256]) : [["—", "—", 0, "—"]]),
];
styleHeader(sourceSheet.getRange("A4:D4"));
styleBody(sourceSheet.getRange(`A5:D${4 + Math.max(1, bundle.sources?.length ?? 0)}`));
sourceSheet.getRange("A:A").format.columnWidth = 28;
sourceSheet.getRange("B:B").format.columnWidth = 42;
sourceSheet.getRange("C:C").format.columnWidth = 16;
sourceSheet.getRange("D:D").format.columnWidth = 70;
sourceSheet.freezePanes.freezeRows(4);

// The complete policy is embedded as ordinary cells, not as an external file link.
setTitle(methodSheet, "Методика автоматического гибридного расчёта", "Параметры, зафиксированные до сопоставления с независимым эталоном.", "B");
const policyRows = [
  ["Параметр", "Значение"],
  ["Версия", bundle.method_version],
  ["Эталонные данные использованы", bundle.reference_data_used],
  ["Поточечная подстройка", bundle.pointwise_tuning_used],
  ["Давление/волна", bundle.policy?.drag?.pressure_wave_source ?? ""],
  ["Индуктивное сопротивление", bundle.policy?.drag?.induced_source ?? ""],
  ["Вязкое сопротивление", bundle.policy?.drag?.parasite_source ?? ""],
  ["Метод Cyα", bundle.policy?.lift?.cy_alpha?.method ?? ""],
  ["Исключённый транзвуковой интервал", JSON.stringify(bundle.policy?.transonic_excluded ?? [])],
  ["ID сертификата геометрии", geometryCertificate?.certificate_id ?? "не приложен"],
  ["SHA-256 MASTER", geometryCertificate?.master_sha256 ?? "—"],
  ["Вердикт сертификации", geometryCertificate?.verdict ?? "—"],
  ["Обязательная гибридная замена", geometryCertificate?.flags?.hybrid_substitution_required ?? "—"],
  ["Полная политика", "Ниже — все поля зафиксированной конфигурации"],
  ...flattenObject(bundle.policy ?? {}).map(([key, value]) => [`policy.${key}`, value]),
  ...flattenObject(geometryCertificate ?? {}).map(([key, value]) => [`geometry_certificate.${key}`, value]),
];
methodSheet.getRange(`A4:B${3 + policyRows.length}`).values = policyRows;
styleHeader(methodSheet.getRange("A4:B4"));
styleBody(methodSheet.getRange(`A5:B${3 + policyRows.length}`));
methodSheet.getRange("A:A").format.columnWidth = 38;
methodSheet.getRange("B:B").format.columnWidth = 62;
methodSheet.getRange("B:B").format.wrapText = true;
methodSheet.freezePanes.freezeRows(4);

workbook.recalculate();
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
const formulaInspection = await workbook.inspect({
  kind: "formula",
  include: "sheet,address,formula,formulas",
  maxChars: 200000,
  options: { maxResults: 10000 },
});
const summaryInspection = await workbook.inspect({
  kind: "table",
  range: `Сводка!A1:H${summaryLast}`,
  include: "values,formulas",
  tableMaxRows: Math.min(summaryLast, 100),
  tableMaxCols: 8,
});
const drawings = await workbook.inspect({ kind: "drawing", include: "sheet,name,type", maxChars: 8000 });
const formulaErrorFindings = matchedInspectionRecords(formulaErrors.ndjson);
const externalFormulaRefs = externalFormulaReferences(formulaInspection.ndjson);
if (formulaErrorFindings.length || externalFormulaRefs.length) {
  throw new Error(
    formulaErrorFindings.length
      ? `Excel содержит формульные ошибки (${formulaErrorFindings.length}); файл не опубликован`
      : `Excel содержит внешние формульные ссылки (${externalFormulaRefs.length}); файл не опубликован`,
  );
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(stagedOutputPath);
injectTestFailure("after_export_save");
await fs.rm(stagedOutputInspectionPath, { force: true });
if (chartCount) await bindScatterChartsToMachCells(stagedOutputPath, machAxisFormula);
injectTestFailure("after_chart_binding");
const packageAudit = inspectXlsxPackage(await fs.readFile(stagedOutputPath), chartCount, machAxisFormula);
const externalLinks = [
  ...packageAudit.external_link_parts,
  ...packageAudit.external_chart_references,
];
if (externalLinks.length || !packageAudit.charts_use_numeric_mach_axis || !packageAudit.charts_use_internal_cells) {
  if (externalLinks.length) {
    throw new Error(`В Excel обнаружены внешние ссылки (${externalLinks.length}); файл не опубликован`);
  }
  throw new Error("Диаграммы Mach не прошли проверку числовой X-оси; файл не опубликован");
}

await fs.mkdir(stagedPreviewsDir, { recursive: true });
const previewFiles = [];
const previewDetails = [];
const finalWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(stagedOutputPath));
injectTestFailure("after_workbook_import");
for (const sheet of finalWorkbook.worksheets.items) {
  const image = await finalWorkbook.render({ sheetName: sheet.name, autoCrop: "all", scale: 1, format: "png" });
  const fileName = `${sheet.name.replace(/[^A-Za-zА-Яа-я0-9_-]+/g, "_")}.png`;
  const imageBytes = new Uint8Array(await image.arrayBuffer());
  if (!imageBytes.byteLength) throw new Error(`Пустой PNG предпросмотра: ${sheet.name}`);
  await fs.writeFile(path.join(stagedPreviewsDir, fileName), imageBytes);
  previewFiles.push(fileName);
  previewDetails.push({ sheet: sheet.name, file: fileName, size_bytes: imageBytes.byteLength });
  injectTestFailure("after_first_preview_write");
}

const verification = {
  schema: "repairmach.hybrid-workbook-verification/1.0",
  build_status: "passed",
  workbook: path.basename(outputPath),
  sheets: workbook.worksheets.items.map(sheet => sheet.name),
  chart_count: chartCount,
  chart_type: "scatter",
  charts_use_numeric_mach_axis: packageAudit.charts_use_numeric_mach_axis,
  charts_use_internal_cells: packageAudit.charts_use_internal_cells,
  external_links_detected: false,
  external_link_parts: packageAudit.external_link_parts,
  external_chart_references: packageAudit.external_chart_references,
  chart_package_audit: packageAudit,
  formula_error_count: 0,
  formula_errors: formulaErrors.ndjson,
  summary: summaryInspection.ndjson,
  drawings: drawings.ndjson,
  previews: previewFiles,
  previews_source: "final_exported_xlsx",
  preview_details: previewDetails,
};
await fs.writeFile(stagedVerificationPath, JSON.stringify(verification, null, 2), "utf8");
// Publish only after every verification step has succeeded.  The verification
// file is renamed last and acts as the commit marker for consumers.
await fs.rename(stagedOutputPath, outputPath);
await fs.rename(stagedPreviewsDir, previewsDir);
await fs.rename(stagedVerificationPath, verificationPath);
console.log(JSON.stringify({ output: outputPath, status: bundle.status, sheets: verification.sheets, previews: previewFiles }, null, 2));
} catch (error) {
  await rollbackPublication();
  throw error;
}
