// std.cpp —— 正解：long long 累加
#include <cstdio>

int main() {
    int n;
    if (scanf("%d", &n) != 1) return 1;
    long long sum = 0;
    for (int i = 0; i < n; ++i) {
        long long x;
        if (scanf("%lld", &x) != 1) return 1;
        sum += x;
    }
    printf("%lld\n", sum);
    return 0;
}
