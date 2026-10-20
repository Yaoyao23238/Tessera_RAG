/**
 * 切分预览「LLM 辅助」弹窗：边界合并增强三档（关/保守/标准）。
 *
 * LLM 审查相邻 chunk 边界判断是否合并并生成摘要前缀增强检索。
 * 档位选择即写入 useLLMAssistStore（localStorage 持久化），
 * 点「重新切分」时随切分参数提交后端。开启后不走切分缓存。
 */

import { Sparkles } from "lucide-react";
import {
  useLLMAssistStore,
  type ChunkLlmMode,
} from "../../stores/useLLMAssistStore";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";

const MODE_OPTIONS: { value: ChunkLlmMode; label: string; desc: string }[] = [
  { value: "off", label: "关闭", desc: "纯规则切分，不调用 LLM" },
  {
    value: "conservative",
    label: "保守",
    desc: "仅合并高置信边界（置信度 ≥ 0.75），改动最少",
  },
  {
    value: "standard",
    label: "标准",
    desc: "LLM 判定可合并即合并，并生成摘要前缀增强检索",
  },
];

export function ChunkLLMDialog({
  chunkCount,
  open,
  onOpenChange,
}: {
  /** 当前 chunk 数（用于 token 粗估）；未加载时省略预估 */
  chunkCount?: number;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const mode = useLLMAssistStore((s) => s.chunkLlmMode);
  const setMode = useLLMAssistStore((s) => s.setChunkLlmMode);

  // 粗估：每边界一次调用，前后各 200 字上下文 + prompt ≈ 500 输入 tokens，
  // 边界数不超过 chunk 数的一半（仅同 section 相邻 text 块参与）
  const estimated =
    mode !== "off" && chunkCount ? Math.round(chunkCount * 0.5 * 500) : 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogTitle className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-violet-500" />
          LLM 辅助切分
        </DialogTitle>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          LLM 审查相邻块的衔接处，判断语义连续是否合并，并为合并块生成摘要前缀。
          选择即时保存，下次「重新切分」时生效。开启后不走切分缓存。
        </p>

        <div className="mt-4 space-y-1.5">
          {MODE_OPTIONS.map((opt) => {
            const active = mode === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => setMode(opt.value)}
                className={cn(
                  "flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
                  active
                    ? "border-slate-900 bg-slate-50 ring-1 ring-slate-900"
                    : "border-gray-200 bg-white hover:bg-gray-50"
                )}
              >
                <span>
                  <span className="text-sm font-medium text-slate-800">
                    {opt.label}
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-400">
                    {opt.desc}
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        {mode !== "off" && (
          <div className="mt-4 flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs">
            <span className="text-slate-500">预估输入 token（按当前块数粗估）</span>
            <span className="font-medium text-slate-800">
              {chunkCount ? `约 ${estimated.toLocaleString()}` : "估算中…"}
            </span>
          </div>
        )}

        <div className="mt-4 flex justify-end">
          <Button size="sm" onClick={() => onOpenChange(false)}>
            完成
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
