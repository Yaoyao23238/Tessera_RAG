/**
 * 文档管理页：拖拽上传 + 文档列表 + 批量操作 + 状态筛选。
 */

import { useState } from "react";
import { useDocuments } from "../hooks/useDocuments";
import { UploadZone } from "../components/documents/UploadZone";
import { UrlImport } from "../components/documents/UrlImport";
import { DocumentTable } from "../components/documents/DocumentTable";
import { BatchToolbar } from "../components/documents/BatchToolbar";
import { EmptyState, ErrorState } from "../components/common/States";
import { ListSkeleton } from "../components/ui/skeleton";
import { friendlyError } from "../lib/api";
import { DOC_STATUS_META, DOC_STATUS, type DocStatus } from "../lib/constants";
import { cn } from "../lib/utils";

export default function DocumentsPage() {
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const { data, isLoading, error, refetch } = useDocuments(1, 100, statusFilter || undefined);

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6 thin-scroll">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">文档管理</h1>
        <span className="text-sm text-slate-400">
          共 {data?.total ?? 0} 个文档
        </span>
      </div>

      <UploadZone />

      {/* URL 导入 */}
      <UrlImport />

      {/* 状态筛选 */}
      <div className="flex flex-wrap items-center gap-1.5">
        <FilterChip
          active={statusFilter === ""}
          label="全部"
          onClick={() => setStatusFilter("")}
        />
        {DOC_STATUS.map((s) => (
          <FilterChip
            key={s}
            active={statusFilter === s}
            label={DOC_STATUS_META[s as DocStatus].label}
            color={DOC_STATUS_META[s as DocStatus].color}
            onClick={() => setStatusFilter(statusFilter === s ? "" : s)}
          />
        ))}
      </div>

      <BatchToolbar
        selectedIds={selectedIds}
        onClear={() => setSelectedIds([])}
      />

      {isLoading ? (
        <ListSkeleton rows={4} />
      ) : error ? (
        <ErrorState message={friendlyError(error)} onRetry={() => refetch()} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title={statusFilter ? "该状态下暂无文档" : "还没有文档"}
          hint="拖拽 PDF / DOCX / PPTX / MD / TXT / HTML 到上方区域开始摄入"
        />
      ) : (
        <DocumentTable
          documents={data.items}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
        />
      )}
    </div>
  );
}

function FilterChip({
  active,
  label,
  color,
  onClick,
}: {
  active: boolean;
  label: string;
  color?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
        active
          ? "border-slate-900 bg-slate-900 text-white"
          : "border-gray-200 bg-white text-slate-600 hover:border-gray-300"
      )}
    >
      {color && (
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: active ? "#fff" : color }}
        />
      )}
      {label}
    </button>
  );
}
