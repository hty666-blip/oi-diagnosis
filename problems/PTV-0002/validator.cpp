// validator.cpp —— 纯 C++ 校验器（零依赖）
#include <cstdio>

int main() {
    int T;
    if (scanf("%d", &T) != 1) { puts("bad T"); return 1; }
    if (T < 1 || T > 1000) { printf("T out of range: %d\n", T); return 1; }
    for (int i = 0; i < T; ++i) {
        int n;
        if (scanf("%d", &n) != 1) { printf("bad n at %d\n", i); return 1; }
        if (n < 1 || n > 20) { printf("n out of range: %d\n", n); return 1; }
    }
    char extra;
    if (scanf(" %c", &extra) == 1) { puts("trailing content"); return 1; }
    return 0;
}
