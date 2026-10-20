/**
 * 切分参数面板：chunk_size/overlap 滑块、语义/Parent-Child 开关、
 * 表格处理下拉。参数改动不自动重切，由父组件的「重新切分」触发。
 * 展开/折叠受父组件控制（顶栏「切分参数」按钮），默认折叠。
 * LLM 辅助（边界合并增强）已从本面板脱离，入口在顶栏「LLM 辅助」弹窗。
 */

import { ChevronDown, ChevronUp, SlidersHorizontal } from "lucide-react";
import { usePreviewStore } from "../../stores/usePreviewStore";
import { Slider } from "../ui/slider";
import { Switch } from "../ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { TABLE_HANDLING_OPTIONS } from "../../lib/constants";
import { cn } from "../../lib/utils";

export function ParamPanel({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  const config = usePreviewStore((s) => s.config);
  const setConfig = usePreviewStore((s) => s.setConfig);

  return (
    <div className="rounded-xl border border-gray-200 bg-white">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-4 py-2.5 text-sm font-medium text-slate-700"
        onClick={onToggle}
      >
        <SlidersHorizontal className="h-4 w-4 text-slate-400" />
        切分参数
        {open ? (
          <ChevronUp className="ml-auto h-4 w-4 text-slate-400" />
        ) : (
          <ChevronDown className="ml-auto h-4 w-4 text-slate-400" />
        )}
      </button>

      {open && (
        <div className="grid grid-cols-1 gap-x-8 gap-y-4 border-t border-gray-100 px-4 py-4 md:grid-cols-2 xl:grid-cols-3">
          <SliderField
            label="chunk_size"
            value={config.chunkSize}
            min={128}
            max={2048}
            step={64}
            onChange={(v) => setConfig({ chunkSize: v })}
          />
          <SliderField
            label="overlap"
            value={config.chunkOverlap}
            min={0}
            max={Math.min(256, config.chunkSize / 2)}
            step={10}
            onChange={(v) => setConfig({ chunkOverlap: v })}
          />
          <SliderField
            label="min_chunk_size"
            value={config.minChunkSize}
            min={0}
            max={200}
            step={10}
            onChange={(v) => setConfig({ minChunkSize: v })}
          />
          <SliderField
            label="语义阈值"
            value={config.semanticThreshold}
            min={0}
            max={1}
            step={0.05}
            format={(v) => v.toFixed(2)}
            onChange={(v) => setConfig({ semanticThreshold: v })}
          />
          <SliderField
            label="父块大小"
            value={config.parentChunkSize}
            min={512}
            max={4096}
            step={128}
            onChange={(v) => setConfig({ parentChunkSize: v })}
          />
          <SliderField
            label="子块大小"
            value={config.childChunkSize}
            min={64}
            max={1024}
            step={32}
            onChange={(v) => setConfig({ childChunkSize: v })}
          />

          <SwitchField
            label="语义切分"
            checked={config.enableSemantic}
            onChange={(v) => setConfig({ enableSemantic: v })}
          />
          <SwitchField
            label="Parent-Child"
            checked={config.enableParentChild}
            onChange={(v) => setConfig({ enableParentChild: v })}
          />
          <SwitchField
            label="过滤空信息块"
            hint="图片/表格无文本时不入库"
            checked={config.dropEmptyBlocks}
            onChange={(v) => setConfig({ dropEmptyBlocks: v })}
          />

          <div>
            <p className="mb-1.5 text-xs font-medium text-slate-600">表格处理</p>
            <Select
              value={config.tableHandling}
              onValueChange={(v) => setConfig({ tableHandling: v })}
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TABLE_HANDLING_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      )}
    </div>
  );
}

function SliderField({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format?: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs font-medium text-slate-600">{label}</span>
        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs tabular-nums text-slate-700">
          {format ? format(value) : value}
        </span>
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v)}
      />
    </div>
  );
}

function SwitchField({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <span className="text-xs font-medium text-slate-600">{label}</span>
        {hint && <span className="ml-1 text-[10px] text-slate-400">{hint}</span>}
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onChange}
        className={cn(checked && "data-[state=checked]:bg-slate-900")}
      />
    </div>
  );
}
