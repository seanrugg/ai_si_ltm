import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { createHashRouter, Navigate, Outlet, RouterProvider, useNavigate } from "react-router-dom";
import { api, setUnauthorizedHandler } from "./api/client.js";
import Shell from "./components/Shell.jsx";
import { Loading } from "./components/ui.jsx";
import Conversation from "./pages/Conversation.jsx";
import DecisionTrace from "./pages/DecisionTrace.jsx";
import Integrity from "./pages/Integrity.jsx";
import Lessons from "./pages/Lessons.jsx";
import Volume from "./pages/Volume.jsx";
import Swarms from "./pages/Swarms.jsx";
import Login from "./pages/Login.jsx";
import Members from "./pages/Members.jsx";
import Outcomes from "./pages/Outcomes.jsx";
import RecordPage from "./pages/RecordPage.jsx";
import Settings from "./pages/Settings.jsx";
import RecallLog from "./pages/RecallLog.jsx";
import Timeline from "./pages/Timeline.jsx";
import Activity from "./pages/Activity.jsx";

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

const router = createHashRouter([
  { path: "/login", element: <Login /> },
  {
    element: <RequireAdmin />,
    children: [
      { path: "/", element: <Timeline /> },
      { path: "/activity", element: <Activity /> },
      { path: "/members", element: <Members /> },
      { path: "/integrity", element: <Integrity /> },
      { path: "/volume", element: <Volume /> },
      { path: "/swarms", element: <Swarms /> },
      { path: "/record/:agent/:seq", element: <RecordPage /> },
      { path: "/conversation", element: <Conversation /> },
      { path: "/decision-trace/:agent/:seq", element: <DecisionTrace /> },
      { path: "/outcomes", element: <Outcomes /> },
      { path: "/lessons", element: <Lessons /> },
      { path: "/recall-log", element: <RecallLog /> },
      { path: "/settings", element: <Settings /> },
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
