import type { AccountInput } from "./api";

export function parseClassStudentCsv(text: string): AccountInput[] {
  const rows = parseAccountCsv(text);
  if (rows.some((row) => row.role !== "student" || row.class_id !== null)) {
    throw new Error("新班学生名单只允许 student，class_id 请留空；保存时自动加入新班。");
  }
  return rows;
}

// 支持带引号、逗号及双引号转义的 CSV；不将临时密码写入浏览器存储。
export function parseAccountCsv(text: string): AccountInput[] {
  const records: string[][] = [];
  let row: string[] = [], field = "", quoted = false, closed = false;
  const source = text.replace(/^\uFEFF/, "");
  for (let i = 0; i <= source.length; i++) {
    const c = source[i];
    if (quoted) {
      if (c === undefined) throw new Error("CSV 引号未闭合。");
      if (c === '"' && source[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') { quoted = false; closed = true; }
      else field += c;
    } else if (c === ',' || c === '\n' || c === '\r' || c === undefined) {
      row.push(field); field = ""; closed = false;
      if (c !== ',') {
        if (row.some((v) => v.trim())) records.push(row);
        row = [];
        if (c === '\r' && source[i + 1] === '\n') i++;
      }
    } else if (c === '"' && !field && !closed) quoted = true;
    else if (closed || c === '"') throw new Error("CSV 引号格式不正确，请使用模板填写。");
    else field += c;
  }
  const header = records.shift();
  if (header?.join(",") !== "username,display_name,role,password,class_id") throw new Error("表头需为 username,display_name,role,password,class_id。");
  if (!records.length || records.length > 100) throw new Error("每次导入 1–100 个账号。");
  return records.map((values, i) => {
    if (values.length !== 5) throw new Error(`第 ${i + 2} 行需有 5 列。`);
    const [username, display_name, role, password, group] = values;
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/.test(username.trim())) throw new Error(`第 ${i + 2} 行用户名格式无效。`);
    if (!display_name.trim() || display_name.trim().length > 64) throw new Error(`第 ${i + 2} 行姓名需为 1–64 个字符。`);
    if (password.length < 8 || password.length > 128) throw new Error(`第 ${i + 2} 行临时密码需为 8–128 个字符。`);
    if (!["student", "teacher", "admin"].includes(role.trim())) throw new Error(`第 ${i + 2} 行角色无效。`);
    if (group.trim() && (!/^\d+$/.test(group.trim()) || !Number.isSafeInteger(Number(group)) || Number(group) <= 0)) throw new Error(`第 ${i + 2} 行班级编号无效。`);
    return { username: username.trim().toLowerCase(), display_name: display_name.trim(), role: role.trim() as AccountInput["role"], password, class_id: group.trim() ? Number(group) : null };
  });
}
