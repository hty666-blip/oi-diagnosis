import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { parseAccountCsv, parseClassStudentCsv } = await sourceModule("../src/admin-import.ts");
const header = "username,display_name,role,password,class_id\r\n";

test("新班名单自动归班前仅接受新学生，拒绝混入教练或其他班级", () => {
  assert.equal(parseClassStudentCsv(header + "s1,学生,student,initial-pass,\n")[0].class_id, null);
  for (const row of ["t1,教练,teacher,initial-pass,", "s1,学生,student,initial-pass,12"]) {
    assert.throws(() => parseClassStudentCsv(header + row), /class_id/);
  }
});

test("CSV 正确处理 BOM、引号、逗号、换行与密码前后空白", () => {
  const rows = parseAccountCsv('\uFEFF' + header + 'S001,"小明,同学",student,"  my secret  ",12\r\nS002,"有""引号\n姓名",teacher,12345678,\r\n');
  assert.deepEqual(rows, [
    { username: "s001", display_name: "小明,同学", role: "student", password: "  my secret  ", class_id: 12 },
    { username: "s002", display_name: '有"引号\n姓名', role: "teacher", password: "12345678", class_id: null },
  ]);
});

test("错误表头、缺列、无效角色、越界编号和未闭合引号明确拒绝", () => {
  for (const source of ["wrong,header\n", header, header + "x,y,student,password", header + "x,y,root,password,", header + "x,y,student,password,9007199254740992", header + 'x,"bad,student,password,', header + 'x,"bad"x,student,password,']) {
    assert.throws(() => parseAccountCsv(source));
  }
  assert.throws(() => parseAccountCsv(header + "x,y,student,password,\n".repeat(101)), /100/);
});
