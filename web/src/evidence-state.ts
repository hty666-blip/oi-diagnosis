export function executionLabel(payload: Record<string, unknown>): string {
  const errors: Record<string, string> = {
    "Time Limit Exceeded": "运行超时", "Memory Limit Exceeded": "内存超限",
    "Output Limit Exceeded": "输出超限", "Non Zero Exit Status": "运行异常退出",
    Signalled: "程序被信号终止", "Internal Error": "执行器异常，验证未完成",
  };
  if (payload.status !== "Accepted") return errors[String(payload.status)] ?? "执行状态待核对";
  if (typeof payload.exit_code === "number" && payload.exit_code !== 0) return "运行异常退出";
  if (payload.matched === true) return "输出匹配";
  if (payload.matched === false) return "输出不匹配";
  return "程序运行结束，需核对输出";
}
