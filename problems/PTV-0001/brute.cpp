// brute.cpp —— 暴力解：递归实现，算法与 std 不同（用于对拍）
#include <cstdio>
#include <vector>

static long long solve(const std::vector<long long>& a, int i) {
    if (i == static_cast<int>(a.size())) return 0;
    return a[i] + solve(a, i + 1);
}

int main() {
    int n;
    if (scanf("%d", &n) != 1) return 1;
    std::vector<long long> a(n);
    for (int i = 0; i < n; ++i) {
        if (scanf("%lld", &a[i]) != 1) return 1;
    }
    printf("%lld\n", solve(a, 0));
    return 0;
}
