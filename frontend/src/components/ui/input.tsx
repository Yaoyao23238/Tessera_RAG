/**
 * 输入框 / 文本域 / 标签（灰底圆角，对齐参考图风格）。
 */

import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-lg border border-transparent bg-gray-100 px-3 text-sm text-slate-800",
        "placeholder:text-slate-400 focus:border-slate-300 focus:bg-white focus:outline-none",
        "focus:ring-2 focus:ring-slate-200 disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full rounded-lg border border-transparent bg-gray-100 px-3 py-2 text-sm text-slate-800",
      "placeholder:text-slate-400 focus:border-slate-300 focus:bg-white focus:outline-none",
      "focus:ring-2 focus:ring-slate-200 disabled:opacity-50",
      className
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

/** 表单标签：required 时显示红色星号。 */
export function FieldLabel({
  children,
  required,
  className,
}: {
  children: React.ReactNode;
  required?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("mb-1.5 text-sm font-medium text-slate-700", className)}>
      {required && <span className="mr-0.5 text-red-500">*</span>}
      {children}
    </div>
  );
}
