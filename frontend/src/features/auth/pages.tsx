import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { toast } from "@/lib/toast";
import { AuthLayout } from "./AuthLayout";
import { GetAppStrip } from "@/components/layout/AppQrModal";

function homeFor(user: any, memberships: any[], tenantId: string | null) {
  if (user?.is_platform_admin) return "/admin";
  if (!tenantId && memberships.length > 1) return "/select-company";
  if (user?.user_type === "EXTERNAL") return "/rep";
  return "/app";
}

export function LoginPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const setLogin = useAuth((s) => s.setLogin);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const d = await api.post("/api/v1/auth/login", { identifier, password, platform: "web", device_name: navigator.userAgent.slice(0, 80) });
      setLogin(d);
      qc.clear();
      if (d.must_change_password) return nav("/change-password");
      nav(homeFor(d.user, d.memberships, useAuth.getState().tenantId));
    } catch (err: any) {
      setError(err instanceof ApiError ? Object.values(err.fieldErrors()).join(" ") || err.message : err.message);
    } finally { setBusy(false); }
  };
  return (
    <AuthLayout title="Sign In" subtitle="Please enter your details to sign in">
      <form onSubmit={submit}>
        {error && <div className="alert alert-danger py-2">{error}</div>}
        <div className="mb-3"><label className="form-label">Email, phone or username</label><div className="input-group"><input className="form-control border-end-0" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="you@example.com" autoComplete="username" autoFocus required /><span className="input-group-text border-start-0"><i className="ti ti-mail" /></span></div></div>
        <div className="mb-3"><label className="form-label">Password</label><div className="input-group"><input type={show ? "text" : "password"} className="form-control border-end-0" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /><span className="input-group-text border-start-0 cursor-pointer" onClick={() => setShow((s) => !s)}><i className={`ti ${show ? "ti-eye" : "ti-eye-off"}`} /></span></div></div>
        <div className="d-flex align-items-center justify-content-between mb-3"><div className="form-check form-check-md mb-0"><input className="form-check-input" id="remember" type="checkbox" defaultChecked /><label htmlFor="remember" className="form-check-label mt-0">Remember me</label></div><Link to="/forgot-password" className="link-danger">Forgot password?</Link></div>
        <button type="submit" className="btn btn-primary w-100" disabled={busy}>{busy ? "Signing in…" : "Sign In"}</button>
      </form>
      <GetAppStrip />
    </AuthLayout>
  );
}

export function ForgotPasswordPage() {
  const nav = useNavigate();
  const [identifier, setIdentifier] = useState("");
  const [channel, setChannel] = useState("email");
  return (
    <AuthLayout title="Forgot password" subtitle="We'll send a verification code to your email or phone">
      <form onSubmit={async (e) => { e.preventDefault(); await api.post("/api/v1/auth/otp/request", { identifier, purpose: "password_reset", channel }); toast.success("If the account exists, a code has been sent."); nav(`/reset-password?identifier=${encodeURIComponent(identifier)}`); }}>
        <div className="mb-3"><label className="form-label">Email or phone</label><input className="form-control" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required autoFocus /></div>
        <div className="mb-3"><label className="form-label">Send code by</label><select className="form-select" value={channel} onChange={(e) => setChannel(e.target.value)}><option value="email">Email</option><option value="sms">SMS</option></select></div>
        <button className="btn btn-primary w-100">Send code</button>
        <div className="text-center mt-3"><Link to="/login">Back to sign in</Link></div>
      </form>
    </AuthLayout>
  );
}

export function ResetPasswordPage({ purpose = "password_reset", title = "Reset password" }: { purpose?: string; title?: string }) {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [identifier, setIdentifier] = useState(sp.get("identifier") || "");
  const [code, setCode] = useState(sp.get("code") || "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <AuthLayout title={title} subtitle={purpose === "invite" ? "Enter the code from your invitation and choose a password" : "Enter the code you received and a new password"}>
      <form onSubmit={async (e) => { e.preventDefault(); setError(null); try { await api.post("/api/v1/auth/password/reset", { identifier, purpose, code, new_password: password }); toast.success("Password set. Please sign in."); nav("/login"); } catch (err: any) { setError(err instanceof ApiError ? Object.values(err.fieldErrors()).join(" ") || err.message : err.message); } }}>
        {error && <div className="alert alert-danger py-2">{error}</div>}
        <div className="mb-3"><label className="form-label">Email or phone</label><input className="form-control" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required /></div>
        <div className="mb-3"><label className="form-label">Verification code</label><input className="form-control" value={code} onChange={(e) => setCode(e.target.value)} required /></div>
        <div className="mb-3"><label className="form-label">New password</label><input type="password" className="form-control" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required /></div>
        <button className="btn btn-primary w-100">Set password</button>
        <div className="text-center mt-3"><Link to="/login">Back to sign in</Link></div>
      </form>
    </AuthLayout>
  );
}

export function ChangePasswordPage() {
  const nav = useNavigate();
  const { user, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <AuthLayout title="Change password" subtitle={user?.must_change_password ? "You must set a new password before continuing" : "Choose a new password"}>
      <form onSubmit={async (e) => { e.preventDefault(); setError(null); try { await api.post("/api/v1/auth/password/change", { current_password: current, new_password: password }); toast.success("Password changed. Please sign in again."); logout(); nav("/login"); } catch (err: any) { setError(err.message); } }}>
        {error && <div className="alert alert-danger py-2">{error}</div>}
        {!user?.must_change_password && <div className="mb-3"><label className="form-label">Current password</label><input type="password" className="form-control" value={current} onChange={(e) => setCurrent(e.target.value)} /></div>}
        <div className="mb-3"><label className="form-label">New password</label><input type="password" className="form-control" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required autoFocus /></div>
        <button className="btn btn-primary w-100">Change password</button>
      </form>
    </AuthLayout>
  );
}

export function SelectCompanyPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { memberships, setTenant, user } = useAuth();
  return (
    <AuthLayout title="Choose a company" subtitle="You belong to more than one company">
      <div className="list-group">
        {memberships.map((m) => (
          <button key={m.tenant_id} className="list-group-item list-group-item-action d-flex justify-content-between align-items-center" onClick={() => { setTenant(m.tenant_id); qc.clear(); nav(user?.user_type === "EXTERNAL" ? "/rep" : "/app"); }}>
            <div><div className="fw-semibold">{m.tenant_name}</div><div className="fs-12 text-muted">{m.is_owner ? "Owner" : m.roles.join(", ")}</div></div>
            <span className={`badge bg-${m.tenant_status === "active" ? "success" : m.tenant_status === "trial" ? "info" : "warning"}-light text-${m.tenant_status === "active" ? "success" : m.tenant_status === "trial" ? "info" : "warning"}`}>{m.tenant_status}</span>
          </button>
        ))}
      </div>
    </AuthLayout>
  );
}
