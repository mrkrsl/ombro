// The brief a job ran from (the copy of the spec it keeps), as a page of its own, from the Jobs page.

import { ArrowLeft, MessageSquare } from "lucide-react";
import { Markdown } from "../components/Markdown";
import { useStore } from "../store/store";

export function BriefPage() {
  const { brief, show, discussBrief } = useStore();
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
              {brief.spec ? (
                <span className="report-tools">
                  <button
                    className="text-button"
                    onClick={() => void discussBrief(brief.job_id)}
                    title="Start a chat about this brief: change it, start it again or schedule it"
                  >
                    <MessageSquare size={16} /> Discuss this brief
                  </button>
                </span>
              ) : null}
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
