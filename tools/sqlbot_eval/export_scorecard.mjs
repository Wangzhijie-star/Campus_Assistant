// Run in an environment with @oai/artifact-tool. Never changes the input workbook.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [planPath, outputPath, basePath] = process.argv.slice(2);
if (!planPath || !outputPath) throw new Error('Usage: node export_scorecard.mjs PLAN OUTPUT [BASE]');
const moduleName = process.env.SQLBOT_ARTIFACT_MODULE;
const { FileBlob, SpreadsheetFile } = await import(moduleName ? pathToFileURL(path.resolve(moduleName)).href : '@oai/artifact-tool');
const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
const inputPath = basePath || plan.template;
if (path.resolve(inputPath) === path.resolve(outputPath)) throw new Error('输出不能覆盖输入模板或已有成绩单');
try { await fs.access(outputPath); throw new Error('输出文件已存在，请选择新文件名'); }
catch (e) { if (e.code !== 'ENOENT') throw e; }
const book = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheets = Object.fromEntries(['业务效果', '执行质量', '效率'].map(name => [name, book.worksheets.getItem(name)]));
for (const sheet of Object.values(sheets)) {
  const headers = sheet.getRange('A2:C2').values[0];
  if (JSON.stringify(headers) !== JSON.stringify(['批次', '问题序号', '运行序号'])) throw new Error('模板表头不匹配');
}
const text = value => value == null ? null : String(value);
// Neutralize spreadsheet formula injection in user/model-originated values.
const safe = value => typeof value === 'string' && /^[=+@-]/.test(value) ? "'" + value : value;
const put = (sheet, address, value) => sheet.getRange(address).values = [[safe(value)]];
function rowFor(sheet, item) {
  const values = sheet.getRange('A3:C202').values;
  const matches = [];
  let empty = null;
  for (let i = 0; i < values.length; i++) {
    const [batch, qid, run] = values[i];
    if (batch === item.batch_id && qid === item.question_id && Number(run) === item.run_index) matches.push(i + 3);
    if (empty === null && !batch && !qid && !run) empty = i + 3;
  }
  if (matches.length > 1) throw new Error('成绩单存在重复身份行');
  if (matches.length) return matches[0];
  if (empty === null) throw new Error('模板数据行容量不足');
  return empty;
}
for (const item of plan.rows) {
  const score = item.score;
  for (const [name, sheet] of Object.entries(sheets)) {
    const row = rowFor(sheet, item);
    sheet.getRange(`A${row}:C${row}`).values = [[item.batch_id, item.question_id, item.run_index]];
    const rowRange = sheet.getRange(`A${row}:${name === '执行质量' ? 'S' : name === '业务效果' ? 'Q' : 'O'}${row}`);
    rowRange.format.wrapText = true;
    rowRange.format.verticalAlignment = 'top';
    rowRange.format.rowHeight = name === '效率' ? 48 : 110;
    const cell = (col, value) => put(sheet, `${col}${row}`, value);
    const dim = code => score?.dimensions?.[code]?.score ?? null;
    if (name === '业务效果') {
      cell('D', item.question);
      if (score) {
        cell('E', dim('correctness')); cell('H', dim('completion')); cell('K', dim('verifiability'));
        const warning = plan.warnings?.[`${item.question_id}/${item.run_id}`];
        cell('P', (warning ? `导入提示：${warning}\n` : '') + Object.entries(score.dimensions).map(([k,v]) => `${k}: ${v.reason}`).join('\n'));
        // F/G, I/J, L/M, O/Q are human/adjudication columns and are never touched.
      }
    } else if (name === '执行质量') {
      cell('E', item.status === 'success' ? '是' : item.status === 'failed' ? '否' : '待确认');
      if (score) {
        cell('G', dim('sql_execution')); cell('L', dim('information_recall')); cell('P', dim('error_handling'));
        cell('K', score.dimensions.information_recall?.reason ?? null);
        cell('O', score.dimensions.error_handling?.reason ?? null);
      }
      cell('N', item.trial?.execution?.error ? text(item.trial.execution.error) : item.client_error);
      cell('R', '安全校验需依据独立证据，不从执行成功推断'); cell('S', item.evidence_path);
      if (plan.na_policy === 'exclude' && score?.dimensions?.error_handling?.status === 'not_applicable') {
        sheet.getRange(`Q${row}`).formulas = [[`=IF(COUNT(G${row},L${row})=2,ROUND((G${row}*40%+L${row}*40%)/80%,2),"")`]];
      }
    } else {
      cell('F', item.trial?.metrics?.server_main_s ?? null);
      cell('M', item.status); cell('N', item.collection_status === 'success' ? '已采集，完整性见警告' : item.collection_status);
      cell('O', '时间单位秒；首次响应、修复次数等未测量则留空。token原值见证据文件。');
    }
  }
}
book.recalculate();
const file = await SpreadsheetFile.exportXlsx(book);
await file.save(outputPath);
if (process.env.SQLBOT_EVAL_PREVIEW) {
  const preview = await book.render({sheetName:'业务效果', range:'A1:Q4', scale:1, format:'png'});
  await fs.writeFile(process.env.SQLBOT_EVAL_PREVIEW, new Uint8Array(await preview.arrayBuffer()));
}
console.log(outputPath);
