import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAdmin } from "../App.jsx";
import { api } from "../api/client.js";

export default function Login() {
  const { username, setUsername } = useAdmin();
  const navigate = useNavigate();
  const [form, setForm] = useState({ username: "", password: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (username) return <Navigate to="/" replace />;

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api("/auth/admin/login", { method: "POST", body: form });
      setUsername(res.username);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.status === 401 ? "That username and password don't match." : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="panel w-full max-w-sm p-6">
        <h1 className="text-xl font-bold">
          LTMS<span className="ml-2 inline-block h-2 w-2 rounded-full bg-led align-middle" aria-hidden="true" />
        </h1>
        <p className="mb-6 mt-1 text-sm text-paper-muted">Memory for the robot mesh. Administrator sign-in.</p>
        <label className="mb-3 block text-sm">
          Username
          <input
            className="field mt-1 w-full"
            autoComplete="username"
            required
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
          />
        </label>
        <label className="mb-4 block text-sm">
          Password
          <input
            className="field mt-1 w-full"
            type="password"
            autoComplete="current-password"
            required
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
        </label>
        {error && (
          <p className="mb-3 text-sm text-bad" role="alert">
            {error}
          </p>
        )}
        <button className="btn btn-primary w-full justify-center" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
