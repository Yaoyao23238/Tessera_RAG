import { RouterProvider } from "react-router-dom";
import { TooltipProvider } from "@radix-ui/react-tooltip";
import { router } from "./router";
import { Toaster } from "./components/common/Toaster";

/**
 * 应用根组件：挂载路由 + toast 浮层。
 * 全局 WS 连接在 AppLayout 内建立（需要 Router context 支持跳转）。
 */
export default function App() {
  return (
    <TooltipProvider>
      <RouterProvider router={router} />
      <Toaster />
    </TooltipProvider>
  );
}
