/**
 * LLM 配置弹窗（严格按参考图三视图实现）：
 * 1. 选择页：「添加模型」标题 + 自定义模型/DeepSeek 两张圆角卡片 + 右箭头 + 右上角关闭
 * 2. DeepSeek 表单：红色必填星号、灰底圆角输入框、API 密钥眼睛图标 + 「获取 API 密钥」链接、
 *    高级配置可展开、底部连通性测试提示、取消/重置/添加模型按钮组
 * 3. 自定义表单：API 格式下拉、完整 URL 开关、模型 ID、展示名称 0/32 计数、左上角返回箭头
 *
 * 提交前先调 POST /settings/llm/test 连通性测试，通过才写入。
 * 适配说明：高级配置 temperature/max_tokens 不在后端 LLMProviderSchema 内，
 * 发送时会被后端忽略（Pydantic 默认忽略额外字段），仅作 UI 保留。
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Box,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Info,
  Loader2,
  Zap,
} from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { Button } from "../ui/button";
import { Input, FieldLabel } from "../ui/input";
import { Switch } from "../ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { cn } from "../../lib/utils";
import type { LLMProvider } from "../../types";

type View = "select" | "deepseek" | "custom";

interface FormState {
  baseUrl: string;
  apiKey: string;
  modelName: string;
  displayName: string;
  fullUrl: boolean;
  temperature: string;
  maxTokens: string;
}

const DEEPSEEK_MODELS = ["deepseek-v4-flash", "deepseek-v4-pro"];

const DEFAULT_DEEPSEEK: FormState = {
  baseUrl: "https://api.deepseek.com",
  apiKey: "",
  modelName: "deepseek-v4-flash",
  displayName: "",
  fullUrl: false,
  temperature: "0.7",
  maxTokens: "4096",
};

const DEFAULT_CUSTOM: FormState = {
  baseUrl: "",
  apiKey: "",
  modelName: "",
  displayName: "",
  fullUrl: false,
  temperature: "0.7",
  maxTokens: "4096",
};

export function LLMProviderDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [view, setView] = useState<View>("select");
  const [form, setForm] = useState<FormState>(DEFAULT_DEEPSEEK);
  const [showKey, setShowKey] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const queryClient = useQueryClient();

  function reset(next: View) {
    setView(next);
    setForm(next === "custom" ? DEFAULT_CUSTOM : DEFAULT_DEEPSEEK);
    setShowKey(false);
    setAdvancedOpen(false);
    setErrorMsg("");
  }

  function close(v: boolean) {
    if (!v) reset("select");
    onOpenChange(v);
  }

  /** 组装 provider payload（完整 URL 模式下剥离 /chat/completions 后缀）。 */
  function buildPayload(): Omit<LLMProvider, "id"> {
    let baseUrl = form.baseUrl.trim();
    if (form.fullUrl && baseUrl.endsWith("/chat/completions")) {
      baseUrl = baseUrl.slice(0, -"/chat/completions".length);
    }
    baseUrl = baseUrl.replace(/\/+$/, "");
    const isDeepseek = view === "deepseek";
    return {
      name: isDeepseek ? "DeepSeek" : form.displayName.trim() || form.modelName.trim(),
      providerType: isDeepseek ? "deepseek" : "custom",
      baseUrl,
      apiKey: form.apiKey.trim(),
      modelName: form.modelName.trim(),
      displayName:
        form.displayName.trim() ||
        (isDeepseek ? `DeepSeek ${form.modelName}` : form.modelName.trim()),
      isDefault: false,
      enabled: true,
    };
  }

  // 添加模型：先连通性测试，通过后写入
  const addMutation = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      await api.post("/settings/llm/test", payload);
      return api.post<LLMProvider>("/settings/llm", payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      close(false);
    },
    onError: (err) => setErrorMsg(friendlyError(err)),
  });

  const canSubmit =
    form.apiKey.trim().length > 0 &&
    form.modelName.trim().length > 0 &&
    (view === "deepseek" || form.baseUrl.trim().length > 0);

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-md">
        {view === "select" ? (
          <>
            <DialogTitle>添加模型</DialogTitle>
            <div className="mt-4 space-y-3">
              <ProviderCard
                icon={<Box className="h-5 w-5 text-slate-500" />}
                title="自定义模型"
                desc="OpenAI 兼容 API 格式"
                onClick={() => reset("custom")}
              />
              <ProviderCard
                icon={<Zap className="h-5 w-5 text-blue-500" />}
                title="DeepSeek"
                desc="深度求索官方 API"
                onClick={() => reset("deepseek")}
              />
            </div>
          </>
        ) : (
          <>
            {/* 表单页头：返回箭头 + 标题 */}
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded-md p-1 text-slate-400 hover:bg-gray-100 hover:text-slate-600"
                onClick={() => reset("select")}
                aria-label="返回"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
              <DialogTitle>
                {view === "deepseek" ? "通过服务商添加" : "自定义模型"}
              </DialogTitle>
            </div>

            <div className="mt-4 space-y-4">
              {view === "deepseek" ? (
                <>
                  <div>
                    <FieldLabel required>服务商</FieldLabel>
                    <Select value="deepseek" disabled>
                      <SelectTrigger>
                        <SelectValue>DeepSeek</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="deepseek">DeepSeek</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <FieldLabel required>模型</FieldLabel>
                    <Select
                      value={form.modelName}
                      onValueChange={(v) => setForm({ ...form, modelName: v })}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {DEEPSEEK_MODELS.map((m) => (
                          <SelectItem key={m} value={m}>
                            {m}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <FieldLabel required>API 格式</FieldLabel>
                    <Select value="openai" disabled>
                      <SelectTrigger>
                        <SelectValue>OpenAI Chat Completions 格式</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="openai">
                          OpenAI Chat Completions 格式
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <div className="flex items-center justify-between">
                      <FieldLabel required>自定义请求地址</FieldLabel>
                      <label className="mb-1.5 flex items-center gap-1.5 text-xs text-slate-500">
                        完整 URL
                        <Switch
                          checked={form.fullUrl}
                          onCheckedChange={(v) => setForm({ ...form, fullUrl: v })}
                          className="scale-90"
                        />
                      </label>
                    </div>
                    <Input
                      placeholder={
                        form.fullUrl
                          ? "https://example.com/v1/chat/completions"
                          : "https://example.com/v1"
                      }
                      value={form.baseUrl}
                      onChange={(e) => setForm({ ...form, baseUrl: e.target.value })}
                    />
                    <p className="mt-1 text-[11px] text-slate-400">
                      {form.fullUrl
                        ? "请输入完整的聊天补全接口地址"
                        : "不要以斜杠 / 结尾，/chat/completions 会自动补充"}
                    </p>
                  </div>
                  <div>
                    <FieldLabel required>模型 ID</FieldLabel>
                    <Input
                      placeholder="例如 gpt-4o-mini / qwen-plus"
                      value={form.modelName}
                      onChange={(e) => setForm({ ...form, modelName: e.target.value })}
                    />
                  </div>
                  <div>
                    <div className="flex items-center justify-between">
                      <FieldLabel>模型展示名称</FieldLabel>
                      <span className="mb-1.5 text-[11px] tabular-nums text-slate-400">
                        {form.displayName.length}/32
                      </span>
                    </div>
                    <Input
                      placeholder="可选，默认使用模型 ID"
                      maxLength={32}
                      value={form.displayName}
                      onChange={(e) =>
                        setForm({ ...form, displayName: e.target.value })
                      }
                    />
                  </div>
                </>
              )}

              {/* API 密钥（眼睛图标 + 获取链接） */}
              <div>
                <div className="flex items-center justify-between">
                  <FieldLabel required>API 密钥</FieldLabel>
                  {view === "deepseek" && (
                    <a
                      href="https://platform.deepseek.com/api_keys"
                      target="_blank"
                      rel="noreferrer"
                      className="mb-1.5 text-xs text-blue-500 hover:underline"
                    >
                      获取 API 密钥
                    </a>
                  )}
                </div>
                <div className="relative">
                  <Input
                    type={showKey ? "text" : "password"}
                    placeholder="sk-..."
                    className="pr-9"
                    value={form.apiKey}
                    onChange={(e) => setForm({ ...form, apiKey: e.target.value })}
                  />
                  <button
                    type="button"
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                    onClick={() => setShowKey((v) => !v)}
                    aria-label={showKey ? "隐藏密钥" : "显示密钥"}
                  >
                    {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              {/* 高级配置（可展开） */}
              <div>
                <button
                  type="button"
                  className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
                  onClick={() => setAdvancedOpen((v) => !v)}
                >
                  高级配置
                  {advancedOpen ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                  )}
                </button>
                {advancedOpen && (
                  <div className="mt-3 grid grid-cols-2 gap-3 rounded-lg bg-gray-50 p-3">
                    <div>
                      <FieldLabel>temperature</FieldLabel>
                      <Input
                        type="number"
                        min={0}
                        max={2}
                        step={0.1}
                        value={form.temperature}
                        onChange={(e) =>
                          setForm({ ...form, temperature: e.target.value })
                        }
                      />
                    </div>
                    <div>
                      <FieldLabel>max_tokens</FieldLabel>
                      <Input
                        type="number"
                        min={1}
                        value={form.maxTokens}
                        onChange={(e) =>
                          setForm({ ...form, maxTokens: e.target.value })
                        }
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* 底部提示 */}
              <p className="flex items-start gap-1.5 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-600">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                连通性测试会发起一次真实请求，会消耗少量模型 Token
              </p>

              {errorMsg && (
                <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
                  {errorMsg}
                </p>
              )}
            </div>

            {/* 按钮组：取消 / 重置 / 添加模型（深色主按钮） */}
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => close(false)}>
                取消
              </Button>
              <Button
                variant="secondary"
                onClick={() => reset(view)}
              >
                重置
              </Button>
              <Button
                onClick={() => addMutation.mutate()}
                disabled={!canSubmit || addMutation.isPending}
              >
                {addMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {addMutation.isPending ? "测试连接中…" : "添加模型"}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 选择页卡片：图标 + 标题 + 描述 + 右侧箭头。 */
function ProviderCard({
  icon,
  title,
  desc,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-4",
        "text-left transition-all hover:border-slate-400 hover:shadow-sm"
      )}
      onClick={onClick}
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-gray-100">
        {icon}
      </span>
      <span className="flex-1">
        <span className="block text-sm font-medium text-slate-800">{title}</span>
        <span className="block text-xs text-slate-400">{desc}</span>
      </span>
      <ChevronRight className="h-4 w-4 text-slate-300" />
    </button>
  );
}
