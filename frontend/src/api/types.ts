// Hand-maintained TypeScript types for the API (a generated superset is available via `npm run gen:api`).
export type UUID = string;

export type User = {
  id: UUID; email: string | null; phone: string | null; full_name: string; user_type: "PLATFORM_ADMIN" | "TENANT" | "EXTERNAL";
  is_platform_admin: boolean; locale: string; timezone: string; avatar: string | null; mfa_enabled: boolean; must_change_password: boolean; last_login: string | null;
};
export type Membership = { id: UUID; tenant_id: UUID; tenant_name: string; tenant_slug: string; tenant_status: string; is_owner: boolean; status: string; roles: string[] };
export type Tenant = {
  id: UUID; slug: string; legal_name: string; display_name: string; status: string; address: string; city: string; phone: string; email: string; website: string;
  tax_numbers: Record<string, string>; logo: string | null; base_currency: string; fiscal_year_start_month: number; timezone: string; settings: Record<string, any>; branding: Record<string, any>;
  plan: { code: string; name: string; status: string; end_date: string; days_left: number } | null;
  white_label?: boolean;
};
export type Me = {
  user: User; memberships: Membership[]; tenant: Tenant | null; permissions: string[]; features: Record<string, any>; limits: Record<string, any>; is_owner: boolean;
  impersonating: boolean; impersonation_expires_at?: string | null; platform_permissions: string[];
  /** the distinct role_type of the roles this login holds, and whether any of them (or ownership) is an office one */
  role_types?: ("internal" | "external" | "platform")[]; has_office_role?: boolean;
  /** per-page switches: { users: { view, add, edit, delete, extras } } — see apps/accounts/page_permissions.py */
  pages?: Record<string, { view: boolean; add: boolean; edit: boolean; delete: boolean; extras: Record<string, boolean> }>; warehouses: UUID[]; supplier_rep: { id: UUID; supplier_id: UUID; supplier_name: string; code: string; territory: string } | null;
};
export type Paginated<T> = { count: number; page: number; pages: number; page_size: number; next: string | null; previous: string | null; results: T[] };
export type Problem = { type: string; title: string; status: number; detail: string; errors: Record<string, string[] | any> };
export type Option = { value: string; label: string };
