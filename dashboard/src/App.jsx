import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { createHashRouter, Navigate, Outlet, RouterProvider, useNavigate } from "react-router-dom";
import { api, setUnauthorizedHandler } from "./api/client.js";
import Shell from "./components/Shell.jsx";
import { Loading } from "./components/ui.jsx";
import ComingSoon from "./pages/ComingSoon.jsx";
import Integrity from "./pages/Integrity.jsx";
import Login from "./pages/Login.jsx";
import Members from "./pages/Members.jsx";
import RecordPage from "./pages/RecordPage.jsx";
import RecallLog from "./pages/RecallLog.jsx";
import Timeline from "./pages/Timeline.jsx";

const AdminContext = createContext(null);
export const useAdmin = () => useContext(AdminContext);

function AdminProvider({ children }) {
  // undefined = still checking, null = logged out, string = username
  const [username, setUsername] = useState(undefined);

  useEffect(() => {
    api("/admin/me")
      .then((d) => setUsername(d.username))
      .catch(() => setUsername(null));
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/admin/logout", { method: "POST" });
    } finally {
      setUsername(null);
    }
  }, []);

  return <AdminContext.Provider value={{ username, setUsername, logout }}>{children}</AdminContext.Provider>;
}

function RequireAdmin() {
  const { username, setUsername } = useAdmin();
  const navigate = useNavigate();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUsername(null);
      navigate("/login", { replace: true });
    });
  }, [navigate, setUsername]);
  if (username === undefined) return <Loading what="Checking your session" />;
  if (username === null) return <Navigate to="/login" replace />;
  return (
    <Shell>
      <Outlet />
    </Shell>
  );
}

// Pages for P4 point at the API they will use; see the agent-task issues.
const router = createHashRouter([
  { path: "/login", element: <Login /> },
  {
    element: <RequireAdmin />,
    children: [
      { path: "/", element: <Timeline /> },
      { path: "/members", element: <Members /> },
      { path: "/integrity", element: <Integrity /> },
      { path: "/record/:agent/:seq", element: <RecordPage /> },
      { path: "/conversation", element: <ComingSoon title="Conversation" endpoint="GET /api/admin/conversation" /> },
      { path: "/decision-trace/:agent/:seq", element: <ComingSoon title="Decision trace" endpoint="GET /api/admin/decision-trace" /> },
      { path: "/outcomes", element: <ComingSoon title="Outcomes" endpoint="GET /api/admin/outcomes" /> },
      { path: "/lessons", element: <ComingSoon title="Lessons and policies" endpoint="GET /api/admin/lessons" /> },
      { path: "/recall-log", element: <RecallLog /> },
      { path: "/settings", element: <ComingSoon title="Settings" endpoint="GET /api/admin/settings" /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

export default function App() {
  return (
    <AdminProvider>
      <RouterProvider router={router} />
    </AdminProvider>
  );
}
