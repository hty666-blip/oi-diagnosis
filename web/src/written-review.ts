import type { WrittenReviewInput } from "./api";

// 响应丢失后的相同操作沿用请求编号；修改内容或动作后开始新请求。
export function createWrittenReviewIntent(uuid: () => string = () => crypto.randomUUID()) {
  let previousKey = "", previousId = "";
  return (input: Omit<WrittenReviewInput, "request_id">): WrittenReviewInput => {
    const key = JSON.stringify(input);
    if (key !== previousKey) { previousKey = key; previousId = uuid(); }
    return { ...input, request_id: previousId };
  };
}
