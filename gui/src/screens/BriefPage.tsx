// The brief a job ran from (the copy of the spec it keeps), as a page of its own, from the Jobs page.

import { ArrowLeft } from "lucide-react";
import { Markdown } from "../components/Markdown";
import { useStore } from "../store/store";

export function BriefPage() {
  const { brief, show } = useStore();
  return (
    <div className="page report-page">
      <div className="column wide-page">
        <p>
          <button className="primary back" onClick={() => show("jobs")}>
            <ArrowLeft size={16} /> Back to Jobs
          </button>
        </p>
        {brief ? (
          <div className="report-card">
            <div className="report-head">
              <span>
                <b>Brief:</b> {brief.name}
              </span>
            </div>
            <div className="report-body">
              {brief.spec ? <Markdown text={brief.spec} /> : <p className="muted">This job has no brief saved.</p>}
            </div>
          </div>
        ) : (
          <p className="muted">Opening the brief…</p>
        )}
      </div>
    </div>
  );
}
