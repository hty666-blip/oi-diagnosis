export function indentedNewline(value: string, start: number, end: number, language: string) {
  const before = value.slice(0, start);
  const after = value.slice(end);
  const line = before.slice(before.lastIndexOf("\n") + 1);
  const indent = line.match(/^[\t ]*/)?.[0] ?? "";
  const existing = value.split("\n").map((part) => part.match(/^(\t+| +)\S/)?.[1]).filter(Boolean) as string[];
  const unit = existing.some((part) => part.startsWith("\t")) ? "\t"
    : " ".repeat(Math.min(...existing.map((part) => part.length), 4));
  const last = line.trimEnd().slice(-1);
  const opens = "{[(".includes(last) && last !== "";
  const deeper = opens || (language === "python3" && last === ":");
  const nextIndent = indent + (deeper ? unit : "");
  const closing = { "{": "}", "[": "]", "(": ")" }[last];
  const pair = closing && after.trimStart().startsWith(closing);
  const suffix = pair ? "\n" + indent + after.trimStart() : after;
  return { value: before + "\n" + nextIndent + suffix, cursor: before.length + 1 + nextIndent.length };
}

export function shouldSendMessage(event: { key: string; shiftKey: boolean; isComposing: boolean; keyCode: number }) {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229;
}
