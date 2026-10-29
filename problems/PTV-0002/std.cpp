// std.cpp —— 正解：long long 求阶乘
#include <cstdio>

int main() {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        int n;
        if (scanf("%d", &n) != 1) return 1;
        long long f = 1;
        for (int i = 2; i <= n; ++i) f *= i;
        printf("%lld\n", f);
    }
    return 0;
}
