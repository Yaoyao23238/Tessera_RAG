/**
 * 全局布局：左侧边栏（LOGO + 五页导航）+ 主内容区。
 */

import { NavLink, Outlet } from "react-router-dom";
import {
  FileText,
  ScanText,
  Scissors,
  Search,
  FlaskConical,
  Settings,
} from "lucide-react";
import logoUrl from "../../assets/tessera-logo.svg";
import { cn } from "../../lib/utils";
import { useWebSocket } from "../../hooks/useWebSocket";

const NAV_ITEMS = [
  { to: "/", label: "文档管理", icon: FileText, end: true },
  { to: "/parse", label: "解析预览", icon: ScanText },
  { to: "/preview", label: "切分预览", icon: Scissors },
  { to: "/search", label: "检索测试", icon: Search },
  { to: "/evaluation", label: "质量评测", icon: FlaskConical },
  { to: "/settings", label: "设置", icon: Settings },
];

export function AppLayout() {
  // 全局 WS 连接放在布局层：这里在 Router context 内（useWebSocket 需要 useNavigate）
  useWebSocket();
  return (
    <div className="flex h-full bg-gray-50">
      {/* 侧边栏 */}
      <aside className="flex w-52 shrink-0 flex-col border-r border-gray-200 bg-white">
        <div className="flex items-center gap-2.5 px-4 pb-5 pt-5">
          <img src={logoUrl} alt="Tessera" className="h-9 w-9" />
          <div>
            <p className="text-base font-bold tracking-wide text-slate-900">Tessera</p>
            <p className="text-[10px] text-slate-400">本地 RAG 文档摄入</p>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-2">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-slate-900 font-medium text-white"
                    : "text-slate-600 hover:bg-gray-100"
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 text-[10px] text-slate-300">v0.1.0 MVP</div>
      </aside>

      {/* 主内容区 */}
      <main className="min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
