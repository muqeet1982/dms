import { date } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "@/api/client";
import { useBranding } from "@/lib/branding";

/**
 * The public terms / privacy page.
 *
 * It must render with no login: Android's prominent-disclosure rule sends a rep here *before* they grant
 * background location, and an app-store reviewer reads it with no account at all.
 */

type Doc = { kind: string; version: string; published_at: string; body: string };

/** Inline markdown — bold, code and links — turned into elements rather than injected as HTML. */
function inline(text: string, key: string) {
  const out: (string | JSX.Element)[] = [];
  const pattern = /\*\*(.+?)\*\*|`(.+?)`|\[(.+?)\]\((.+?)\)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = pattern.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<strong key={`${key}-b${i}`}>{m[1]}</strong>);
    else if (m[2]) out.push(<code key={`${key}-c${i}`} className="text-body">{m[2]}</code>);
    else out.push(<a key={`${key}-a${i}`} href={m[4]}>{m[3]}</a>);
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (row: string) => row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

/** The subset of markdown the legal text actually uses. No HTML is ever injected. */
function Markdown({ body }: { body: string }) {
  const lines = body.split("\n");
  const blocks: JSX.Element[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];

  const flush = () => {
    if (paragraph.length) {
      blocks.push(<p key={`p${blocks.length}`} className="mb-3">{inline(paragraph.join(" "), `p${blocks.length}`)}</p>);
      paragraph = [];
    }
    if (list.length) {
      blocks.push(<ul key={`u${blocks.length}`} className="mb-3 ps-4">{list.map((li, i) => <li key={i} className="mb-1">{inline(li, `u${blocks.length}-${i}`)}</li>)}</ul>);
      list = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) { flush(); continue; }
    if (/^---+$/.test(trimmed)) { flush(); blocks.push(<hr key={`h${blocks.length}`} className="my-4" />); continue; }

    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flush();
      const level = heading[1].length;
      const Tag = (["h1", "h2", "h3", "h4"][level - 1] || "h4") as "h1";
      blocks.push(<Tag key={`t${blocks.length}`} className={level === 1 ? "h3 mb-3" : level === 2 ? "h5 mt-4 mb-2" : "h6 mt-3 mb-2"}>{inline(heading[2], `t${blocks.length}`)}</Tag>);
      continue;
    }

    // a table: header row, a separator of dashes, then body rows
    if (trimmed.startsWith("|") && /^\|[\s:|-]+\|$/.test((lines[i + 1] || "").trim())) {
      flush();
      const head = cells(trimmed);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++].trim()));
      i--;
      blocks.push(
        <div key={`tb${blocks.length}`} className="table-responsive mb-3">
          <table className="table table-sm table-bordered align-middle mb-0">
            <thead className="table-light"><tr>{head.map((h, j) => <th key={j} className="fs-13">{inline(h, `th${j}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k} className="fs-13">{inline(c, `td${j}-${k}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }

    const bullet = /^[-*]\s+(.*)$/.exec(trimmed);
    if (bullet) { if (paragraph.length) flush(); list.push(bullet[1]); continue; }

    if (list.length) flush();
    paragraph.push(trimmed);
  }
  flush();
  return <>{blocks}</>;
}

export function LegalPublicPage() {
  const { kind = "privacy" } = useParams();
  const branding = useBranding();
  const doc = useQuery<Doc>({ queryKey: ["legal", kind], queryFn: () => api.get(`/api/v1/legal/${kind}`), retry: false });
  const title = kind === "terms" ? "Terms of service" : "Privacy policy";

  return (
    <div className="min-vh-100" style={{ background: "#F8F9FA" }}>
      <div className="container py-4 py-md-5" style={{ maxWidth: 820 }}>
        <div className="d-flex align-items-center gap-2 mb-3">
          {branding?.logo_url
            ? <img src={branding.logo_url} alt="" style={{ height: 28 }} />
            : <span className="fw-semibold">{branding?.site_name || "DMS"}</span>}
        </div>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-4 p-md-5">
            {doc.isLoading && <div className="text-muted py-4">Loading…</div>}
            {doc.isError && (
              <>
                <h1 className="h4 mb-2">{title}</h1>
                <p className="text-muted mb-0">
                  This document has not been published yet. Please contact{" "}
                  <a href={`mailto:${branding?.support_email || "support@dms.invoiceflow.pk"}`}>{branding?.support_email || "support@dms.invoiceflow.pk"}</a>.
                </p>
              </>
            )}
            {doc.data && (
              <>
                <Markdown body={doc.data.body} />
                <hr className="my-4" />
                <div className="fs-12 text-muted">Version {doc.data.version} · {date(doc.data.published_at)}</div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
