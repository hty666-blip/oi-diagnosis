// brute.cpp —— 递归实现，算法与 std 不同（用于对拍）
#include <cstdio>

static long long fact(int n) {
    if (n <= 1) return 1;
    return n * fact(n - 1);
}

int main() {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        int n;
        if (scanf("%d", &n) != 1) return 1;
        printf("%lld\n", fact(n));
    }
    return 0;
}
