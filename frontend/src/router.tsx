import { createBrowserRouter } from "react-router-dom";
import { AppLayout } from "./components/layout/AppLayout";
import DocumentsPage from "./pages/DocumentsPage";
import ParsePreviewPage from "./pages/ParsePreviewPage";
import ChunkPreviewPage from "./pages/ChunkPreviewPage";
import SearchPage from "./pages/SearchPage";
import EvaluationPage from "./pages/EvaluationPage";
import SettingsPage from "./pages/SettingsPage";

/**
 * 路由：AppLayout（侧边栏）下挂六个页面。
 */
export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <DocumentsPage /> },
      { path: "parse", element: <ParsePreviewPage /> },
      { path: "preview", element: <ChunkPreviewPage /> },
      { path: "search", element: <SearchPage /> },
      { path: "evaluation", element: <EvaluationPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
