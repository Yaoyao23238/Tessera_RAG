/**
 * 运行实验对话框：选择测试集 + 参与文档（多选）+ 切分配置 + 实验名。
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { useDocuments } from "../../hooks/useDocuments";
import { DEFAULT_CHUNKING } from "../../stores/usePreviewStore";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { Button } from "../ui/button";
import { Input, FieldLabel } from "../ui/input";
import { Checkbox } from "../ui/checkbox";
import { Switch } from "../ui/switch";
import { Slider } from "../ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import type { ChunkingConfig, Experiment, QASet } from "../../types";

export function RunExperimentDialog({
  qasets,
  open,
  onOpenChange,
}: {
  qasets: QASet[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [qaSetId, setQaSetId] = useState("");
  const [docIds, setDocIds] = useState<string[]>([]);
  const [config, setConfig] = useState<ChunkingConfig>(DEFAULT_CHUNKING);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const { data: docsData } = useDocuments(1, 200);
  const candidates = (docsData?.items ?? []).filter((d) =>
    ["parsed", "chunked", "indexed"].includes(d.status)
  );

  const mutation = useMutation({
    mutationFn: () =>
      api.post<Experiment>("/evaluation/experiments", {
        qaSetId,
        documentIds: docIds,
        chunkingConfig: config,
        name: name || null,
      }),
    onSuccess: (exp) => {
      pushToast({
        kind: "info",
        title: `实验「${exp.name}」已开始运行`,
        message: "进度经 WebSocket 推送",
      });
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      onOpenChange(false);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "创建实验失败", message: friendlyError(err) }),
  });

  function toggleDoc(id: string) {
    setDocIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogTitle>运行评测实验</DialogTitle>
        <p className="mt-1 text-xs text-slate-400">
          将按所选切分配置在独立索引命名空间（exp_xxx 表）重切并向量化，不影响主库
        </p>

        <div className="mt-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <FieldLabel required>测试集</FieldLabel>
              <Select value={qaSetId} onValueChange={setQaSetId}>
                <SelectTrigger>
                  <SelectValue placeholder="选择测试集" />
                </SelectTrigger>
                <SelectContent>
                  {qasets.map((qs) => (
                    <SelectItem key={qs.id} value={qs.id}>
                      {qs.name}（{qs.itemCount} 条）
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <FieldLabel>实验名</FieldLabel>
              <Input
                placeholder="留空自动命名 exp_时间戳"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>

          <div>
            <FieldLabel required>参与文档（{docIds.length}）</FieldLabel>
            <div className="max-h-36 space-y-1.5 overflow-y-auto rounded-lg border border-gray-100 p-3 thin-scroll">
              {candidates.length === 0 && (
                <p className="text-xs text-slate-400">暂无已解析文档</p>
              )}
              {candidates.map((d) => (
                <label
                  key={d.id}
                  className="flex cursor-pointer items-center gap-2 text-sm text-slate-700"
                >
                  <Checkbox
                    checked={docIds.includes(d.id)}
                    onCheckedChange={() => toggleDoc(d.id)}
                  />
                  <span className="truncate">{d.filename}</span>
                </label>
              ))}
            </div>
          </div>

          {/* 切分配置（精简版：核心参数） */}
          <div className="rounded-lg border border-gray-100 p-3">
            <p className="mb-3 text-xs font-medium text-slate-600">切分配置</p>
            <div className="grid grid-cols-2 gap-x-6 gap-y-3">
              <div>
                <div className="mb-1 flex justify-between text-xs text-slate-500">
                  <span>chunk_size</span>
                  <span className="tabular-nums">{config.chunkSize}</span>
                </div>
                <Slider
                  value={[config.chunkSize]}
                  min={128}
                  max={2048}
                  step={64}
                  onValueChange={([v]) => setConfig({ ...config, chunkSize: v })}
                />
              </div>
              <div>
                <div className="mb-1 flex justify-between text-xs text-slate-500">
                  <span>overlap</span>
                  <span className="tabular-nums">{config.chunkOverlap}</span>
                </div>
                <Slider
                  value={[config.chunkOverlap]}
                  min={0}
                  max={256}
                  step={10}
                  onValueChange={([v]) => setConfig({ ...config, chunkOverlap: v })}
                />
              </div>
              <label className="flex items-center justify-between text-xs text-slate-600">
                语义切分
                <Switch
                  checked={config.enableSemantic}
                  onCheckedChange={(v) => setConfig({ ...config, enableSemantic: v })}
                />
              </label>
              <label className="flex items-center justify-between text-xs text-slate-600">
                Parent-Child
                <Switch
                  checked={config.enableParentChild}
                  onCheckedChange={(v) => setConfig({ ...config, enableParentChild: v })}
                />
              </label>
              <label className="flex items-center justify-between text-xs text-slate-600">
                LLM 辅助
                <Switch
                  checked={config.enableLlm}
                  onCheckedChange={(v) => setConfig({ ...config, enableLlm: v })}
                />
              </label>
            </div>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!qaSetId || docIds.length === 0 || mutation.isPending}
          >
            {mutation.isPending ? "创建中…" : "运行实验"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
