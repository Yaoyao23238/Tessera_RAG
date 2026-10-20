/**
 * 按钮（shadcn/ui 风格精简版）。
 */

import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "secondary" | "outline" | "ghost" | "destructive";
  size?: "sm" | "md" | "lg" | "icon";
}

const VARIANTS: Record<string, string> = {
  // 深色主按钮（对齐参考图）
  default: "bg-slate-900 text-white hover:bg-slate-700 shadow-sm",
  secondary: "bg-gray-100 text-slate-700 hover:bg-gray-200",
  outline: "border border-gray-200 bg-white text-slate-700 hover:bg-gray-50",
  ghost: "text-slate-600 hover:bg-gray-100",
  destructive: "bg-red-600 text-white hover:bg-red-500",
};

const SIZES: Record<string, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-10 px-6 text-sm",
  icon: "h-8 w-8",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "md", type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors",
        "disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none",
        "focus-visible:ring-2 focus-visible:ring-slate-400",
        VARIANTS[variant],
        SIZES[size],
        className
      )}
      {...props}
    />
  )
);
Button.displayName = "Button";
