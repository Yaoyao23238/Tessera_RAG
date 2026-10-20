/**
 * 检索范围筛选器（PRD P0-8）：按文档 / chunk 类型过滤检索结果。
 * 文档列表只列「已入库」——检索对象是向量库内容，未入库文档选了也没结果。
 */

import { useState } from "react";
import { Filter, X } from "lucide-react";
import { useDocuments } from "../../hooks/useDocuments";
import { CHUNK_TYPES, CHUNK_TYPE_META } from "../../lib/constants";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { FieldLabel } from "../ui/input";

export function SearchFilters({
  docIds,
  chunkTypes,
  onDocIdsChange,
  onChunkTypesChange,
}: {
  docIds: string[];
  chunkTypes: string[];
  onDocIdsChange: (v: string[]) => void;
  onChunkTypesChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const { data: docsData } = useDocuments(1, 200);
  const indexed = (docsData?.items ?? []).filter((d) => d.status === "indexed");
  const activeCount = docIds.length + chunkTypes.length;

  function toggle(list: string[], value: string, setter: (v: string[]) => void) {
    setter(
      list.includes(value)
        ? list.filter((x) => x !== value)
        : [...list, value]
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-9"
          onClick={() => setOpen((v) => !v)}
        >
          <Filter className="mr-1 h-3.5 w-3.5" />
          筛选{activeCount > 0 ? `（${activeCount}）` : ""}
        </Button>
        {activeCount > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="h-9 text-slate-500"
            onClick={() => {
              onDocIdsChange([]);
              onChunkTypesChange([]);
            }}
          >
            <X className="mr-1 h-3.5 w-3.5" />
            清空筛选
          </Button>
        )}
      </div>

      {open && (
        <div className="mt-2 grid grid-cols-2 gap-4 rounded-lg border border-gray-100 bg-white p-3 shadow-sm">
          <div>
            <FieldLabel>文档范围（不选 = 全部）</FieldLabel>
            <div className="max-h-36 space-y-1.5 overflow-y-auto rounded-lg border border-gray-50 p-2 thin-scroll">
              {indexed.length === 0 ? (
                <p className="text-xs text-slate-400">暂无已入库文档</p>
              ) : (
                indexed.map((d) => (
                  <label
                    key={d.id}
                    className="flex cursor-pointer items-center gap-2 text-sm text-slate-700"
                  >
                    <Checkbox
                      checked={docIds.includes(d.id)}
                      onCheckedChange={() => toggle(docIds, d.id, onDocIdsChange)}
                    />
                    <span className="truncate">{d.filename}</span>
                  </label>
                ))
              )}
            </div>
          </div>
          <div>
            <FieldLabel>内容类型（不选 = 全部）</FieldLabel>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5 pt-1">
              {CHUNK_TYPES.map((t) => (
                <label
                  key={t}
                  className="flex cursor-pointer items-center gap-1.5 text-sm text-slate-700"
                >
                  <Checkbox
                    checked={chunkTypes.includes(t)}
                    onCheckedChange={() =>
                      toggle(chunkTypes, t, onChunkTypesChange)
                    }
                  />
                  <span>{CHUNK_TYPE_META[t].label}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
