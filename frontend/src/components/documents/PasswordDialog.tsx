/**
 * 加密 PDF 密码弹窗：提交密码后触发重解析（密码不落盘）。
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function PasswordDialog({
  documentId,
  filename,
  open,
  onOpenChange,
}: {
  documentId: string | null;
  filename: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [password, setPassword] = useState("");
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  const mutation = useMutation({
    mutationFn: () =>
      api.post(`/documents/${documentId}/unlock`, { password }),
    onSuccess: () => {
      pushToast({ kind: "info", title: "已提交密码，重新解析中" });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      onOpenChange(false);
      setPassword("");
    },
    onError: (err) => {
      pushToast({ kind: "error", title: "解锁失败", message: friendlyError(err) });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogTitle className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-amber-500" />
          加密 PDF 需要密码
        </DialogTitle>
        <p className="mt-2 text-sm text-slate-500">
          「{filename}」已加密。输入密码后将重新解析，密码仅用于本次解密，不会保存。
        </p>
        <Input
          type="password"
          className="mt-4"
          placeholder="PDF 密码"
          value={password}
          autoFocus
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && password) mutation.mutate();
          }}
        />
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            跳过
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!password || mutation.isPending}
          >
            {mutation.isPending ? "提交中…" : "解密并解析"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
