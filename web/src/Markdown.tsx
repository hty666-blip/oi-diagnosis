import { type ReactNode } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";

/**
 * 极简 Markdown 渲染：只覆盖题面里出现的语法。
 * 不引入 react-markdown，原因：题面结构稳定，轻量解析更可控，
 * 且不会因为依赖升级导致构建失败（本项目已经踩过 esbuild 的坑）。
 */
function math(source: string, key: string, displayMode = false): ReactNode {
  const html = katex.renderToString(source.trim(), {
    displayMode,
    output: "htmlAndMathml",
    throwOnError: false,
    strict: "ignore",
    trust: false,
  });

  return (
    <span
      key={key}
      className={displayMode ? "math-display-inner" : "math-inline"}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\$(?!\$)[^$\n]+\$)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={`${key}-b${i}`}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) {
      out.push(<code key={`${key}-c${i}`} className="inline-code">{tok.slice(1, -1)}</code>);
    } else {
      out.push(math(tok.slice(1, -1), `${key}-m${i}`));
    }
    last = m.index + tok.length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const lines = (text || "").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let code: string[] | null = null;
  let displayMath: string[] | null = null;
  let list: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (para.length) {
      blocks.push(<p key={`p${key++}`}>{inline(para.join(" "), `p${key}`)}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`ul${key++}`}>
          {list.map((it, idx) => (
            <li key={idx}>{inline(it, `li${key}-${idx}`)}</li>
          ))}
        </ul>
      );
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");

    if (code) {
      if (line.trim().startsWith("```")) {
        blocks.push(
          <pre key={`pre${key++}`} className="code-block">
            <code>{code.join("\n")}</code>
          </pre>
        );
        code = null;
      } else {
        code.push(raw);
      }
      continue;
    }

    if (displayMath) {
      if (line.trim() === "$$") {
        blocks.push(
          <div key={`math${key++}`} className="math-display">
            {math(displayMath.join("\n"), `math-inner${key}`, true)}
          </div>
        );
        displayMath = null;
      } else {
        displayMath.push(raw);
      }
      continue;
    }

    if (line.trim().startsWith("```")) {
      flushPara();
      flushList();
      code = [];
      continue;
    }

    const singleLineMath = /^\s*\$\$(.+)\$\$\s*$/.exec(line);
    if (singleLineMath) {
      flushPara();
      flushList();
      blocks.push(
        <div key={`math${key++}`} className="math-display">
          {math(singleLineMath[1], `math-inner${key}`, true)}
        </div>
      );
      continue;
    }

    if (line.trim() === "$$") {
      flushPara();
      flushList();
      displayMath = [];
      continue;
    }

    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }

    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      flushList();
      const lvl = h[1].length;
      const content = inline(h[2], `h${key}`);
      if (lvl <= 2) blocks.push(<h3 key={`h${key++}`}>{content}</h3>);
      else blocks.push(<h4 key={`h${key++}`}>{content}</h4>);
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      flushPara();
      list.push(line.replace(/^\s*[-*]\s+/, ""));
      continue;
    }

    const ol = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
    if (ol) {
      flushPara();
      list.push(`${ol[1]}. ${ol[2]}`);
      continue;
    }

    para.push(line);
  }

  if (code) {
    blocks.push(
      <pre key={`pre${key++}`} className="code-block">
        <code>{code.join("\n")}</code>
      </pre>
    );
  }
  if (displayMath) {
    blocks.push(
      <div key={`math${key++}`} className="math-display">
        {math(displayMath.join("\n"), `math-inner${key}`, true)}
      </div>
    );
  }
  flushPara();
  flushList();

  return <div className="md">{blocks}</div>;
}
