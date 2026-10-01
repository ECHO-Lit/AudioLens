import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";

type AccountInfo = { authenticated: boolean; email: string | null };

export default function Account() {
  const [info, setInfo] = useState<AccountInfo>({ authenticated: false, email: null });
  const [mode, setMode] = useState<"login" | "register">("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    const response = await fetch(`${API_BASE}/auth/me`, { credentials: "include" });
    if (response.ok) setInfo(await response.json());
  };
  useEffect(() => { void refresh(); }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/auth/${mode}`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not sign in.");
      setPassword("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sign in.");
    } finally { setBusy(false); }
  };

  const signOut = async () => {
    const response = await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail || "Could not end this session. Your data was not cleared.");
      return;
    }
    setInfo({ authenticated: false, email: null });
  };

  const endAnonymousSession = async () => {
    const response = await fetch(`${API_BASE}/session/end`, { method: "POST", credentials: "include" });
    if (response.ok) {
      setError("This session ended and its uploaded data was deleted. Reload to start a clean session.");
    } else {
      const body = await response.json().catch(() => ({}));
      setError(body.detail || "Could not end this session. Your data was not cleared.");
    }
  };

  return <main className="mx-auto max-w-lg p-8">
    <Link to="/" className="text-sm text-primary underline">Back to ECHO</Link>
    <h1 className="mt-6 text-2xl font-semibold">Your account</h1>
    {info.authenticated ? <section className="mt-6 space-y-4">
      <p>Signed in as <strong>{info.email}</strong>.</p>
      <p className="text-sm text-muted-foreground">This login has a private session. Other browsers and logins get separate empty sessions. Ending this session deletes its uploads, datasets, jobs, and saved analyses.</p>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button onClick={() => void signOut()}>End session and sign out</Button>
    </section> : <>
      <p className="mt-2 text-sm text-muted-foreground">Each browser session has its own private workspace. Create an account to sign in, but each new login starts with a clean session.</p>
      <div className="mt-6 flex gap-2">
        <Button type="button" variant={mode === "register" ? "default" : "outline"} onClick={() => setMode("register")}>Create account</Button>
        <Button type="button" variant={mode === "login" ? "default" : "outline"} onClick={() => setMode("login")}>Sign in</Button>
      </div>
      <form className="mt-5 space-y-4" onSubmit={(event) => void submit(event)}>
        <label className="block text-sm">Email<input className="mt-1 block w-full rounded border px-3 py-2" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label className="block text-sm">Password<input className="mt-1 block w-full rounded border px-3 py-2" type="password" autoComplete={mode === "register" ? "new-password" : "current-password"} minLength={mode === "register" ? 12 : undefined} required value={password} onChange={(event) => setPassword(event.target.value)} />{mode === "register" && <span className="text-xs text-muted-foreground">Use at least 12 characters.</span>}</label>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button type="submit" disabled={busy}>{busy ? "Please wait…" : mode === "register" ? "Create account" : "Sign in"}</Button>
      </form>
      <div className="mt-8 border-t pt-5">
        <p className="mb-3 text-sm text-muted-foreground">End the current browser session and immediately erase its uploaded data.</p>
        <Button type="button" variant="outline" onClick={() => void endAnonymousSession()}>End session and erase data</Button>
      </div>
    </>}
  </main>;
}
