// validator.cpp —— 纯 C++ 校验器（零依赖，不依赖 testlib）
// 通过: 退出码 0；失败: 退出码 1 并打印原因
#include <cstdio>

int main() {
    int n;
    if (scanf("%d", &n) != 1) { puts("bad n: not an integer"); return 1; }
    if (n < 1 || n > 100000) { printf("n out of range: %d\n", n); return 1; }

    for (int i = 0; i < n; ++i) {
        long long x;
        if (scanf("%lld", &x) != 1) { printf("bad a[%d]\n", i); return 1; }
        if (x < -1000000000LL || x > 1000000000LL) { printf("a[%d] out of range: %lld\n", i, x); return 1; }
    }

    // 不允许有多余内容
    char extra;
    if (scanf(" %c", &extra) == 1) { puts("trailing content"); return 1; }

    return 0;
}
