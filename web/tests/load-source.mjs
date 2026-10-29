import { readFile } from "node:fs/promises";
import ts from "typescript";

// 使用项目已有 TypeScript 转译器，不引入测试运行时依赖。
export async function sourceModule(relative) {
  const source = await readFile(new URL(relative, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  });
  return import("data:text/javascript;base64," + Buffer.from(outputText).toString("base64"));
}
