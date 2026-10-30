import type { TestSummary } from "./api";

export function resultState(verdict: string | null, tests?: TestSummary | null): { label: string; tone: string } {
  if (verdict === "UNVERIFIED") return { label: "历史结果待核对", tone: "warn" };
  if (verdict === "AC") {
    const verified = tests?.scope === "package" && Number.isInteger(tests.total)
      && tests.total > 0 && tests.passed === tests.total;
    return verified ? { label: "题包测试通过", tone: "ok" }
      : { label: "历史通过 · 范围待核对", tone: "warn" };
  }
  const errors: Record<string, string> = {
    CE: "编译未通过", WA: "输出不符合预期", RE: "运行出错",
    TLE: "运行超时", MLE: "内存超限", OLE: "输出超限",
  };
  if (verdict && errors[verdict]) return { label: errors[verdict], tone: "bad" };
  return { label: verdict === "SAMPLE_PASS" ? "样例通过 · 待完整验证"
    : verdict === "NO_TESTS" ? "暂无测试 · 未验证" : "尚未完成验证", tone: "warn" };
}
