import { ReactNode } from "react";
import { useBranding } from "@/lib/branding";

/** Two-column auth layout from the template (accounts/login.html). */
export function AuthLayout({ children, title, subtitle }: { children: ReactNode; title: string; subtitle?: string }) {
  const b = useBranding();
  return (
    <div className="main-wrapper">
      <div className="container-fuild">
        <div className="w-100 overflow-hidden position-relative flex-wrap d-block vh-100">
          <div className="row">
            <div className="col-lg-5">
              <div className="login-background position-relative d-lg-flex align-items-center justify-content-center d-none flex-wrap vh-100">
                <div className="bg-overlay-img"><img src="/assets/img/bg/bg-02.png" className="bg-2" alt="" /><img src="/assets/img/bg/bg-03.png" className="bg-3" alt="" /></div>
                <div className="authentication-card w-100">
                  <div className="authen-overlay-item border w-100">
                    <h1 className="text-white display-1">{b?.login_headline || "Every unit in, every unit out."}</h1>
                    <div className="my-4 mx-auto authen-overlay-img"><img src="/assets/img/bg/authentication-bg-01.png" alt="" /></div>
                    <p className="text-white fs-20 fw-semibold text-center">{b?.login_subtext || "Distribution, delivery and accounts in one place."}</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="col-lg-7 col-md-12 col-sm-12">
              <div className="row justify-content-center align-items-center vh-100 overflow-auto flex-wrap">
                <div className="col-md-7 mx-auto vh-100">
                  <div className="vh-100 d-flex flex-column justify-content-between p-4 pb-0">
                    <div className="mx-auto mb-4 text-center mt-4">{b?.logo_url ? <img src={b.logo_url} alt={b?.site_name || "DMS"} style={{ maxHeight: 48 }} /> : <h3 className="fw-bold text-primary mb-0">{b?.site_name || "DMS"}</h3>}<div className="fs-12 text-muted">{b?.site_tagline || "Distribution Management"}</div></div>
                    <div>
                      <div className="text-center mb-3"><h2 className="mb-2">{title}</h2>{subtitle && <p className="mb-0">{subtitle}</p>}</div>
                      {children}
                    </div>
                    <div className="mt-5 pb-4 text-center"><p className="mb-0 text-gray-9 fs-12">{b?.footer_text || `© ${new Date().getFullYear()} ${b?.company_name || "dms.invoiceflow.pk"}`}</p></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
