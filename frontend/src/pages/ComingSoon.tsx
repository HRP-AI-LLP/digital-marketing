import { Link, useLocation } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { PageHeader } from "../components/ui";

/** Shown for any address the studio doesn't have (a mistyped or old link). */
export default function ComingSoon() {
  const { pathname, search } = useLocation();
  // An outside link (e.g. a published YouTube or LinkedIn post) opened as if it were a studio page.
  const outside = /^\/(https?:\/\/?.*)$/i.exec(pathname);
  const url = outside ? outside[1].replace(/^(https?:)\/(?!\/)/i, "$1//") + search : null;
  return (
    <div>
      <PageHeader title={url ? "This link goes to another website" : "Page not found"} />
      <div className="max-w-xl rounded-lg border border-line bg-surface p-6">
        {url ? (
          <>
            <p className="[overflow-wrap:anywhere]">{url}</p>
            <a href={url} target="_blank" rel="noreferrer"
              className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-white hover:bg-primary-700">
              Open it <ExternalLink size={15} />
            </a>
          </>
        ) : (
          <>
            <p>There's no page at this address. It may be an old or mistyped link.</p>
            <Link to="/" className="mt-4 inline-block text-primary-700 underline">Go to the dashboard</Link>
          </>
        )}
      </div>
    </div>
  );
}
