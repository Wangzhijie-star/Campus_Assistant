// Offline integration check: requires the artifact-tool runtime, no SQLBot calls.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';

const { FileBlob, SpreadsheetFile } = await import(process.env.SQLBOT_ARTIFACT_MODULE
  ? pathToFileURL(path.resolve(process.env.SQLBOT_ARTIFACT_MODULE)).href : '@oai/artifact-tool');
const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), '.output');
const input = path.join(dir, 'scorecard-v2.xlsx');
const base = path.join(dir, 'manual-base.xlsx');
const output = path.join(dir, 'manual-preserved.xlsx');
const book = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const sheet = book.worksheets.getItem('业务效果');
sheet.getRange('F3:G3').values = [[88, 90]];
sheet.getRange('I3:J3').values = [[81, 85]];
sheet.getRange('L3:M3').values = [[70, 80]];
await (await SpreadsheetFile.exportXlsx(book)).save(base);
execFileSync(process.execPath, [path.join(dir, '..', '..', 'export_scorecard.mjs'), path.join(dir,'plan.json'), output, base],
  {env:{...process.env, SQLBOT_EVAL_PREVIEW:''}, stdio:'pipe'});
const result = await SpreadsheetFile.importXlsx(await FileBlob.load(output));
const values = result.worksheets.getItem('业务效果');
assert.deepEqual(values.getRange('F3:G3').values, [[88,90]]);
assert.deepEqual(values.getRange('I3:J3').values, [[81,85]]);
assert.deepEqual(values.getRange('L3:M3').values, [[70,80]]);
assert.deepEqual(values.getRange('E3').values, [[75]]);
console.log('PASS: existing human/adjudication values preserved; AI cells updated');
