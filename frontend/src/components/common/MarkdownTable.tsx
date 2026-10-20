/**
 * 共享 markdown 表格渲染：解析预览 / 切分预览 / 原文面板统一视觉。
 *
 * 解析 markdown 表格为 <table>（表头 emerald 底），非标准表格退化等宽文本；
 * 超长表格只渲染前 MAX_TABLE_ROWS 行并提示。
 */

import { useMemo } from "react";

/** 表格最多直接渲染的行数 */
export const MAX_TABLE_ROWS = 50;

/** 解析 markdown 表格为 headers/rows；非标准表格返回 null。 */
export function parseMarkdownTable(
  md: string
): { headers: string[]; rows: string[][] } | null {
  const lines = md
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.includes("|"));
  if (lines.length < 2) return null;
  const splitRow = (line: string) => {
    const stripped = line.replace(/^\|/, "").replace(/\|$/, "");
    return stripped.split("|").map((c) => c.trim());
  };
  if (!/^\|[\s:|-]+\|$/.test(lines[1])) return null;
  return { headers: splitRow(lines[0]), rows: lines.slice(2).map(splitRow) };
}

/** 表格体：markdown → <table>，超长截断；非标准表格退化等宽文本。 */
export function MarkdownTable({ markdown }: { markdown: string }) {
  const parsed = useMemo(() => parseMarkdownTable(markdown), [markdown]);
  if (!parsed) {
    return (
      <pre className="max-h-64 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-slate-600">
        {markdown}
      </pre>
    );
  }
  const { headers, rows } = parsed;
  const shown = rows.slice(0, MAX_TABLE_ROWS);
  return (
    <div className="overflow-x-auto rounded-md border border-gray-200">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-emerald-50/60">
            {headers.map((h, i) => (
              <th
                key={i}
                className="border-b border-gray-200 px-2.5 py-1.5 text-left font-medium text-slate-600"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i} className="odd:bg-white even:bg-gray-50/60">
              {row.map((cell, j) => (
                <td key={j} className="px-2.5 py-1.5 text-slate-600">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > MAX_TABLE_ROWS && (
        <p className="border-t border-gray-100 bg-gray-50 px-2.5 py-1 text-[11px] text-slate-400">
          仅渲染前 {MAX_TABLE_ROWS} 行，共 {rows.length} 行
        </p>
      )}
    </div>
  );
}
