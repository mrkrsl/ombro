// A job's details, as a page of its own, from Jobs › Job details or the side panel: the brief it
// ran from (the copy of the spec it keeps), then Ombro's record of how it went, once it has ended.

import { ArrowLeft, MessageSquare } from "lucide-react";
import { Markdown } from "../components/Markdown";
import { useStore } from "../store/store";

/** The record without its own heading: the page gives it one. */
function withoutHeading(record: string): string {
  return record.replace(/^#\s+(Job details|Ombro's record)\s*\n+/, "").trim();
}

export function BriefPage() {
  const { brief, record, show, discussBrief, detailsFrom } = useStore();
  const back = detailsFrom === "chat" ? "Back to the chat" : "Back to Jobs";
  return (
    <div className="page report-page">
      <div className="column wide-page">
        <p>
          <button className="primary back" onClick={() => show(detailsFrom)}>
            <ArrowLeft size={16} /> {back}
          </button>
        </p>
        {brief ? (
          <>
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
            <div className="report-card">
              <div className="report-head">
                <span>
                  <b>Job details</b>
                </span>
              </div>
              <div className="report-body">
                {record ? (
                  <Markdown className="record" text={withoutHeading(record)} />
                ) : (
                  <p className="muted">The job's details are written when it ends.</p>
                )}
              </div>
            </div>
          </>
        ) : (
          <p className="muted">Opening the job's details…</p>
        )}
      </div>
    </div>
  );
}
