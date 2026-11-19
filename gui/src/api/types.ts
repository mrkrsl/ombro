// Generated from src/api/schema.json by npm run types. Do not edit.

/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "CoreEvent".
 */
export type CoreEvent =
  | ChatCleared
  | Failure
  | JobEnded
  | JobProgress
  | JobStarted
  | LeadSaid
  | ModeChanged
  | Note
  | OpenPage
  | PastTurn
  | Progress
  | ProposalShown
  | Usage;
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ServerMessage".
 */
export type ServerMessage =
  | EventMessage
  | QuestionMessage
  | QuestionClosedMessage
  | EditFilesMessage
  | SessionMessage
  | JobMessage
  | CountsMessage;

export interface OmbroApi {
  api: Api;
  core_event:
    | ChatCleared
    | Failure
    | JobEnded
    | JobProgress
    | JobStarted
    | LeadSaid
    | ModeChanged
    | Note
    | OpenPage
    | PastTurn
    | Progress
    | ProposalShown
    | Usage;
  field: Field;
  form: Form;
}
/**
 * Every type the app uses, gathered so one schema holds them all.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Api".
 */
export interface Api {
  answer_reply: AnswerReply;
  approval: ApprovalRow;
  approval_detail: ApprovalDetail;
  brief: Brief;
  browser: BrowserView;
  chat: ChatRow;
  chat_detail: ChatDetail;
  command: CommandView;
  crews: Crews;
  folders: FolderList;
  input_reply: InputReply;
  job: JobRow;
  job_status: JobStatus;
  keys: KeyList;
  models: ModelList;
  preferences: Preferences;
  report: Report;
  role: RoleDetail;
  schedule: ScheduleRow;
  scheduler: SchedulerState;
  server_message:
    | EventMessage
    | QuestionMessage
    | QuestionClosedMessage
    | EditFilesMessage
    | SessionMessage
    | JobMessage
    | CountsMessage;
  session: SessionView;
  since_last: SinceLast;
  spec: SpecRow;
  subscribe: SubscribeMessage;
  summary: Summary;
  tools: ToolList;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "AnswerReply".
 */
export interface AnswerReply {
  ok: boolean;
  problem: string | null;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ApprovalRow".
 */
export interface ApprovalRow {
  arguments: unknown;
  class: string;
  crews: string[];
  edited: boolean;
  job_id: string;
  job_name: string;
  long_ref: string;
  reason: string;
  ref: string;
  role: string;
  status: string;
  summary: string;
  tool: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ApprovalDetail".
 */
export interface ApprovalDetail {
  arguments: unknown;
  class: string;
  crews: string[];
  edited: boolean;
  job_id: string;
  job_name: string;
  long_ref: string;
  reason: string;
  ref: string;
  role: string;
  skill: {
    [k: string]: unknown;
  } | null;
  status: string;
  summary: string;
  tool: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Brief".
 */
export interface Brief {
  active: boolean;
  brief: string;
  chat: string | null;
  cleared_at: string | null;
  created_at: string | null;
  crews: string[];
  finished_at: string | null;
  job_id: string;
  limit_hit: string | null;
  live: boolean;
  name: string;
  path: string;
  seen_at: string | null;
  source: string;
  spec: string;
  started_at: string | null;
  status: string | null;
  total_tokens: number;
  waiting: number;
}
/**
 * Settings › General › Browser: the choice, and what each would be.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "BrowserView".
 */
export interface BrowserView {
  choice: string;
  endpoint: string | null;
  installed: InstalledBrowser[];
  problem: string | null;
  using: string | null;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "InstalledBrowser".
 */
export interface InstalledBrowser {
  name: string;
  path: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ChatRow".
 */
export interface ChatRow {
  id: string | null;
  jobs: string[];
  name: string;
  path: string;
  title: string;
  updated_at: string;
  when: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ChatDetail".
 */
export interface ChatDetail {
  id: string | null;
  jobs: string[];
  name: string;
  path: string;
  title: string;
  turns: ChatTurn[];
  updated_at: string;
  when: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ChatTurn".
 */
export interface ChatTurn {
  text: string;
  who: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "CommandView".
 */
export interface CommandView {
  help: string;
  name: string;
  screen: string;
  where: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Crews".
 */
export interface Crews {
  crews: CrewRow[];
  default_crew: string;
  lead: LeadView;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "CrewRow".
 */
export interface CrewRow {
  default: boolean;
  description: string;
  name: string;
  roles: RoleRow[];
  title: string;
}
/**
 * A role in a crew. One that can't be loaded has its name, title and problem only.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "RoleRow".
 */
export interface RoleRow {
  description?: string;
  kind?: string;
  model?: string;
  name: string;
  problem?: string;
  skills?: string[];
  title: string;
  tools?: string[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "LeadView".
 */
export interface LeadView {
  model: string | null;
  name: string;
  path: string;
  problem?: string;
  title: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "FolderList".
 */
export interface FolderList {
  declined: string[];
  file: string;
  folders: FolderRow[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "FolderRow".
 */
export interface FolderRow {
  access: string;
  backup: string | null;
  name: string;
  path: string;
  problem: string | null;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "InputReply".
 */
export interface InputReply {
  handled?: boolean;
  route?: Route;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Route".
 */
export interface Route {
  args: string;
  command: string;
  screen: string;
  where: "screen" | "app";
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobRow".
 */
export interface JobRow {
  active: boolean;
  brief: string;
  chat: string | null;
  cleared_at: string | null;
  created_at: string | null;
  crews: string[];
  finished_at: string | null;
  job_id: string;
  limit_hit: string | null;
  live: boolean;
  name: string;
  path: string;
  seen_at: string | null;
  source: string;
  started_at: string | null;
  status: string | null;
  total_tokens: number;
  waiting: number;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobStatus".
 */
export interface JobStatus {
  active: boolean;
  allowance: string;
  brief: string;
  chat: string | null;
  cleared_at: string | null;
  created_at: string | null;
  crews: string[];
  finished_at: string | null;
  folders: unknown[];
  interview_usage: {
    [k: string]: unknown;
  };
  job_id: string;
  limit_hit: string | null;
  limits: {
    [k: string]: unknown;
  };
  live: boolean;
  name: string;
  outbox: {
    [k: string]: number;
  };
  outward: {
    [k: string]: unknown;
  };
  path: string;
  pid: number | null;
  seen_at: string | null;
  source: string;
  started_at: string | null;
  status: string | null;
  tool_names: string[];
  total_tokens: number;
  usage: {
    [k: string]: unknown;
  };
  waiting: number;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "KeyList".
 */
export interface KeyList {
  file: string;
  keys: KeyRow[];
  sign_ins: SignIn[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "KeyRow".
 */
export interface KeyRow {
  name: string;
  set: boolean;
  source: string | null;
  used_by: string[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "SignIn".
 */
export interface SignIn {
  server: string;
  status: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ModelList".
 */
export interface ModelList {
  chatgpt: ChatGPTPlan;
  connections: Connection[];
  default: string | null;
  presets: ProviderPreset[];
}
/**
 * Signing in with ChatGPT, so model calls use the person's ChatGPT plan (no key).
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ChatGPTPlan".
 */
export interface ChatGPTPlan {
  email: string | null;
  problem: string | null;
  signing_in: boolean;
  state: string;
  usage_url: string;
  welcome: string | null;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Connection".
 */
export interface Connection {
  api: string;
  endpoint: string | null;
  key: string | null;
  label: string;
  models: ModelEntry[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ModelEntry".
 */
export interface ModelEntry {
  default: boolean;
  model: string;
  name: string;
  used_by: string[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ProviderPreset".
 */
export interface ProviderPreset {
  endpoint: string;
  key: string;
  label: string;
  model: string | null;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Preferences".
 */
export interface Preferences {
  background_feed: boolean;
  default_crew: string;
  default_crew_title: string;
  editor: string;
  files: {
    [k: string]: string;
  };
  instructions: string;
  instructions_set: boolean;
  name: string;
  notifications: boolean;
  outward: string;
  theme: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Report".
 */
export interface Report {
  active: boolean;
  brief: string;
  chat: string | null;
  cleared_at: string | null;
  created_at: string | null;
  crews: string[];
  deliverables: Deliverable[];
  finished_at: string | null;
  job_id: string;
  limit_hit: string | null;
  live: boolean;
  name: string;
  path: string;
  report: string | null;
  report_path: string;
  seen_at: string | null;
  source: string;
  started_at: string | null;
  status: string | null;
  total_tokens: number;
  waiting: number;
  workspace: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Deliverable".
 */
export interface Deliverable {
  exists: boolean;
  kind: string;
  name: string;
  path: string;
  role: string;
  text?: string;
  url: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "RoleDetail".
 */
export interface RoleDetail {
  all_skills: boolean;
  crew: string;
  description: string;
  kind: string;
  model: string;
  name: string;
  path: string;
  skills: string[];
  soul: string;
  title: string;
  tools: string[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ScheduleRow".
 */
export interface ScheduleRow {
  crews: string[];
  cron: string;
  enabled: boolean;
  last_job: LastJob | null;
  name: string;
  next: string | null;
  path: string;
  problem: string | null;
  spec: string;
  spec_title: string;
  when: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "LastJob".
 */
export interface LastJob {
  due: string | null;
  job_id: string;
  status: string | null;
  waiting: number;
}
/**
 * Whether scheduled jobs can run: the scheduler service (scheduler.check).
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "SchedulerState".
 */
export interface SchedulerState {
  can_install: boolean;
  message: string;
  ok: boolean;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "EventMessage".
 */
export interface EventMessage {
  event:
    | LeadSaid
    | Progress
    | Note
    | Failure
    | ProposalShown
    | Usage
    | ModeChanged
    | PastTurn
    | ChatCleared
    | JobStarted
    | JobProgress
    | JobEnded
    | OpenPage;
  seq: number;
  session: string | null;
  type: "event";
}
/**
 * The Lead's own words. `asks` is true when it is waiting for an answer.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "LeadSaid".
 */
export interface LeadSaid {
  asks?: boolean;
  text: string;
  type: "lead";
}
/**
 * Where something stands: interview topics covered, a test call under way.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Progress".
 */
export interface Progress {
  text: string;
  type: "progress";
}
/**
 * Something Ombro says. `items` is a list shown beneath it. `spaced` starts
 * a new paragraph. `level` is info, warning or done.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Note".
 */
export interface Note {
  items?: string[];
  level?: string;
  spaced?: boolean;
  text: string;
  type: "note";
}
/**
 * Something went wrong. Shown on its own, never mistaken for the Lead.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Failure".
 */
export interface Failure {
  text: string;
  type: "error";
}
/**
 * Files an interview proposes to write, waiting for the user's decision.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ProposalShown".
 */
export interface ProposalShown {
  files: FileView[];
  type: "proposal";
  warnings?: string[];
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "FileView".
 */
export interface FileView {
  diff?: string[];
  label: string;
  path: string;
  replaces: boolean;
  text: string;
}
/**
 * Tokens used so far against the cap, for a chat or an interview.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Usage".
 */
export interface Usage {
  final?: boolean;
  max_tokens: number | null;
  mode: string;
  scope: string;
  tool_calls?: number;
  total_tokens: number;
  type: "usage";
}
/**
 * Chat moved into Brief, or back.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ModeChanged".
 */
export interface ModeChanged {
  mode: string;
  text?: string;
  type: "mode";
}
/**
 * A turn from a chat reopened to carry on, shown again: who said it, and what.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "PastTurn".
 */
export interface PastTurn {
  text: string;
  type: "past";
  who: string;
}
/**
 * /new: a fresh chat began, so an interface may clear what is on screen.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ChatCleared".
 */
export interface ChatCleared {
  type: "cleared";
}
/**
 * A job started in the background, after the user's yes.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobStarted".
 */
export interface JobStarted {
  job_id: string;
  name: string;
  text: string;
  type: "job_started";
}
/**
 * A step in a job's journal worth showing: a role briefed, a result back, an approval asked.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobProgress".
 */
export interface JobProgress {
  action: string;
  job_id: string;
  live?: boolean;
  name: string;
  note: string;
  role: string;
  text: string;
  type: "job_progress";
}
/**
 * A job has ended: how, what it made, what waits for approval. Written by Ombro, not the model.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobEnded".
 */
export interface JobEnded {
  again?: string;
  again_text?: string;
  brief?: string;
  files?: JobFile[];
  job_id: string;
  limit_hit?: string | null;
  live?: boolean;
  name: string;
  offers?: JobOffer[];
  status: string;
  summary?: string;
  text: string;
  type: "job_ended";
  waiting?: number;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobFile".
 */
export interface JobFile {
  exists: boolean;
  name: string;
  path: string;
  url: string;
}
/**
 * A way to carry on with a job that didn't finish: a button's words and the chat command it sends.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobOffer".
 */
export interface JobOffer {
  command: string;
  label: string;
}
/**
 * Ombro suggests a page of the app, such as Scheduled with a brief picked. A terminal shows the text.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "OpenPage".
 */
export interface OpenPage {
  args: string;
  screen: string;
  text: string;
  type: "open_page";
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "QuestionMessage".
 */
export interface QuestionMessage {
  field: Field;
  id: string;
  prompt: string;
  seq: number;
  session: string;
  type: "question";
}
/**
 * One question. In a form, one field of it.
 *
 * kind: text (a line, or several if `multiline`), secret (hidden, never shown
 * or saved), choice (one of `options`), yesno, or wait (only to go on).
 * `pattern` is a regular expression the answer must match in full, and
 * `invalid` what to say when it does not (or when a required answer is
 * empty). A choice with `other` also takes a free answer, described by it; with
 * `pattern` too, only a free answer that matches it.
 *
 * `aside`: a question asked in the chat that a new message may set aside. When
 * what the user sends is not an answer to it, the interface raises SetAside
 * from `ask` and passes the message to the session, which sends it to the
 * Lead once the chat has dealt with the question going unanswered.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Field".
 */
export interface Field {
  aside?: boolean;
  default?: unknown;
  help?: string;
  invalid?: string;
  kind?: string;
  label: string;
  mode?: string;
  multiline?: boolean;
  name: string;
  options?: Option[];
  other?: string;
  pattern?: string | null;
  required?: boolean;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Option".
 */
export interface Option {
  aliases?: string[];
  key: string;
  label: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "QuestionClosedMessage".
 */
export interface QuestionClosedMessage {
  id: string;
  seq: number;
  session: string;
  type: "question_closed";
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "EditFilesMessage".
 */
export interface EditFilesMessage {
  id: string;
  paths: string[];
  seq: number;
  session: string;
  type: "edit_files";
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "SessionMessage".
 */
export interface SessionMessage {
  kind: string;
  seq: number;
  session: string;
  state: "working" | "waiting" | "idle" | "ended";
  type: "session";
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "JobMessage".
 */
export interface JobMessage {
  event: JobProgress | JobEnded;
  job_id: string;
  line: string;
  name: string;
  status: string;
  type: "job";
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "CountsMessage".
 */
export interface CountsMessage {
  active: number;
  crews_ready: number;
  running: string[];
  scheduled: number;
  type: "counts";
  unseen: number;
  waiting: number;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "SessionView".
 */
export interface SessionView {
  chat: string | null;
  chat_id: string | null;
  id: string;
  jobs: string[];
  kind: string;
  mode: string | null;
  question: OpenQuestion | null;
  seq: number;
  state: string;
  title: string | null;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "OpenQuestion".
 */
export interface OpenQuestion {
  field: Field;
  id: string;
  kind: "question" | "edit_files";
  paths: string[];
  prompt: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "SinceLast".
 */
export interface SinceLast {
  finished: number;
  jobs: UnseenJob[];
  waiting: number;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "UnseenJob".
 */
export interface UnseenJob {
  brief: string;
  finished_at: string | null;
  job_id: string;
  status: string | null;
  waiting: number;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "SpecRow".
 */
export interface SpecRow {
  crews: string[];
  modified: string;
  name: string;
  path: string;
  title: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "SubscribeMessage".
 */
export interface SubscribeMessage {
  all?: boolean;
  sessions?: {
    [k: string]: number;
  };
  type?: "subscribe";
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Summary".
 */
export interface Summary {
  active_ids: string[];
  counts: Counts;
  crews: CrewRow[];
  lead: LeadView;
  model: string | null;
  name: string;
  outward: string;
  unseen: UnseenJob[];
  version: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Counts".
 */
export interface Counts {
  active_jobs: number;
  crews: number;
  crews_ready: number;
  scheduled_jobs: number;
  unseen_jobs: number;
  waiting: number;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ToolList".
 */
export interface ToolList {
  apis: ApiRow[];
  browser: string | null;
  composio: ComposioAccount | null;
  folders: string[];
  native: NativeTool[];
  sandbox: string | null;
  servers: ServerRow[];
}
/**
 * A web API: its address and what it may do. Its key by name only, and whether it is set.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ApiRow".
 */
export interface ApiRow {
  about: string;
  access: string;
  calls: number;
  how_to: string;
  key: string | null;
  key_set: boolean;
  name: string;
  send_key: string;
  url: string;
}
/**
 * The user's Composio account, once connected: its key by name only, and its toolkits.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ComposioAccount".
 */
export interface ComposioAccount {
  access: string;
  dashboard: string;
  key: string;
  key_set: boolean;
  problem: string | null;
  toolkits: ComposioToolkit[];
}
/**
 * A service connected in the user's Composio account, such as Google Docs.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ComposioToolkit".
 */
export interface ComposioToolkit {
  active: boolean;
  slug: string;
  status: string;
  title: string;
  tools: number | null;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "NativeTool".
 */
export interface NativeTool {
  calls: number | null;
  max_calls: number | null;
  provider: string | null;
  tool: string;
}
/**
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "ServerRow".
 */
export interface ServerRow {
  args: string[];
  command: string | null;
  problem: string | null;
  secrets: string[];
  server: string;
  sign_in: string | null;
  signing_in: boolean;
  url: string | null;
}
/**
 * Several fields asked together: one at a time in a terminal, on one page in a
 * graphical interface. `intro` lines are shown first.
 *
 * This interface was referenced by `OmbroApi`'s JSON-Schema
 * via the `definition` "Form".
 */
export interface Form {
  fields: Field[];
  intro?: string[];
  name: string;
  title: string;
}
