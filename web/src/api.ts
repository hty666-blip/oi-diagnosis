const BASE = "/api/v1";
const TOKEN_KEY = "oi_token";
const USER_KEY = "oi_user";

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: "student" | "teacher" | "admin";
  class_name: string;
}

export function readLocal(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

export function writeLocal(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* 禁用存储时仍允许继续编辑。 */ }
}

export function getToken(): string {
  return readLocal(TOKEN_KEY) ?? "";
}

export function setSession(token: string, user: AuthUser) {
  writeLocal(TOKEN_KEY, token);
  writeLocal(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch { /* 草稿不随退出删除。 */ }
}

export function cachedUser(): AuthUser | null {
  try {
    const user = JSON.parse(readLocal(USER_KEY) ?? "null") as AuthUser | null;
    return user && typeof user.id === "number" && ["student", "teacher", "admin"].includes(user.role)
      ? user : null;
  } catch { return null; }
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作未完成，请重试。";
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  return { ...(token ? { Authorization: "Bearer " + token } : {}), ...extra };
}

async function checkResponse(res: Response, login = false): Promise<Response> {
  if (res.status === 401) {
    if (login) throw new ApiError("用户名或密码错误，请检查后重试。", 401);
    clearSession();
    window.dispatchEvent(new Event("oi-session-expired"));
    throw new ApiError("登录已过期，请重新登录；本机代码草稿已保留。", 401);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        detail = body.detail.map((item: { msg?: string }) => item.msg ?? "").filter(Boolean).join("；");
      }
    } catch { /* 不把服务器 HTML 或内部错误堆栈显示给学生。 */ }
    throw new ApiError(detail || ({
      403: "没有权限进行此操作。",
      404: "记录不存在，或当前账号无权访问。",
      429: "操作过于频繁，请稍后重试。",
      503: "判题服务暂时不可用，代码已保留，请稍后再次提交。",
    } as Record<number, string>)[res.status] || "服务暂时无法完成请求，请重试（" + res.status + "）。", res.status);
  }
  return res;
}

async function request(url: string, body?: unknown, method = "GET"): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(BASE + url, {
      method,
      headers: authHeaders(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("无法连接服务，请检查网络和后端是否启动；当前输入已保留。");
  }
  return checkResponse(response, url === "/auth/login");
}

async function get<T>(url: string): Promise<T> {
  return (await request(url)).json() as Promise<T>;
}

async function post<T>(url: string, body?: unknown): Promise<T> {
  return (await request(url, body, "POST")).json() as Promise<T>;
}

export interface Problem {
  pid: string;
  title: string;
  difficulty: string;
  tags: string[];
  samples: number;
  package_level: string;
  source: string;
}

export interface ProblemDetail {
  pid: string;
  title: string;
  difficulty: string;
  tags: string[];
  samples: { input: string; output: string }[];
  statement_md: string;
  source: string;
  source_url: string;
  package_level: string;
  time_limit_ms: number;
}

export interface NatureTag {
  p: string;
  code: string;
  line?: number;
  col?: number;
  message?: string;
  hint?: string;
}

export interface TestSummary {
  passed: number;
  total: number;
  tested?: number;
  scope?: "samples" | "package";
  suite_digest?: string;
}

export interface SubmissionResult {
  id: number;
  parent_submission_id?: number | null;
  stage: string;
  verdict: string | null;
  tests_passed: TestSummary | null;
  problem_pid?: string;
  problem_title?: string;
  code?: string;
  diagnosis?: {
    narrative: string;
    status: string;
    route?: string;
    nature: NatureTag[];
    mechanism: DiagResult["mechanism"];
    degraded?: string[];
    review_revision?: number;
    reviewer_id?: number | null;
    reviewed_at?: string | null;
  } | null;
}

export interface DiagResult {
  submission_id: number;
  verdict: string | null;
  status: string;
  route: string;
  confidence?: number;
  narrative: string;
  first_hint_level?: number;
  nature: NatureTag[];
  mechanism: { code: string; confidence: number; evidence_ids?: number[]; explain?: string }[];
  degraded?: string[];
  review_revision?: number;
  reviewer_id?: number | null;
  reviewed_at?: string | null;
}

export interface EvidenceItem {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  digest: string;
  reproducible?: boolean;
}

export interface SessionReply {
  session_id: number;
  status: string;
  level: number;
  action: string;
  reply: string;
  verified_submission_id?: number;
  verdict?: string | null;
}

export interface SessionHistory {
  session_id: number;
  status: string;
  level: number;
  turns: { role: "tutor" | "student"; level: number; content: string }[];
}

export interface MySubmission {
  id: number;
  parent_submission_id?: number | null;
  verdict: string | null;
  created_at: string;
  narrative: string;
  route: string | null;
  p: string | null;
  problem_pid?: string;
  problem_title?: string;
  stage?: string;
  tests_passed?: TestSummary | null;
  diagnosis_status?: string | null;
  review_revision?: number;
  reviewer_id?: number | null;
}

export interface CoachItem {
  diagnosis_id: number;
  submission_id: number;
  verdict: string | null;
  confidence: number;
  narrative: string;
  route: string | null;
  problem_pid?: string;
  problem_title?: string;
  student_name?: string;
  review_revision?: number;
  tests_passed?: TestSummary | null;
}

export type ReviewDecision = "confirm" | "revise" | "return";

export interface ReviewEntry {
  id: number;
  submission_id: number;
  revision: number;
  actor: { id: number; name: string };
  decision: ReviewDecision;
  narrative: string;
  evidence_ids: number[];
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  created_at: string;
}

export interface ReviewHistory {
  items: ReviewEntry[];
  current_revision: number;
}

export interface ReviewInput {
  submission_id: number;
  expected_revision: number;
  decision: ReviewDecision;
  narrative: string;
  evidence_ids: number[];
}

export interface LineageItem {
  id: number;
  parent_submission_id: number | null;
  code: string;
  verdict: string | null;
  stage: string;
  tests_passed: TestSummary | null;
  created_at: string;
}

export interface Overview {
  submissions: number;
  diagnoses: number;
  pending_review: number;
  verdict: Record<string, number>;
  route: Record<string, number>;
}

export const api = {
  demoAccounts: () => get<{ username: string; password: string; role: string; name: string }[]>("/auth/demo-accounts"),
  login: (username: string, password: string) =>
    post<{ access_token: string; user: AuthUser }>("/auth/login", { username, password }),
  me: () => get<AuthUser>("/auth/me"),
  problems: () => get<Problem[]>("/problems?limit=200"),
  getProblem: (pid: string) => get<ProblemDetail>("/problems/" + encodeURIComponent(pid)),
  submit: (pid: string, code: string, parentId?: number | null) =>
    post<SubmissionResult>("/submissions", {
      problem_pid: pid, code, ...(parentId == null ? {} : { parent_submission_id: parentId }),
    }),
  getSubmission: (id: number) => get<SubmissionResult>("/submissions/" + id),
  evidence: (id: number) => get<EvidenceItem[]>("/submissions/" + id + "/evidence"),
  lineage: (id: number) => get<LineageItem[]>("/submissions/" + id + "/lineage"),
  reviews: (id: number) => get<ReviewHistory>("/submissions/" + id + "/reviews"),
  review: (body: ReviewInput) => post<{
    review: ReviewEntry;
    diagnosis: { review_revision: number; status: string; narrative: string; reviewer_id: number; reviewed_at: string };
  }>("/coach/reviews", body),
  diagnose: (id: number) => post<DiagResult>("/submissions/" + id + "/diagnose"),
  createSession: (submissionId: number) =>
    post<SessionReply>("/sessions", { submission_id: submissionId }),
  getSession: (id: number) => get<SessionHistory>("/sessions/" + id),
  sendMessage: (sessionId: number, content: string) =>
    post<SessionReply>("/sessions/" + sessionId + "/messages", { content }),
  verifySession: (sessionId: number, submissionId: number) =>
    post<SessionReply>("/sessions/" + sessionId + "/verify", { submission_id: submissionId }),
  mySubmissions: () => get<MySubmission[]>("/me/submissions"),
  coachQueue: () => get<CoachItem[]>("/coach/queue"),
  coachOverview: () => get<Overview>("/coach/overview"),
  downloadCard: async (id: number) => {
    const response = await request("/submissions/" + id + "/card");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "错题卡-" + id + ".md";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
