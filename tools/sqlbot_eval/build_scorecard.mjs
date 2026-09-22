import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "D:/SQLBot-main/项目学习文档/数据集和测试问题集/Text2SQL评分表模板.xlsx";
const outputDir = "C:/Users/Lenovo/.codex/visualizations/2026/09/17/01a0ae38-f1be-78c3-b613-04e0348b9d01/sqlbot-eval-simulation-001";
const outputPath = `${outputDir}/Text2SQL评分表_simulation_001.xlsx`;
const previewDir = `${outputDir}/previews`;

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));

const business = workbook.worksheets.getItem("业务效果");
business.getRange("A3:E3").values = [[
  "simulation_001", "S001", 1,
  "软件2404班获得一等奖学金的是谁？", 100,
]];
business.getRange("H3").values = [[100]];
business.getRange("K3").values = [[100]];
business.getRange("O3:Q3").values = [[
  "是",
  "无业务效果扣分；信息召回噪声在执行质量表记录。",
  "人工评分与裁定分待填写。",
]];
business.getRange("P3:Q3").format.wrapText = true;
business.getRange("A3:Q3").format.rowHeight = 34;

const execution = workbook.worksheets.getItem("执行质量");
execution.getRange("A3:G3").values = [[
  "simulation_001", "S001", 1, "是", "是", 1, 100,
]];
execution.getRange("H3:L3").values = [[
  "误召回：加权成绩、加权学分成绩、GPA专业排名、专业绩点排名、班级加权排名、加权班级排名、平均学分绩点、GPA",
  "数据源ID 8（会话固定，未触发数据源选择日志）",
  "excel_Sheet1_75da3c529fca59608c72",
  "班级、姓名、学号、奖学金等级均覆盖",
  70,
]];
execution.getRange("M3:P3").values = [["无", "无", "不适用", null]];
execution.getRange("Q3").formulas = [["=IF(COUNT(G3,L3)=2,ROUND((G3*40%+L3*40%)/80%,2),\"\")"]];
execution.getRange("R3:S3").values = [[
  "未独立采集；SQL已通过系统流程并成功执行",
  "trials/S001.json；review_inputs/S001.json；ai_reviews/S001.json",
]];
execution.getRange("H3:S3").format.wrapText = true;
execution.getRange("A3:S3").format.rowHeight = 48;

const efficiency = workbook.worksheets.getItem("效率");
efficiency.getRange("A3:H3").values = [[
  "simulation_001", "S001", 1, 2.78, 2.85, 3.98, 9638, 269,
]];
efficiency.getRange("J3:O3").values = [[
  3, 0, 0, "成功", "是",
  "时间单位为秒；token包含SQL、图表和推荐问题调用。主流程耗时不含异步推荐问题。",
]];
efficiency.getRange("D3:F3").format.numberFormat = "0.00\"秒\"";
efficiency.getRange("G3:J3").format.numberFormat = "#,##0";
efficiency.getRange("O3").format.wrapText = true;
efficiency.getRange("A3:O3").format.rowHeight = 36;

for (const sheet of [business, execution, efficiency]) {
  sheet.getRange("A3:" + (sheet.name === "执行质量" ? "S3" : sheet.name === "业务效果" ? "Q3" : "O3")).format.verticalAlignment = "center";
}

workbook.recalculate();
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

for (const [name, range] of [["业务效果", "A1:Q5"], ["执行质量", "A1:S5"], ["效率", "A1:O5"]]) {
  const preview = await workbook.render({ sheetName: name, range, scale: 1.5, format: "png" });
  await fs.writeFile(`${previewDir}/${name}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const check = await workbook.inspect({
  kind: "table",
  range: "业务效果!A1:Q3",
  include: "values,formulas",
  tableMaxRows: 5,
  tableMaxCols: 20,
});
console.log(check.ndjson);
const executionCheck = await workbook.inspect({
  kind: "table",
  range: "执行质量!A1:S3",
  include: "values,formulas",
  tableMaxRows: 5,
  tableMaxCols: 22,
});
console.log(executionCheck.ndjson);
const efficiencyCheck = await workbook.inspect({
  kind: "table",
  range: "效率!A1:O3",
  include: "values,formulas",
  tableMaxRows: 5,
  tableMaxCols: 18,
});
console.log(efficiencyCheck.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
